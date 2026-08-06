"""FlashKey FK-01 device lifecycle manager.

Background thread that monitors USB for device hotplug, automatically
performs HELLO-based Challenge-Response handshake, and maintains a
PING keepalive to prevent firmware heartbeat timeout.

Architecture
------------
A single background thread runs the main loop which drives a simple
state machine::

    DISCONNECTED -> CONNECTING -> AUTHED
         ^              |            |
         |              v            |
         +---- (timeout/fail)  (PING lost)

    AUTHED -> IDLE  (serial port released after an idle timeout; the
                     firmware heartbeat pauses together with the port)
    IDLE   -> DISCONNECTED  (woken by the next tool call, which triggers
                             re-detection + full re-handshake)

Device discovery on Linux uses an inotify watcher thread (stdlib, zero
extra dependencies) to react to ``/dev/ttyACM*`` creation/removal
without polling.  On Windows or if inotify fails, a lightweight
``find_port()`` poll every 1 s is used as a fallback.
"""

from __future__ import annotations

import logging
import os
import platform
import select
import threading
import time
from enum import Enum, auto

from flashkey_mcp import FlashKey, find_port
from flashkey_mcp.protocol import FrameParser
from flashkey_mcp.commands import CMD_EVT_BUTTON, CMD_EVT_MODULE_DATA, CMD_HELLO
from flashkey_mcp.events import EventRecorder
from flashkey_mcp.modules import ModuleRegistry
from flashkey_mcp.singleton import acquire as acquire_instance_lock
from flashkey_mcp.singleton import release as release_instance_lock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How long to wait for a HELLO frame from firmware (seconds)
_HELLO_TIMEOUT: float = 3.0
# How long the active handshake may take (seconds)
_HANDSHAKE_TIMEOUT: float = 2.0
# Total: 3 + 2 = 5 s (NFR-1)
_TOTAL_HANDSHAKE_BUDGET: float = _HELLO_TIMEOUT + _HANDSHAKE_TIMEOUT

# PING keepalive interval (NFR-2)
_PING_INTERVAL: float = 2.0
# Consecutive PING failures before declaring device lost.
# Set higher to tolerate USB bus saturation during flash operations.
_PING_MAX_FAILS: int = 10

# Poll interval in seconds when inotify is unavailable (Windows / fallback)
_FALLBACK_POLL_INTERVAL: float = 1.0

# Extension-module manifest poll interval while AUTHED
_MODULE_POLL_INTERVAL: float = 10.0

# 空闲释放串口：最后一次工具调用后超过该秒数无活动则关闭串口。
# 固件心跳（PING）随端口一起暂停；下次工具调用自动重连并重新握手。
# 设为 0 可禁用空闲释放（一直保持连接，旧行为）。
_IDLE_TIMEOUT_S: float = float(os.environ.get("FLASHKEY_IDLE_TIMEOUT", "30"))

# require_authed() 从 IDLE 唤醒后等待"重新检测 + 握手"完成的超时
_WAKE_TIMEOUT_S: float = _TOTAL_HANDSHAKE_BUDGET + 3.0

# ---------------------------------------------------------------------------
# Error messages (需求 2.5)
# ---------------------------------------------------------------------------

ERR_NO_DEVICE = "未检测到 FlashKey FK-01，请插入设备"
ERR_PERMISSION = "权限不足，请执行 sudo usermod -aG dialout $USER 后重新登录"
ERR_BUSY = "FK-01 被其他程序占用，请关闭后重试"
ERR_HANDSHAKE_TIMEOUT = "FK-01 握手超时，请拔出重新插入后重试"
ERR_AUTH_FAIL = "认证失败，可能固件密钥不匹配"
ERR_DISCONNECTED = "设备已断开，请重新插入"

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class DeviceState(Enum):
    """FK-01 connection lifecycle states."""

    DISCONNECTED = auto()  # no device detected
    CONNECTING = auto()  # device found, attempting handshake
    AUTHED = auto()  # fully authenticated, PING keepalive active
    IDLE = auto()  # port released after idle timeout; reconnects on demand


