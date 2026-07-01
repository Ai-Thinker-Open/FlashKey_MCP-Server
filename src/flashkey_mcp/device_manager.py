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
from flashkey_mcp.commands import CMD_HELLO

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
        logger.debug("PING keepalive resumed")

    def require_authed(self) -> None:
        """Raise ``RuntimeError`` with a Chinese i18n message if not authed."""
        with self._lock:
            state = self._state
            error = self._last_error

        if state is DeviceState.AUTHED:
            return

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

            {"authed": bool, "version": str, "boot": 0|1, "rst": 0|1,
             "v5v": 0|1, "v3v3": 0|1}
        """
        if not self.authed:
            return {
                "authed": False, "version": "", "boot": 0,
                "rst": 0, "v5v": 0, "v3v3": 0,
            }

        fk = self._fk
        if fk is None:
            return {
                "authed": False, "version": "", "boot": 0,
                "rst": 0, "v5v": 0, "v3v3": 0,
            }

        try:
            version = fk.commands.get_version()
            pin_status = fk.commands.get_status()
            return {
                "authed": True,
                "version": version.get("version", ""),
                "boot": pin_status.get("boot", 0),
                "rst": pin_status.get("rst", 0),
                "v5v": pin_status.get("v5v", 0),
                "v3v3": pin_status.get("v3v3", 0),
            }
        except Exception as exc:
            logger.warning("get_status failed: %s", exc)
            return {
                "authed": False, "version": "", "boot": 0,
                "rst": 0, "v5v": 0, "v3v3": 0,
            }

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
        while not self._stop_event.is_set():
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
                logger.info("Handshake succeeded — device authenticated")
                return
        except Exception as exc:
            logger.warning("Handshake error: %s", exc)

        # -- Failure -------------------------------------------------------
        self._last_error = ERR_HANDSHAKE_TIMEOUT
        self._transition_to_disconnected(ERR_HANDSHAKE_TIMEOUT)

    # ------------------------------------------------------------------
    # AUTHED → PING keepalive
    # ------------------------------------------------------------------

    def _ping_keepalive(self) -> None:
        """Send PING every 2 s.  After _PING_MAX_FAILS consecutive failures, disconnect."""
        fail_count = 0
        while not self._stop_event.is_set():
            fk = self._fk
            with self._lock:
                state = self._state

            if state is not DeviceState.AUTHED or fk is None:
                return  # state changed externally

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