class DeviceManager:
    """Manages FK-01 device lifecycle in a background thread.

    Usage::

        dm = DeviceManager()
        dm.start()          # launch background monitor thread
        ...
        dm.require_authed() # raise RuntimeError with i18n message if not authed
        dm.fk.commands.boot_set(True)  # use the command interface directly
        dm.stop()           # clean shutdown
    """

    def __init__(self) -> None:
        # -- state (protected by _lock) --
        self._state: DeviceState = DeviceState.DISCONNECTED
        self._fk: FlashKey | None = None
        self._last_error: str = ""
        self._lock: threading.RLock = threading.RLock()

        # -- control --
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._pause_keepalive: bool = False  # suppress PING during flash ops
        self._last_activity: float = 0.0  # last tool-driven activity (monotonic)
        self._recorder = EventRecorder()
        self._module_registry: ModuleRegistry | None = None

        # -- inotify (Linux) --
        self._inotify_fd: int = -1
        self._inotify_wd: int = -1
        self._inotify_wake_r: int = -1  # self-pipe read end for stopping select
        self._inotify_wake_w: int = -1

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def authed(self) -> bool:
        return self._state is DeviceState.AUTHED

    @property
    def connected(self) -> bool:
        return self._state in (DeviceState.CONNECTING, DeviceState.AUTHED)

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def fk(self) -> FlashKey | None:
        """The open device handle, or *None* if not connected."""
        return self._fk

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the background monitor thread.

        Idempotent — safe to call multiple times.
        """
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="fk-device-monitor"
        )
        self._monitor_thread.start()

    def stop(self) -> None:
        """Signal the background thread to exit and close the device."""
        self._stop_event.set()
        self._wake_monitor()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=3.0)
        self._close_device()
        self._cleanup_inotify()
        # 释放进程级单实例锁，允许其他 flashkey-mcp 实例接管设备
        release_instance_lock()

    # ------------------------------------------------------------------
    # Guard for MCP tools
    # ------------------------------------------------------------------

    def pause_keepalive(self) -> None:
        """Suppress PING keepalive (call before long flash operations)."""
        self._pause_keepalive = True
        logger.debug("PING keepalive paused")

    def resume_keepalive(self) -> None:
        """Resume PING keepalive after flash completes."""
        self._pause_keepalive = False
        self.mark_active()  # 长操作结束视为一次活动，空闲计时重新开始
        logger.debug("PING keepalive resumed")

    def mark_active(self) -> None:
        """Record tool-driven device activity (resets the idle-release timer)."""
        with self._lock:
            self._last_activity = time.monotonic()

    def require_authed(self) -> None:
        """Raise ``RuntimeError`` with a Chinese i18n message if not authed.

        If the port was released by the idle timeout, this wakes the monitor
        thread and waits for re-detection + handshake before returning, so
        tool calls always see a ready (heartbeat-running) session.
        """
        with self._lock:
            state = self._state
            error = self._last_error

        if state is DeviceState.AUTHED:
            self.mark_active()
            return

        if state is DeviceState.IDLE:
            # 空闲释放后的第一次工具调用：回到 DISCONNECTED 并唤醒监控线程，
            # 由它执行完整的"检测 → 打开串口 → HELLO 握手 → 恢复心跳"流程。
            with self._lock:
                self._state = DeviceState.DISCONNECTED
                self._last_error = ""
            self._wake_monitor()
            deadline = time.monotonic() + _WAKE_TIMEOUT_S
            while time.monotonic() < deadline and not self._stop_event.is_set():
                time.sleep(0.05)
                with self._lock:
                    state = self._state
                    error = self._last_error
                if state is DeviceState.AUTHED:
                    self.mark_active()
                    return
                if state is DeviceState.DISCONNECTED and error:
                    break
            if state is DeviceState.AUTHED:
                self.mark_active()
                return
            if error:
                raise RuntimeError(error)
            raise RuntimeError("FK-01 重新连接超时，请稍候重试")

        if state is DeviceState.CONNECTING:
            raise RuntimeError("FK-01 正在连接中，请稍候重试")

        # DISCONNECTED
        if error:
            raise RuntimeError(error)
        raise RuntimeError(ERR_NO_DEVICE)

    # ------------------------------------------------------------------
    # flashkey_status (no auth required)
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return unified status dict.  Always callable — no auth needed.

        Returns::

            {"authed": bool, "idle": bool, "version": str, "boot": 0|1, "rst": 0|1,
             "v5v": 0|1, "v3v3": 0|1, "vusb": 0|1,
             "module": {"present": bool, "type": str|None, "fw": str|None}}

        ``idle=True`` means the serial port was released by the idle timeout
        (heartbeat paused); the next tool call reconnects automatically.
        """
        module = self.get_module_info()
        module_field = {
            "present": bool(module.get("present")),
            "type": None,
            "fw": None,
        }
        if module.get("module"):
            module_field["type"] = module["module"].get("name")
            module_field["fw"] = (
                module["module"].get("version")
                or module["module"].get("fw")
                or module["module"].get("firmware")
            )

        if not self.authed:
            return {
                "authed": False,
                "idle": self._state is DeviceState.IDLE,
                "version": "", "boot": 0,
                "rst": 0, "v5v": 0, "v3v3": 0, "vusb": 0,
                "module": module_field,
            }

        fk = self._fk
        if fk is None:
            return {
                "authed": False, "idle": False, "version": "", "boot": 0,
                "rst": 0, "v5v": 0, "v3v3": 0, "vusb": 0,
                "module": module_field,
            }

        try:
            self.mark_active()  # 状态查询是设备活动，重置空闲计时
            version = fk.commands.get_version()
            pin_status = fk.commands.get_status()
            return {
                "authed": True, "idle": False,
                "version": version.get("version", ""),
                "boot": pin_status.get("boot", 0),
                "rst": pin_status.get("rst", 0),
                "v5v": pin_status.get("v5v", 0),
                "v3v3": pin_status.get("v3v3", 0),
                "vusb": pin_status.get("vusb", 0),
                "module": module_field,
            }
        except Exception as exc:
            logger.warning("get_status failed: %s", exc)
            return {
                "authed": False, "idle": False, "version": "", "boot": 0,
                "rst": 0, "v5v": 0, "v3v3": 0, "vusb": 0,
                "module": module_field,
            }

    def get_recent_events(self, limit: int = 20) -> list[dict]:
        """Return recently recorded device events (newest first)."""
        return self._recorder.recent(limit)

    def set_module_registry(self, registry: ModuleRegistry) -> None:
        """Attach the dynamic-tool module registry (wired by the server)."""
        self._module_registry = registry

    def get_module_info(self) -> dict:
        """Return cached module info from the registry (no IO)."""
        if self._module_registry is None:
            return {"present": False, "module": None, "tools": [], "last_error": ""}
        return self._module_registry.info()

    def _drain_events(self) -> None:
        """Consume queued device→host event frames and record them."""
        fk = self._fk
        if fk is None:
            return
        while True:
            try:
                cmd, data = fk.transport.event_queue.get_nowait()
            except Exception:
                break
            try:
                if cmd == CMD_EVT_BUTTON:
                    self._recorder.record_button_event(data)
                elif cmd == CMD_EVT_MODULE_DATA:
                    if self._module_registry is not None:
                        self._module_registry.on_module_data(data)
                    else:
                        logger.debug("Module data ignored (no registry): %s", data.hex())
                else:
                    logger.debug(
                        "Ignored unsolicited frame 0x%02X: %s", cmd, data.hex()
                    )
            except Exception as exc:
                logger.warning("Event handling failed: %s", exc)

    # ==================================================================
    # Background monitor loop
    # ==================================================================

    def _monitor_loop(self) -> None:
        """Single background thread — drives the state machine forever."""
        self._init_inotify()

        while not self._stop_event.is_set():
            with self._lock:
                state = self._state

            if state is DeviceState.DISCONNECTED:
                self._wait_for_device()
            elif state is DeviceState.CONNECTING:
                self._do_handshake()
            elif state is DeviceState.AUTHED:
                self._ping_keepalive()
            elif state is DeviceState.IDLE:
                self._wait_idle_wake()
            else:
                time.sleep(0.1)

        self._cleanup_inotify()

    # ------------------------------------------------------------------
    # DISCONNECTED → wait for device
    # ------------------------------------------------------------------

    def _wait_for_device(self) -> None:
        """Block until FK-01 is detected on a USB serial port.

        On Linux with inotify available this is event-driven; on Windows
        or as a fallback we use a short-interval poll.
        """
        logger.info("Waiting for FK-01 device...")
        lock_logged = False
        while not self._stop_event.is_set():
            # 单实例锁：防止多个 flashkey-mcp 进程同时打开 FK-01 串口互抢
            if not acquire_instance_lock():
                if not lock_logged:
                    logger.warning(
                        "检测到其他 flashkey-mcp 实例占用 FK-01 设备，"
                        "等待其退出后接管..."
                    )
                    lock_logged = True
                self._sleep_or_watch(_FALLBACK_POLL_INTERVAL)
                continue
            lock_logged = False

            info = find_port()
            if info is not None:
                try:
                    fk = FlashKey(port=info["port"], timeout=0.1)
                    with self._lock:
                        self._fk = fk
                        self._last_error = ""
                        self._state = DeviceState.CONNECTING
                    logger.info(
                        "FK-01 detected on %s (%s %s)",
                        info["port"], info.get("vendor", ""), info.get("model", ""),
                    )
                    return
                except (OSError, IOError) as exc:
                    err_lower = str(exc).lower()
                    if "permission" in err_lower or "denied" in err_lower:
                        self._last_error = ERR_PERMISSION
                    elif "busy" in err_lower or "resource" in err_lower:
                        self._last_error = ERR_BUSY
                    else:
                        self._last_error = f"无法打开设备: {exc}"
                    logger.warning("FK-01 open failed: %s", exc)

            # Wait for next trigger
            self._sleep_or_watch(_FALLBACK_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # CONNECTING → handshake
    # ------------------------------------------------------------------

    def _do_handshake(self) -> None:
        """Perform HELLO-based Challenge-Response handshake.

        Phase 1 (3 s): listen for firmware HELLO frame
        Phase 2 (2 s): active CHALLENGE → RESPONSE handshake

        On success → AUTHED.  On failure → DISCONNECTED.
        """
        fk = self._fk
        if fk is None:
            self._transition_to_disconnected(ERR_NO_DEVICE)
            return

        logger.info("Starting HELLO handshake...")

        # -- Phase 1: wait for HELLO frame --------------------------------
        parser = FrameParser()
        try:
            fk.transport.reset_input_buffer()
        except Exception:
            pass

        hello_seen = False
        deadline = time.monotonic() + _HELLO_TIMEOUT

        while time.monotonic() < deadline:
            try:
                byte_data = fk.transport.read(1)
            except Exception:
                break
            if byte_data:
                result = parser.feed(byte_data[0])
                if result is not None:
                    cmd, _data = result
                    if cmd == CMD_HELLO:
                        hello_seen = True
                        logger.info("HELLO frame received")
                        break

        # -- Phase 2: active handshake -------------------------------------
        try:
            if fk.commands.handshake():
                with self._lock:
                    self._state = DeviceState.AUTHED
                    self._last_error = ""
                self.mark_active()  # 握手完成视为一次活动：连接后有完整的空闲窗口
                logger.info("Handshake succeeded — device authenticated")
                # 处理握手期间到达的缓存事件（固件认证完成后补传按键事件）
                self._drain_events()
                return
        except Exception as exc:
            logger.warning("Handshake error: %s", exc)

        # -- Failure -------------------------------------------------------
        self._last_error = ERR_HANDSHAKE_TIMEOUT
        self._transition_to_disconnected(ERR_HANDSHAKE_TIMEOUT)

    # ------------------------------------------------------------------
    # AUTHED → PING keepalive / idle release
    # ------------------------------------------------------------------

    def _check_idle_release(self) -> bool:
        """Release the serial port if it has been idle long enough.

        Called from the AUTHED keepalive loop.  Returns True if the port
        was released (state moved to IDLE).  While a long operation
        (flash/log) has keepalive paused, the port is never released.
        """
        if _IDLE_TIMEOUT_S <= 0:
            return False
        with self._lock:
            if self._state is not DeviceState.AUTHED or self._fk is None:
                return False
            if self._pause_keepalive:
                return False  # 长操作（烧录/日志采集）期间不释放
            if time.monotonic() - self._last_activity < _IDLE_TIMEOUT_S:
                return False
        self._enter_idle()
        return True

    def _enter_idle(self) -> None:
        """Close the FK-01 port and move to IDLE (heartbeat paused)."""
        with self._lock:
            if self._fk is not None:
                try:
                    self._fk.close()
                except Exception:
                    pass
                self._fk = None
            self._state = DeviceState.IDLE
            self._last_error = ""
        logger.info(
            "No tool activity for %.0fs — FK-01 port released (idle). "
            "Next tool call will reconnect and re-handshake.",
            _IDLE_TIMEOUT_S,
        )

    def _wait_idle_wake(self) -> None:
        """IDLE: port closed, wait for a tool call or shutdown."""
        while not self._stop_event.is_set():
            with self._lock:
                if self._state is not DeviceState.IDLE:
                    return
            self._sleep_or_watch(0.5)

    def _ping_keepalive(self) -> None:
        """Send PING every 2 s.  After _PING_MAX_FAILS consecutive failures, disconnect."""
        fail_count = 0
        last_module_poll = 0.0
        while not self._stop_event.is_set():
            fk = self._fk
            with self._lock:
                state = self._state

            if state is not DeviceState.AUTHED or fk is None:
                return  # state changed externally

            # 空闲超时 → 释放串口（心跳随端口暂停，下次调用重新握手）
            if self._check_idle_release():
                return

            self._drain_events()

            # 扩展模块清单轮询（10s 一次，仅 AUTHED 时）
            if (
                self._module_registry is not None
                and time.monotonic() - last_module_poll >= _MODULE_POLL_INTERVAL
            ):
                last_module_poll = time.monotonic()
                self._poll_module()

            try:
                fk.commands.ping(read_timeout=1.0)
                fail_count = 0
            except Exception as exc:
                fail_count += 1
                logger.warning(
                    "PING keepalive fail %d/%d: %s",
                    fail_count, _PING_MAX_FAILS, exc,
                )
                if fail_count >= _PING_MAX_FAILS:
                    logger.warning("Keepalive lost — disconnecting")
                    self._transition_to_disconnected(ERR_DISCONNECTED)
                    return

            self._sleep_or_watch(_PING_INTERVAL)

    def _poll_module(self) -> None:
        """Query the module manifest and sync dynamic tools."""
        fk = self._fk
        registry = self._module_registry
        if fk is None or registry is None:
            return
        try:
            raw = fk.commands.module_get_info(read_timeout=2.0)
            registry.update(raw)
        except TimeoutError:
            # 固件无模块支持或总线忙：保留上一份状态，避免误删工具
            logger.debug("Module manifest poll timed out — keeping previous state")
        except Exception as exc:
            logger.warning("Module manifest poll failed: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition_to_disconnected(self, error_msg: str) -> None:
        """Close the device and move to DISCONNECTED state."""
        with self._lock:
            self._last_error = error_msg
            self._state = DeviceState.DISCONNECTED
            if self._fk is not None:
                try:
                    self._fk.close()
                except Exception:
                    pass
                self._fk = None
        logger.info("Device disconnected: %s", error_msg)

    def _close_device(self) -> None:
        """Close the serial port if open (lock-free — called from stop)."""
        fk = self._fk
        if fk is not None:
            try:
                fk.close()
            except Exception:
                pass
            self._fk = None

    # ==================================================================
    # inotify (Linux only, zero extra dependencies)
    # ==================================================================

    # Linux inotify constants
    _IN_ACCESS = 0x00000001
    _IN_MODIFY = 0x00000002
    _IN_CREATE = 0x00000100
    _IN_DELETE = 0x00000200
    _IN_CLOSE_WRITE = 0x00000008

    def _init_inotify(self) -> None:
        """Set up inotify watch on ``/dev`` for ttyACM/ttyUSB changes.

        No-op on non-Linux platforms or if the inotify syscall fails.
        """
        if not _is_linux():
            return

        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6", use_errno=True)

            # inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
            IN_NONBLOCK = 0o4000
            IN_CLOEXEC = 0o2000000
            fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
            if fd < 0:
                logger.debug("inotify_init1 failed (errno=%d)", ctypes.get_errno())
                return

            # inotify_add_watch(fd, "/dev", IN_CREATE | IN_DELETE | IN_CLOSE_WRITE)
            watch_mask = self._IN_CREATE | self._IN_DELETE | self._IN_CLOSE_WRITE
            path = b"/dev\0"
            wd = libc.inotify_add_watch(fd, path, watch_mask)
            if wd < 0:
                logger.debug("inotify_add_watch /dev failed")
                os.close(fd)
                return

            # Self-pipe for waking select() on stop
            r, w = os.pipe()
            self._inotify_fd = fd
            self._inotify_wd = wd
            self._inotify_wake_r = r
            self._inotify_wake_w = w
            logger.debug("inotify watching /dev (fd=%d, wd=%d)", fd, wd)
        except Exception as exc:
            logger.debug("inotify setup failed: %s", exc)

    def _cleanup_inotify(self) -> None:
        """Tear down inotify resources."""
        if self._inotify_wake_w >= 0:
            os.close(self._inotify_wake_w)
            self._inotify_wake_w = -1
        if self._inotify_wake_r >= 0:
            os.close(self._inotify_wake_r)
            self._inotify_wake_r = -1
        if self._inotify_fd >= 0:
            os.close(self._inotify_fd)
            self._inotify_fd = -1

    def _wake_monitor(self) -> None:
        """Write a byte to the self-pipe so select() returns."""
        w = self._inotify_wake_w
        if w >= 0:
            try:
                os.write(w, b"x")
            except Exception:
                pass

    def _sleep_or_watch(self, timeout: float) -> None:
        """Sleep for *timeout* seconds, but wake early on inotify events.

        On Linux with inotify active this uses ``select()`` so the thread
        is woken immediately when ``/dev/`` changes — no polling.
        On Windows or when inotify is unavailable this is a plain sleep.
        """
        fd = self._inotify_fd
        r = self._inotify_wake_r
        if fd < 0 or r < 0:
            # No inotify — plain sleep, but check stop_event every 200 ms
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._stop_event.is_set():
                    return
                time.sleep(min(0.2, timeout))
            return

        try:
            # Drain any stale inotify events
            _drain_inotify(fd)

            # select on inotify fd + wake pipe, with timeout
            rlist, _, _ = select.select([fd, r], [], [], timeout)
            if rlist:
                # If woken via self-pipe, drain the byte
                if r in rlist:
                    try:
                        os.read(r, 1)
                    except Exception:
                        pass
                # Drain inotify events
                if fd in rlist:
                    _drain_inotify(fd)
        except Exception:
            # select() failed — fall back to plain sleep
            time.sleep(timeout)


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _drain_inotify(fd: int) -> None:
    """Read and discard pending inotify events."""
    try:
        while True:
            data = os.read(fd, 4096)
            if not data or len(data) < 16:
                break
    except (BlockingIOError, OSError):
        pass
