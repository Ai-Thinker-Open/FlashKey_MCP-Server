"""FlashKey FK-01 MCP Server — SSE (default) or stdio transport.

Usage::

    flashkey-mcp                    # SSE mode (default), on :8100
    flashkey-mcp --port 8200        # SSE on custom port
    flashkey-mcp --stdio            # stdio mode (legacy, one process per session)

SSE is the default so that one long-lived process can serve every AI
session (multiple clients share the single FK-01 device without
serial-port preemption).  On startup the server launches
:class:`DeviceManager` which immediately begins scanning for FK-01,
performs the HELLO handshake on detection, and maintains a PING
keepalive.  By the time the AI makes its first tool call, FK-01 may
already be authenticated and ready.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from flashkey_mcp.transport import list_all_ports, FLASHKEY_VID, FLASHKEY_PID
from flashkey_mcp.device_manager import DeviceManager
from flashkey_mcp.modules import ModuleRegistry, module_timeout_ms
from flashkey_mcp import firmware_tools
from flashkey_mcp import guide as _guide
from flashkey_mcp.errors import E, FlashkeyError, map_require_authed_error

logger = logging.getLogger(__name__)

# ── Port validation ───────────────────────────────────────────────────


def _validate_flash_port(port: str) -> None:
    """Raise ``ToolError`` if *port* is the FK-01 control port.

    FK-01 has two ports identified by VID/PID, not device name:
      - ``fk_control`` (VID=1A86, PID=FE0D) → FK-01 main controller, MCP only.
      - ``fk_log``     (VID=1A86, PID=8010) → WCH-LinkE VCP on v0.1.1, log/flash use this.

    Always use ``list_ports()`` and match by ``role`` field.
    """
    import serial.tools.list_ports as _list_ports
    for p in _list_ports.comports():
        if p.device == port:
            if p.vid == FLASHKEY_VID and p.pid == FLASHKEY_PID:
                # Find the correct flash port to suggest
                flash_ports = [
                    pp.device for pp in _list_ports.comports()
                    if pp.vid == 0x1A86 and pp.pid == 0x8010
                ]
                hint = ""
                if flash_ports:
                    hint = f" 请改用日志/烧录端口: {', '.join(flash_ports)}"
                raise FlashkeyError(
                    E.PORT_WRONG_ROLE,
                    f"{port} 是 FK-01 主控端口 (role=fk_control, MCP 内部专用)，"
                    f"不能用于烧录或日志。{hint}",
                    hint="请用 list_ports() 按 role 字段选择端口",
                    recovery_tool="list_ports",
                )
            return  # port found, not FK-01 control — OK
    # Port not found in system — let the actual serial open fail naturally


_FK_LOG_MAX_BAUD = 921600


def _validate_baud_for_port(port: str, baud_rate: int) -> None:
    """FlashKey 自带串口 (role=fk_log) 最高仅支持 921600，更高需外接 USB-UART。"""
    if baud_rate <= _FK_LOG_MAX_BAUD:
        return
    import serial.tools.list_ports as _list_ports

    for p in _list_ports.comports():
        if p.device == port and p.vid == 0x1A86 and p.pid == 0x8010:
            raise FlashkeyError(
                E.INVALID_ARG,
                f"{port} 是 FlashKey 自带串口 (role=fk_log)，最高仅支持 921600，"
                f"不能使用 {baud_rate}。",
                hint="把 baud_rate 降到 ≤921600，或改用外接 USB-UART 串口",
            )


# ── Singleton device manager ─────────────────────────────────────────
_dm: DeviceManager | None = None
_module_registry = ModuleRegistry()
# Flash/log mutual exclusion lock (per serial port)
_flash_lock = threading.Lock()
_flash_active_port: str = ""

# ── log_open / log_close session state ──────────────
_LOG_MAX_LINES = 10_000
_LOG_FILE = Path(tempfile.gettempdir()) / "flashkey-log.txt"
_LOG_HISTORY_DIR = Path(
    os.environ.get("FLASHKEY_LOG_HISTORY_DIR", str(Path.home() / "flashkey-logs"))
)
_LOG_HISTORY_MAX = max(1, int(os.environ.get("FLASHKEY_LOG_HISTORY_MAX", "10")))
_PROJECT_SAFE_RE = re.compile(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+")


def _sanitize_project(project: str) -> str:
    """把项目名清洗成安全目录名（允许中文/字母/数字/_-，去掉路径分隔符）。"""
    cleaned = _PROJECT_SAFE_RE.sub("_", project or "").strip("_")
    return cleaned or "default"


_log_session_lock = threading.RLock()
_log_session: dict[str, Any] = {
    "open": False,
    "port": "",
    "baud_rate": 115200,
    "project": "default",
    "serial": None,
    "thread": None,
    "stop_event": None,
    "started_at": 0.0,
    "ended_at": 0.0,
    "lines": deque(maxlen=_LOG_MAX_LINES),
    "bytes": 0,
    "error": "",
}


def _get_dm() -> DeviceManager:
    """Return the global DeviceManager, creating and starting it on first access.

    Started at MCP server launch so FK-01 discovery and handshake happen
    before the AI's first tool call.
    """
    global _dm
    if _dm is None:
        _dm = DeviceManager()
        _module_registry.attach(mcp)
        _dm.set_module_registry(_module_registry)
        _module_registry.set_io_handler(_module_io_forward)
        _dm.start()
        logger.info("DeviceManager started (state: %s)", _dm.state.name)
    return _dm


# ======================================================================
# Error wrapper — returns isError for unauthenticated tools
# ======================================================================

def _accepts_context(fn: Any) -> bool:
    """Return True when *fn* declares a ``context`` parameter."""
    import inspect as _inspect

    try:
        return "context" in _inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _tool_wrapper(fn: Any, require_auth: bool = True) -> Any:
    """Wrap a tool function with common error handling.

    ``require_auth=True`` tools call ``DeviceManager.require_authed()``
    before the tool body.  All failures are normalized to
    :class:`FlashkeyError` with a stable error ``code``, a ``hint`` for
    the next step, and a ``retryable`` flag, so agents can decide
    whether to retry, recover, or stop.

    The returned wrapper is async so blocking tools can yield to the
    event loop.  FastMCP injects a :class:`Context` object into the
    ``context`` parameter (excluded from the tool schema); it is
    forwarded only to wrapped functions that declare it, enabling
    server→client progress notifications.
    """
    import functools
    import inspect as _inspect

    @functools.wraps(fn)
    async def wrapper(context: Context | None = None, *args: Any, **kwargs: Any) -> dict:
        if require_auth:
            try:
                _get_dm().require_authed()
            except FlashkeyError:
                raise
            except RuntimeError as exc:
                raise map_require_authed_error(exc) from exc
            except Exception as exc:
                raise FlashkeyError(
                    E.AUTH_REQUIRED, f"认证检查失败: {exc}",
                    hint="先完成密钥认证（SET_KEY / flashkey_auth 流程）后重试",
                ) from exc
        try:
            call_kwargs = dict(kwargs)
            if _accepts_context(fn):
                call_kwargs["context"] = context
            result = fn(*args, **call_kwargs)
            if _inspect.isawaitable(result):
                result = await result
            return result
        except FlashkeyError:
            raise
        except TimeoutError as exc:
            raise FlashkeyError(
                E.TIMEOUT, str(exc),
                hint="重试一次；若持续超时请检查设备/模组连接与波特率",
                retryable=True,
            ) from exc
        except ToolError as exc:
            raise FlashkeyError(
                E.INTERNAL, str(exc),
                hint="重试；若仍失败请查看 flashkey-mcp 服务日志",
                retryable=True,
            ) from exc
        except Exception as exc:
            raise FlashkeyError(
                E.INTERNAL, f"{type(exc).__name__}: {exc}",
                hint="重试；若仍失败请查看 flashkey-mcp 服务日志",
                retryable=True,
            ) from exc

    return wrapper


# ======================================================================
# Server→client progress notifications
# ======================================================================

PROGRESS_HEARTBEAT_S = 1.0


def _discard_progress_future(fut: Any) -> None:
    """Best-effort: swallow any error from a background progress send."""
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc:
        logger.debug("progress notification failed: %s", exc)


class _Progress:
    """Thread-safe, monotonically non-decreasing server→client progress.

    ``stage`` is used from worker threads to snap to a stage milestone;
    ``heartbeat`` is used by the async heartbeat task to interpolate
    smoothly.  Without a client-supplied ``progressToken`` all sends no-op.
    """

    def __init__(self, context: Context | None, loop: asyncio.AbstractEventLoop) -> None:
        self._context = context
        self._loop = loop
        self._last = 0.0
        self._lock = threading.Lock()

    def _report(self, pct: float, message: str) -> None:
        if self._context is None:
            return
        try:
            coro = self._context.report_progress(pct, total=100.0, message=message)
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            fut.add_done_callback(_discard_progress_future)
        except Exception as exc:
            logger.debug("progress notification skipped: %s", exc)

    def stage(self, pct: float, message: str) -> None:
        with self._lock:
            self._last = max(self._last, pct)
            pct = self._last
        self._report(pct, message)

    def heartbeat(self, pct: float, message: str) -> None:
        with self._lock:
            if pct < self._last:
                return
            self._last = pct
        self._report(pct, message)


async def _progress_heartbeat(
    progress: _Progress,
    start_pct: float,
    end_pct: float,
    expected_s: float,
    label: str,
) -> None:
    """Interpolate progress from *start_pct* toward *end_pct* over time."""
    started = time.monotonic()
    while True:
        await asyncio.sleep(PROGRESS_HEARTBEAT_S)
        elapsed = time.monotonic() - started
        frac = min(elapsed / expected_s, 1.0)
        progress.heartbeat(
            start_pct + (end_pct - start_pct) * frac,
            f"{label}（已运行 {int(elapsed)}s）",
        )


def _require_fk():
    """Return the FlashKey device handle or raise ToolError."""
    dm = _get_dm()
    fk = dm.fk
    if fk is None:
        raise FlashkeyError(
            E.DEVICE_NOT_FOUND,
            "设备未连接，请插入 FlashKey FK-01",
            hint="插入 FK-01 并等待握手；WSL 下先确认 usbip 已挂载",
            retryable=True,
            recovery_tool="status / list_ports",
        )
    return dm, fk


def _module_io_forward(payload: bytes) -> dict:
    """Forward serialized tool args to the module and collect the 0x63 window."""
    _, fk = _require_fk()
    return fk.commands.module_io(payload, window_ms=module_timeout_ms())


def _tool_module_info() -> dict:
    """Query extension-module presence, manifest and registered mod_* tools."""
    return _get_dm().get_module_info()


# ======================================================================
# Tool implementations  (NO DeviceManager parameter in signatures!)
# ======================================================================

# ── status (NEW, no auth required) ──────────────────────────

def _tool_status() -> dict:
    """Get unified device status — always callable, no auth needed."""
    return _get_dm().get_status()


# ── list_ports (NEW, no auth required) ──────────────────────

def _tool_list_ports() -> dict:
    """List all available serial ports on the system."""
    return {"ports": list_all_ports()}

# ── recover (NEW, no auth required) ─────────────────────────

def _usbipd_reattach_fk() -> list[str]:
    """Best-effort WSL usbipd re-attach of FK-01 / WCH-LinkE USB devices.

    Returns a list of human-readable notes about what was done.
    No-op when usbipd.exe is not available (non-WSL host).
    """
    import shutil
    import subprocess

    exe = shutil.which("usbipd.exe")
    if not exe:
        return ["usbipd.exe 不可用（非 WSL 环境），跳过自动重挂载"]

    notes: list[str] = []
    try:
        out = subprocess.run([exe, "list"], capture_output=True, text=True, timeout=15)
        lines = (out.stdout or "").splitlines()
    except Exception as exc:
        return [f"usbipd list 失败: {exc}"]

    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        if "Attached" in line:
            continue
        busid, vidpid = parts[0], parts[1]
        if vidpid not in ("1a86:fe0d", "1a86:8010"):
            continue
        try:
            r = subprocess.run(
                [exe, "attach", "--wsl", "--busid", busid],
                capture_output=True, text=True, timeout=30,
            )
            notes.append(
                f"usbipd attach {busid} ({vidpid}): "
                + ((r.stdout or r.stderr or "").strip() or f"exit={r.returncode}")
            )
        except Exception as exc:
            notes.append(f"usbipd attach {busid} 失败: {exc}")

    return notes or ["未发现需要重挂载的 FK-01/WCH-LinkE 设备"]


def _tool_recover(reattach: bool = False) -> dict:
    """One-stop recovery: optional USB re-attach + forced re-handshake."""
    dm = _get_dm()
    notes: list[str] = []
    if reattach:
        notes.extend(_usbipd_reattach_fk())
    try:
        result = dm.recover()
    except Exception as exc:
        raise FlashkeyError(
            E.HANDSHAKE_FAILED, f"恢复握手失败: {exc}",
            hint="检查 USB 链路后重试 recover",
            retryable=True,
            recovery_tool="recover",
        ) from exc
    status = dm.get_status()
    hints: list[str] = []
    if not result.get("connected"):
        hints.append(
            "设备未就绪：确认 FK-01 已插入；WSL 下先 usbip attach，"
            "再调 recover(reattach=True)"
        )
    elif not result.get("authed"):
        hints.append("设备已连接但未认证：完成密钥认证后重试")
    hints.extend(notes)
    return {
        "ok": bool(result.get("connected") and result.get("authed")),
        "connected": result.get("connected", False),
        "authed": result.get("authed", False),
        "error": result.get("error", ""),
        "status": status,
        "hints": hints,
    }



# ── ping ────────────────────────────────────────────────────

def _tool_ping() -> dict:
    _, fk = _require_fk()
    return fk.commands.ping()


# ── auth_status (DEPRECATED) ────────────────────────────────

def _tool_auth_status() -> dict:
    _, fk = _require_fk()
    result = fk.commands.auth_status()
    result["_deprecated"] = "请使用 status() 代替"
    return result


# ── GPIO tools ───────────────────────────────────────────────────────

def _tool_boot_set(value: bool) -> dict:
    _, fk = _require_fk()
    fk.commands.boot_set(value)
    return {"result": "ok"}


def _tool_boot_get() -> dict:
    _, fk = _require_fk()
    return {"value": fk.commands.boot_get()}


def _tool_rst_set(value: bool) -> dict:
    _, fk = _require_fk()
    fk.commands.rst_set(value)
    return {"result": "ok"}


def _tool_rst_get() -> dict:
    _, fk = _require_fk()
    return {"value": fk.commands.rst_get()}


def _tool_rst_pulse(ms: int = 50) -> dict:
    _, fk = _require_fk()
    fk.commands.rst_pulse(ms)
    return {"result": "ok"}


def _tool_v5v_set(value: bool) -> dict:
    _, fk = _require_fk()
    fk.commands.v5v_set(value)
    return {"result": "ok"}


def _tool_v5v_get() -> dict:
    _, fk = _require_fk()
    return {"value": fk.commands.v5v_get()}


def _tool_vusb_set(value: bool) -> dict:
    _, fk = _require_fk()
    fk.commands.vusb_set(value)
    return {"result": "ok"}


def _tool_vusb_get() -> dict:
    _, fk = _require_fk()
    return {"value": fk.commands.vusb_get()}


def _tool_v3v3_set(value: bool) -> dict:
    _, fk = _require_fk()
    fk.commands.v3v3_set(value)
    return {"result": "ok"}


def _tool_v3v3_get() -> dict:
    _, fk = _require_fk()
    return {"value": fk.commands.v3v3_get()}


def _tool_get_version() -> dict:
    _, fk = _require_fk()
    return fk.commands.get_version()


def _tool_get_uid() -> dict:
    _, fk = _require_fk()
    return {"uid": fk.commands.get_uid()}

# ── get_events (v0.1.1) ─────────────────────────────────────

def _tool_get_events(limit: int = 20) -> dict:
    """Return recorded device events (e.g. manual PB8/PB9 button operations)."""
    dm = _get_dm()
    count = max(1, min(int(limit), 100))
    events = dm.get_recent_events(count)
    return {"count": len(events), "events": events}


# ── get_status (DEPRECATED — use status) ──────────

def _tool_get_status() -> dict:
    _, fk = _require_fk()
    result = fk.commands.get_status()
    result["authed"] = 1
    result["_deprecated"] = "请使用 status() 代替"
    return result


def _tool_enter_bootloader() -> dict:
    _, fk = _require_fk()
    fk.commands.boot_set(True)
    fk.commands.rst_pulse()
    return {"result": "ok"}


# ======================================================================
# flash (NEW) — 需求三
# ======================================================================

# Register cleanup hook for process kill during flash
_flash_cleanup_needed = False
_flash_cleanup_dm: DeviceManager | None = None


def _flash_atexit_cleanup() -> None:
    """Emergency recovery: if the MCP process dies mid-flash, reset target."""
    global _flash_cleanup_needed
    if not _flash_cleanup_needed:
        return
    dm = _flash_cleanup_dm
    if dm is None or dm.fk is None:
        return
    try:
        logger.warning("atexit: emergency target recovery (RST pulse + BOOT low)")
        dm.fk.commands.rst_pulse(50)
        dm.fk.commands.boot_set(False)
    except Exception:
        pass


atexit.register(_flash_atexit_cleanup)


# Break mode: RST 脉冲不依赖解析工具提示文本。启动烧录工具后，
# 检测到复位提示（快路径）或经过该延时（兜底），先到者触发一次 RST 脉冲。
_BREAK_RST_DELAY_S = 2.0


def _flash_break_mode(
    fk: Any,
    flash_cmd: list[str],
    flash_dir: str,
    flash_timeout: int = 120,
    progress_cb: Any = None,
) -> tuple[bool, list[str]]:
    """BL602 serial break mode: run flash tool → one RST pulse → wait.

    The flash tool (bflb_iot_tool) sends a sync pattern on the flash port TX, then
    waits for the target reset.  FK-01 pulses its RST pin to reset the BL602 —
    the boot ROM detects the sync pattern at reset and enters bootloader.
    No BOOT pin manipulation needed.

    The RST pulse is triggered **without relying on parsing the tool's prompt
    text** (which may be buffered, localized, or written to stderr): it fires
    as soon as a reset prompt is detected, or after a short fixed delay
    (``_BREAK_RST_DELAY_S``), whichever comes first.

    Sequence:
    1. Start ``make flash`` (Popen), monitor stdout/stderr
    2. Wait for reset prompt or fallback delay
    3. Pulse FK-01 RST once → BL602 resets, boot ROM enters bootloader
    4. Wait for flash tool to complete handshake and write
    5. Recovery: RST pulse to boot normally

    Returns:
        ``(success, output_lines)``.
    """
    import threading as _threading

    def _emit(pct: float, message: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(pct, message)
        except Exception as exc:
            logger.debug("progress_cb failed: %s", exc)

    # Ensure FK-01 GPIOs don't conflict with the flash port DTR/RTS control.
    # BOOT low = default, the serial bridge handles reset signalling via RTS.
    fk.commands.boot_set(False)

    proc = None
    output_lines: list[str] = []

    try:
        _emit(10, "启动烧录工具（等待复位）")
        proc = subprocess.Popen(
            flash_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=flash_dir if flash_dir else None,
        )

        prompt_seen = _threading.Event()

        def _read_stream(stream):
            try:
                for line in iter(stream.readline, ""):
                    if line:
                        output_lines.append(line.rstrip("\r\n"))
                        lower = line.lower()
                        if any(
                            kw in lower
                            for kw in ("reset", "rest", "press", "uart", "复位", "please", "gpio8")
                        ):
                            prompt_seen.set()
            except Exception:
                pass

        out_reader = _threading.Thread(target=_read_stream, args=(proc.stdout,), daemon=True)
        err_reader = _threading.Thread(target=_read_stream, args=(proc.stderr,), daemon=True)
        out_reader.start()
        err_reader.start()

        # Prompt detection is only a fast path — never a hard requirement.
        # Wait for the prompt, the fallback delay, or an early tool exit.
        rst_deadline = time.monotonic() + _BREAK_RST_DELAY_S
        while proc.poll() is None and time.monotonic() < rst_deadline:
            if prompt_seen.wait(timeout=min(0.1, rst_deadline - time.monotonic())):
                break
        if proc.poll() is not None:
            # Tool exited before we could reset — no point pulsing RST.
            out_reader.join(timeout=2)
            err_reader.join(timeout=2)
            success = proc.returncode == 0
            if not success:
                output_lines.append("[错误] 烧录工具提前退出（未触发 RST 复位）")
            return success, output_lines
        logger.info("Break mode: pulsing FK-01 RST (prompt=%s)", prompt_seen.is_set())
        _emit(45, "触发一次 RST 脉冲（进入烧录）")
        fk.commands.rst_pulse(50)
        output_lines.append("[FlashKey] RST 脉冲已发出")
        _emit(50, "固件写入中…")

        # Wait for flash tool to finish
        try:
            proc.wait(timeout=flash_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out_reader.join(timeout=2)
            err_reader.join(timeout=2)
            _emit(90, f"烧录超时 ({flash_timeout} 秒)")
            return False, output_lines + [f"[错误] 烧录超时 ({flash_timeout} 秒)"]

        out_reader.join(timeout=3)
        err_reader.join(timeout=3)

        success = proc.returncode == 0
        return success, output_lines

    except Exception as exc:
        logger.exception("Break mode internal error: %s", exc)
        return False, output_lines + [f"[错误] 烧录异常: {exc}"]


# ── Chip → default mode ──────────────────────────────────────────────

_FLASH_DEFAULT_MODE: dict[str, str] = {
    # BL602: 默认串口打断（make flash）；固件不支持打断或擦除后改用 ISP（make eflash）。
    # BL616/BL618: BOOT+RST first, then flash tool.
    "bl602": "break",
    "bl616": "isp",
    "bl618": "isp",
}


async def _tool_flash(
    firmware_path: str,
    flash_port: str,
    context: Context | None = None,
    chip: str = "ai-m62",
    baud_rate: int = 921600,
    tool: str = "",
    flash_dir: str = "",
    mode: str = "",
) -> dict:
    """Single-call flash workflow.

    Two modes are supported:

    **break** (default for BL602) — serial break / 串口打断:
        Run flash tool → trigger one RST pulse (prompt detection or short
        delay) → wait for completion → recovery.
        BL602 只烧 App，不烧 boot2；无法触发时改用 ISP。

    **isp** (default for BL616/BL618; BL602 传 mode="isp") — make eflash:
        BOOT↑ → RST pulse → run flash tool → RST → BOOT↓.
        BL602 的 ISP 模式全量烧录（含 boot2），`make erase_flash` 擦除后必须用它。

    FK-01 handles BOOT/RST timing.  The actual firmware write is delegated
    to an external tool::

        BL602 break: ``make -C <flash_dir> flash p=<port> b=<baud>``
        BL602 isp:   ``make -C <flash_dir> eflash p=<port> b=<baud>``
        BL616:  ``make -C <flash_dir> flash CHIP=bl616 COMX=<port> BAUDRATE=<baud_rate>``
        BL618:  same as BL616 with CHIP=bl618

    This is a **blocking** call for the client: depending on firmware
    size, it may take 10–120 seconds.  Progress notifications are sent
    via the injected ``context`` when the client supplies a
    ``progressToken``.
    """
    global _flash_active_port, _flash_cleanup_needed, _flash_cleanup_dm
    loop = asyncio.get_running_loop()
    progress = _Progress(context, loop)

    def _stage(pct: float, message: str) -> None:
        progress.stage(pct, message)

    chip = _guide.normalize_chip(chip)

    # -- Validate params early ─────────────────────────────────────
    _stage(2, "校验烧录参数")
    if not mode:
        mode = _FLASH_DEFAULT_MODE.get(chip, "isp")

    if mode not in ("break", "isp"):
        raise FlashkeyError(
            E.INVALID_ARG, f"不支持的烧录模式: {mode}。可选: break, isp",
            hint="请使用 break 或 isp 模式",
        )

    # Reject FK-01 control port — must use fk_log (WCH-LinkE VCP)
    _validate_flash_port(flash_port)
    _validate_baud_for_port(flash_port, baud_rate)

    fw_path = Path(firmware_path).expanduser().resolve()
    if not fw_path.is_file():
        raise FlashkeyError(
            E.INVALID_ARG, f"固件文件不存在: {firmware_path}",
            hint="检查 firmware_path 是否指向真实存在的固件文件",
        )

    dm, fk = _require_fk()
    _stage(5, "获取烧录锁")

    # -- Resolve flash tool command ----------------------------------
    flash_cmd = _resolve_flash_tool(
        chip, tool, flash_dir, flash_port, baud_rate, fw_path, mode,
    )
    _stage(10, "解析烧录命令")

    # -- Acquire flash lock (mutual exclusion with log monitoring) ------
    if not _flash_lock.acquire(blocking=False):
        raise FlashkeyError(
            E.PORT_BUSY, "烧录进行中，请等待当前烧录完成后再试",
            hint="等待当前烧录结束后重试",
            retryable=True,
        )

    _flash_active_port = flash_port
    dm.pause_keepalive()  # 长操作期间禁止空闲释放 FK-01 控制口
    start_time = time.monotonic()
    output_lines: list[str] = []

    # ── BREAK mode (BL602 serial interrupt) ──────────────────────────
    if mode == "break":
        _flash_cleanup_needed = True
        _flash_cleanup_dm = dm
        _stage(15, "启动烧录工具（等待复位）")
        heartbeat = asyncio.create_task(
            _progress_heartbeat(progress, 15, 90, 45, "烧录进行中")
        )

        try:
            success, output_lines = await asyncio.to_thread(
                _flash_break_mode, fk, flash_cmd, flash_dir, 120, progress.stage,
            )
        finally:
            _flash_cleanup_needed = False
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            try:
                # RST 引脚应连接到 BL602 CHIP_EN — 烧录完成后复位使芯片正常启动
                fk.commands.rst_pulse(50)
            except Exception as exc:
                logger.error("Target recovery failed: %s", exc)
                output_lines.append(f"[警告] 目标芯片复位失败: {exc}")
            _flash_active_port = ""
            dm.resume_keepalive()
            _flash_lock.release()

        duration = time.monotonic() - start_time
        logger.info(
            "Flash break result: success=%s duration=%.1fs output_tail=%r",
            success, duration, "\n".join(output_lines)[-300:],
        )
        _stage(100, "烧录完成" if success else "烧录失败")
        return {
            "success": success,
            "output": "\n".join(output_lines),
            "duration": round(duration, 1),
            "chip": chip,
            "mode": mode,
        }

    # ── ISP mode (BL602 make eflash / BL616/BL618 make flash) ────────
    try:
        # Enter bootloader mode: BOOT=HIGH + RST pulse before flash tool
        fk.commands.boot_set(True)
        fk.commands.rst_pulse(50)
        await asyncio.sleep(0.2)  # ISP mode settling time
        _stage(15, "进入 ISP 模式（BOOT↑ + RST）")
        _stage(25, "启动烧录工具（固件写入中）")

        # -- Run external flash tool -----------------------------------
        logger.info("Flashing %s (ISP): %s", chip, " ".join(flash_cmd))

        _flash_cleanup_needed = True
        _flash_cleanup_dm = dm
        heartbeat = asyncio.create_task(
            _progress_heartbeat(progress, 25, 90, 60, "固件写入中")
        )

        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                flash_cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=flash_dir if flash_dir else None,
            )
            if proc.stdout:
                output_lines.append(proc.stdout)
            if proc.stderr:
                output_lines.append(proc.stderr)
            success = proc.returncode == 0
        except subprocess.TimeoutExpired:
            success = False
            output_lines.append("[错误] 烧录超时 (120 秒)")
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
    finally:
        # -- ALWAYS recover target -----------------------------------
        _flash_cleanup_needed = False
        try:
            fk.commands.rst_pulse(50)
            fk.commands.boot_set(False)
        except Exception as exc:
            logger.error("Target recovery failed: %s", exc)
            output_lines.append(f"[警告] 目标芯片复位失败: {exc}")
        _flash_active_port = ""
        dm.resume_keepalive()
        _flash_lock.release()

    duration = time.monotonic() - start_time
    logger.info(
        "Flash isp result: success=%s duration=%.1fs output_tail=%r",
        success, duration, "\n".join(output_lines)[-300:],
    )
    _stage(95, "目标芯片已复位")
    _stage(100, "烧录完成" if success else "烧录失败")
    return {
        "success": success,
        "output": "\n".join(output_lines),
        "duration": round(duration, 1),
        "chip": chip,
        "mode": mode,
    }


# ── FLASH_TOOL_CONFIG: chip → [make_cmd, baud_rate] ────────────────

_FLASH_BAUD_MAP: dict[str, int] = {
    "bl602": 921600,
    "bl616": 921600,
    "bl618": 921600,
}

_FLASH_MAKE_ARGS_MAP: dict[str, str] = {
    "bl602": "p={port} b={baud}",
    "bl616": "CHIP=bl616 COMX={port} BAUDRATE={baud}",
    "bl618": "CHIP=bl618 COMX={port} BAUDRATE={baud}",
}

_FLASH_MAKE_ISP_ARGS_MAP: dict[str, str] = {
    "bl602": "eflash p={port} b={baud}",
}


def _resolve_flash_tool(
    chip: str,
    tool: str,
    flash_dir: str,
    flash_port: str,
    baud_rate: int,
    fw_path: Path,
    mode: str = "",
) -> list[str]:
    """Resolve the flash tool command for the target chip.

    Priority:
    1. User-supplied ``tool`` (run as-is with args substitued)
    2. ``make flash`` / ``make eflash`` from SDK (if ``flash_dir`` is set)
    3. same from current directory (if Makefile has the target)
    4. Error with install instructions
    """
    supported = sorted(_FLASH_MAKE_ARGS_MAP.keys())
    if chip not in _FLASH_MAKE_ARGS_MAP:
        raise FlashkeyError(
            E.INVALID_ARG,
            f"不支持的芯片类型: {chip}。当前支持: {', '.join(supported)}",
            hint=f"请选择支持的芯片类型: {', '.join(supported)}",
        )

    # -- 1. User-supplied custom tool ---------------------------------
    if tool:
        return _build_custom_cmd(tool, chip, flash_port, baud_rate, fw_path)

    # -- 2. make flash / make eflash from SDK --------------------------
    make_dir = flash_dir or "."
    makefile = Path(make_dir) / "Makefile"

    if makefile.is_file():
        make_target = (
            "eflash" if mode == "isp" and chip == "bl602" else "flash"
        )
        # Verify the Makefile has the target
        try:
            result = subprocess.run(
                ["make", "-C", make_dir, "-n", make_target],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 2:  # 2 = no such target
                args_tpl = (
                    _FLASH_MAKE_ISP_ARGS_MAP.get(chip)
                    if mode == "isp" and chip == "bl602"
                    else _FLASH_MAKE_ARGS_MAP[chip]
                )
                args_str = args_tpl.format(port=flash_port, baud=baud_rate)
                return ["make", "-C", make_dir, make_target] + args_str.split()
        except Exception:
            pass

    # -- 3. No tool found → error with instructions ------------------
    if chip == "bl602" and mode == "isp":
        raise FlashkeyError(
            E.INVALID_ARG,
            "未找到 Ai-WB2 ISP 烧录工具（make eflash）。请克隆 Ai-Thinker-WB2 SDK，"
            "把 flash_dir 指向烧录工程目录（如 <sdk>/app），"
            "或通过 tool 参数指定烧录命令。\n"
            "SDK: https://github.com/Ai-Thinker-Open/Ai-Thinker-WB2",
            hint="设置 flash_dir 为烧录工程目录，或通过 tool 参数指定 make eflash 命令",
        )
    elif chip == "bl602":
        raise FlashkeyError(
            E.INVALID_ARG,
            "未找到 Ai-WB2 烧录工具（make flash）。请克隆 Ai-Thinker-WB2 SDK，"
            "把 flash_dir 指向烧录工程目录（如 <sdk>/app），"
            "或通过 tool 参数指定烧录命令。\n"
            "SDK: https://github.com/Ai-Thinker-Open/Ai-Thinker-WB2",
            hint="设置 flash_dir 为烧录工程目录，或通过 tool 参数指定烧录命令",
        )
    else:
        raise FlashkeyError(
            E.INVALID_ARG,
            f"未找到 {chip.upper()} 烧录工具。请克隆 Bouffalo SDK，"
            f"把 flash_dir 指向烧录工程目录，"
            f"或通过 tool 参数指定烧录命令。\n"
            "SDK: https://github.com/bouffalolab/bouffalo_sdk",
            hint="设置 flash_dir 为烧录工程目录，或通过 tool 参数指定烧录命令",
        )


def _build_custom_cmd(
    tool: str, chip: str, flash_port: str, baud_rate: int, fw_path: Path,
) -> list[str]:
    """Build a flash command from a user-supplied tool string.

    Supports ``{port}``, ``{baud}``, ``{firmware}``, ``{chip}`` placeholders.
    """
    result = []
    for part in tool.split():
        part = part.format(
            port=str(flash_port),
            baud=str(baud_rate),
            firmware=str(fw_path),
            chip=chip,
        )
        result.append(part)
    return result


# ======================================================================
# log_open / log_close — 后台日志监控
# ======================================================================

def _log_reader(
    ser: Any,
    stop_event: threading.Event,
    session: dict[str, Any],
) -> None:
    """后台读取 fk_log 串口，追加写入日志文件并保留最近 N 行。"""
    try:
        while not stop_event.is_set():
            try:
                data = ser.readline()
            except Exception:
                break
            if not data:
                continue
            try:
                line = data.decode("utf-8", errors="replace").rstrip("\r\n")
            except Exception:
                line = str(data)
            if not line:
                continue
            session["lines"].append(line)
            session["bytes"] += len(data)
            try:
                with _LOG_FILE.open("a", encoding="utf-8", errors="replace") as f:
                    f.write(line + "\n")
            except Exception:
                pass
    except Exception as exc:
        session["error"] = f"{type(exc).__name__}: {exc}"


def _tool_log_open(port: str, baud_rate: int = 115200, project: str = "") -> dict:
    """Open fk_log serial and start background log capture (non-blocking)."""
    import serial as pyserial

    global _flash_active_port

    project = _sanitize_project(project)
    _validate_flash_port(port)
    _validate_baud_for_port(port, baud_rate)

    with _log_session_lock:
        if _log_session["open"]:
            raise FlashkeyError(
                E.PORT_BUSY,
                f"日志监控已开启（{_log_session['port']}），请先调用 log_close()",
                hint="先调用 log_close() 关闭当前监控",
            )

        if not _flash_lock.acquire(blocking=False):
            raise FlashkeyError(
                E.PORT_BUSY,
                "烧录或其他串口操作进行中，暂时无法打开日志监控",
                hint="等待当前烧录/串口操作结束，或先 recover()",
                retryable=True,
            )

        try:
            ser = pyserial.Serial(port=port, baudrate=baud_rate, timeout=0.1)
        except Exception as exc:
            _flash_lock.release()
            raise FlashkeyError(
                E.PORT_BUSY, f"无法打开串口 {port}: {exc}",
                hint="确认设备已插入且没有其他程序占用该串口；WSL 下先 usbip attach",
                retryable=True,
            ) from exc

        try:
            _LOG_FILE.write_text("", encoding="utf-8")
        except Exception as exc:
            ser.close()
            _flash_lock.release()
            raise FlashkeyError(
                E.INTERNAL, f"无法初始化日志文件 {_LOG_FILE}: {exc}",
                hint="检查临时目录可写性后重试",
            ) from exc

        _flash_active_port = port
        _get_dm().pause_keepalive()  # 日志采集期间禁止空闲释放 FK-01 控制口
        stop_event = threading.Event()
        _log_session.update(
            open=True,
            port=port,
            baud_rate=baud_rate,
            project=project,
            serial=ser,
            stop_event=stop_event,
            started_at=time.monotonic(),
            ended_at=0.0,
            lines=deque(maxlen=_LOG_MAX_LINES),
            bytes=0,
            error="",
        )
        thread = threading.Thread(
            target=_log_reader,
            args=(ser, stop_event, _log_session),
            daemon=True,
            name="fk-log-reader",
        )
        _log_session["thread"] = thread
        thread.start()

    return {
        "ok": True,
        "monitoring": True,
        "port": port,
        "baud_rate": baud_rate,
        "project": project,
        "log_resource": "flashkey://log",
    }


def _prune_log_history(project: str) -> int:
    """每个项目最多保留 _LOG_HISTORY_MAX 份日志，超出删除最旧的。"""
    try:
        dest_dir = _LOG_HISTORY_DIR / project
        files = sorted(
            dest_dir.glob("flashkey-log-*.txt"),
            key=lambda p: p.stat().st_mtime,
        )
        removed = 0
        while len(files) > _LOG_HISTORY_MAX:
            files[0].unlink(missing_ok=True)
            files.pop(0)
            removed += 1
        return removed
    except Exception as exc:
        logger.warning("Failed to prune log history for %s: %s", project, exc)
        return 0


def _newest_archive_text() -> str:
    """读取整个历史目录里最新一份归档日志的内容（用于去重）。"""
    try:
        candidates = [
            p
            for p in _LOG_HISTORY_DIR.glob("*/flashkey-log-*.txt")
            if p.is_file()
        ]
        if not candidates:
            return ""
        newest = max(candidates, key=lambda p: p.stat().st_mtime)
        return newest.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _archive_orphan_log() -> dict:
    """log_close 无会话时，把临时日志文件补归档（防重启/SIGKILL 丢失，去重）。"""
    try:
        text = _LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"archived": False, "reason": "无日志文件"}
    if not text.strip():
        return {"archived": False, "reason": "无日志内容"}
    if _newest_archive_text() == text:
        return {"archived": False, "reason": "已归档过"}
    return _archive_log("default", text.splitlines())


def _archive_log(project: str, lines: list[str]) -> dict:
    """把本次日志归档到 ~/flashkey-logs/<project>/，每项目最多保留 10 份。"""
    try:
        text = "\n".join(lines)
        if not text.strip():
            return {"archived": False, "reason": "无日志内容"}
        text += "\n"  # 与 flashkey://log 的落盘格式保持一致
        dest_dir = _LOG_HISTORY_DIR / project
        dest_dir.mkdir(parents=True, exist_ok=True)
        base = f"flashkey-log-{time.strftime('%Y%m%d-%H%M%S')}"
        dest = dest_dir / f"{base}.txt"
        n = 1
        while dest.exists():  # 同一秒多次关闭避免覆盖
            dest = dest_dir / f"{base}-{n}.txt"
            n += 1
        dest.write_text(text, encoding="utf-8")
        removed = _prune_log_history(project)
        return {
            "archived": True,
            "path": str(dest.resolve()),
            "bytes": len(text.encode("utf-8")),
            "lines": len(lines),
            "removed_old": removed,
        }
    except Exception as exc:
        logger.warning("Failed to archive log for project %s: %s", project, exc)
        return {"archived": False, "reason": f"{type(exc).__name__}: {exc}"}


def _tool_log_close() -> dict:
    """Stop background log capture, close serial and finalize flashkey://log."""
    global _flash_active_port

    with _log_session_lock:
        if not _log_session["open"]:
            result = {"ok": True, "monitoring": False, "message": "未在监控"}
            recovery = _archive_orphan_log()
            if recovery.get("archived"):
                result["archive"] = recovery
            return result

        stop_event = _log_session["stop_event"]
        if stop_event is not None:
            stop_event.set()
        thread = _log_session["thread"]
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        ser = _log_session["serial"]
        if ser is not None:
            try:
                ser.close()
            except Exception as exc:
                logger.warning("Failed to close log serial: %s", exc)

        lines = list(_log_session["lines"])
        total_bytes = _log_session["bytes"]
        try:
            with _LOG_FILE.open("w", encoding="utf-8", errors="replace") as f:
                for line in lines:
                    f.write(line + "\n")
        except Exception as exc:
            logger.warning("Failed to finalize log file: %s", exc)

        duration = time.monotonic() - _log_session["started_at"]
        port = _log_session["port"]
        baud_rate = _log_session["baud_rate"]
        project = _log_session.get("project", "default")
        error = _log_session["error"]
        archive = _archive_log(project, lines)

        _log_session.update(
            open=False,
            port="",
            baud_rate=115200,
            project="default",
            serial=None,
            thread=None,
            stop_event=None,
            started_at=0.0,
            ended_at=time.monotonic(),
            lines=deque(maxlen=_LOG_MAX_LINES),
            bytes=0,
            error="",
        )
        _flash_active_port = ""
        _get_dm().resume_keepalive()
        try:
            _flash_lock.release()
        except RuntimeError:
            pass  # 防御：锁已被其他路径释放时避免崩溃

        result = {
            "ok": True,
            "monitoring": False,
            "port": port,
            "baud_rate": baud_rate,
            "project": project,
            "archive": archive,
            "duration_s": round(duration, 2),
            "lines": len(lines),
            "bytes": total_bytes,
            "log_resource": "flashkey://log",
        }
        if error:
            result["error"] = error
        return result


def _shutdown_archive_log() -> None:
    """服务退出时若日志监控仍开启，先归档本次日志（防重启丢失）。"""
    global _flash_active_port
    try:
        with _log_session_lock:
            if not _log_session["open"]:
                return
            lines = list(_log_session["lines"])
            project = _log_session.get("project", "default")
            archive = _archive_log(project, lines)
            logger.info("Shutdown log archive for %s: %s", project, archive)
            try:
                with _LOG_FILE.open("w", encoding="utf-8", errors="replace") as f:
                    for line in lines:
                        f.write(line + "\n")
            except Exception:
                pass
            _log_session["open"] = False
            _flash_active_port = ""
            try:
                _flash_lock.release()
            except RuntimeError:
                pass
    except Exception:
        logger.exception("Failed to archive log on shutdown")


def _tool_log_dump(dest_path: str = "") -> dict:
    """把最近一次采集的日志转存到本地文件（不触碰串口，无需认证）。"""
    try:
        text = _LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        text = ""
    except Exception as exc:
        raise FlashkeyError(
            E.INVALID_ARG,
            f"读取日志失败: {exc}",
            hint="先调用 log_open() 采集、log_close() 关闭后再转存",
        )
    if not text.strip():
        return {
            "success": False,
            "message": "暂无日志，请先 log_open() 采集并 log_close() 后再转存",
            "path": "",
            "bytes": 0,
            "lines": 0,
        }

    if dest_path:
        dest = Path(dest_path).expanduser()
    else:
        dest = Path.home() / "flashkey-logs" / (
            f"flashkey-log-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        )
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise FlashkeyError(
            E.INVALID_ARG,
            f"写入日志文件失败: {exc}",
            hint=f"检查目标路径是否可写，或换一个路径，如 {Path.home() / 'flashkey-logs'}",
        )

    return {
        "success": True,
        "path": str(dest.resolve()),
        "bytes": len(text.encode("utf-8")),
        "lines": text.count("\n"),
        "message": f"日志已转存到 {dest}",
    }


# ======================================================================
# send (NEW) — 串口数据发送
# ======================================================================


def _tool_send(
    port: str,
    data: str,
    baud_rate: int = 115200,
    encoding: str = "text",
    read_response: bool = False,
    read_timeout: float = 1.0,
) -> dict:
    """Send serial data to the target chip through the WCH-LinkE VCP UART bridge.

    Opens *port* (the WCH-LinkE VCP used for flashing/logging),
    sends *data*, optionally reads back a response, and closes the port.

    Encoding modes:
    - ``"text"`` (default): send the string as UTF-8 bytes. Supports
      escape sequences like ``\\n``, ``\\r``, ``\\t``, ``\\\\``.
    - ``"hex"``: parse *data* as a hex string (spaces optional),
      e.g. ``"48 65 6C 6C 6F"`` or ``"48656C6C6F"``.

    Args:
        port: The serial port (role=fk_log, the WCH-LinkE VCP; NOT fk_control).
        data: The data payload to send.
        baud_rate: Serial baud rate (default 115200).
        encoding: ``"text"`` (default) or ``"hex"``.
        read_response: If True, read back data from the target for
              up to *read_timeout* seconds after sending.
        read_timeout: Max seconds to wait for a response (default 1.0, max 10.0).
    """
    import serial as pyserial

    # Reject FK-01 control port
    _validate_flash_port(port)
    _validate_baud_for_port(port, baud_rate)

    # Mutual exclusion with flash on the same port
    if _flash_lock.locked() and _flash_active_port == port:
        raise FlashkeyError(
            E.PORT_BUSY, "烧录进行中，串口正忙，请等待烧录完成",
            hint="等待烧录结束后重试",
            retryable=True,
        )

    # Decode data based on encoding
    if encoding == "text":
        # unicode_escape interprets literal \n \r \t \\ etc. as control chars,
        # while leaving already-decoded control chars from JSON unchanged.
        raw = data.encode("utf-8").decode("unicode_escape").encode("latin-1")
    elif encoding == "hex":
        hex_str = data.replace(" ", "").replace("\n", "").replace("\t", "")
        if len(hex_str) % 2 != 0:
            raise FlashkeyError(
                E.INVALID_ARG, "hex 编码数据长度必须为偶数",
                hint="提供偶数长度的 hex 字符串",
            )
        try:
            raw = bytes.fromhex(hex_str)
        except ValueError as exc:
            raise FlashkeyError(
                E.INVALID_ARG, f"hex 解码失败: {exc}",
                hint="检查 hex 字符串是否合法",
            )
    else:
        raise FlashkeyError(
            E.INVALID_ARG, f"不支持的编码: {encoding}。可选: text, hex",
            hint="请使用 text 或 hex",
        )

    if not raw:
        raise FlashkeyError(
            E.INVALID_ARG, "发送数据不能为空",
            hint="提供非空数据",
        )

    # Clamp read_timeout
    read_timeout = min(max(read_timeout, 0.1), 10.0)

    response_lines: list[str] = []
    actual_sent: int = 0

    try:
        ser = pyserial.Serial(port=port, baudrate=baud_rate, timeout=0.1)
    except Exception as exc:
        raise FlashkeyError(
            E.PORT_BUSY, f"无法打开串口 {port}: {exc}",
            hint="确认设备已插入且没有其他程序占用该串口；WSL 下先 usbip attach",
            retryable=True,
        )

    try:
        ser.reset_input_buffer()
        actual_sent = ser.write(raw)
        ser.flush()

        if read_response:
            deadline = time.monotonic() + read_timeout
            while time.monotonic() < deadline:
                try:
                    line_bytes = ser.readline()
                except Exception:
                    break
                if line_bytes:
                    try:
                        line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                    except Exception:
                        line = str(line_bytes)
                    response_lines.append(line)
    finally:
        ser.close()

    result: dict[str, Any] = {
        "sent": actual_sent,
        "data": _summarize_data(raw, encoding),
    }
    if read_response:
        result["response_lines"] = len(response_lines)
        result["response"] = "\n".join(response_lines) if response_lines else "(无响应)"

    return result


def _summarize_data(raw: bytes, encoding: str) -> str:
    """Create a human-readable summary of the sent data payload."""
    if encoding == "hex":
        if len(raw) <= 50:
            return raw.hex(" ")
        return raw[:50].hex(" ") + f"... ({len(raw)} bytes)"
    else:
        text = raw.decode("utf-8", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
        if len(raw) <= 50:
            return text
        return f"{text[:50]}... ({len(raw)} bytes)"


# ======================================================================
# MCP server setup
# ======================================================================

from mcp.server.fastmcp import Context, FastMCP  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402
from mcp.server.fastmcp.resources import FunctionResource, TextResource  # noqa: E402

mcp = FastMCP(
    name="flashkey-mcp",
    instructions=_guide._INSTRUCTIONS,
)

# ── Register 19 tools ───────────────────────────────────────────────
# Note: each tool function's signature is used by FastMCP to generate
# JSON Schema.  Only bool / int / str / float / Optional[str] types
# are allowed — no custom class arguments.

# Status & discovery (no auth required)
mcp.add_tool(
    _tool_wrapper(_tool_status, require_auth=False),
    name="status",
    description=(
        "查询 FlashKey FK-01 统一状态。不需要认证，始终可调用。"
        "返回认证状态(authed)、是否空闲释放串口(idle)、固件版本(version)、"
        "引脚状态(boot/rst/v5v/v3v3)。"
        "idle=true 表示串口因空闲超时已释放，下次调用会自动重连。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_list_ports, require_auth=False),
    name="list_ports",
    description=(
        "列出系统所有可用串口。每项包含 port、description、VID、PID、role。\n"
        "role=fk_control → FK-01 主控口 (MCP 内部使用，不能用于烧录/日志)\n"
        "role=fk_log     → WCH-LinkE VCP (FK-01 v0.1.1 日志/烧录口，最高 921600)\n"
        "role=unknown    → 其他设备\n"
        "烧录或采集日志前，务必先调用此工具确认端口 role。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_recover, require_auth=False),
    name="recover",
    description=(
        "🛠 一站式恢复：可选 USB 重挂载 + 强制重新握手。无需认证，工具失败时优先调用。\n"
        "参数:\n"
        "  reattach: 是否先尝试用 usbipd.exe 重新挂载 FK-01 / WCH-LinkE 设备（WSL 环境），默认 False\n"
        "返回: ok(是否恢复成功)、connected、authed、error(失败原因)、status(完整状态)、hints(下一步建议)\n"
        "典型用法: 工具返回 DEVICE_NOT_FOUND / HANDSHAKE_FAILED / PORT_BUSY 时，先调本工具恢复再重试。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_module_info, require_auth=False),
    name="module_info",
    description=(
        "查询 FlashKey 扩展模块状态（无需认证）。"
        "返回模块是否在线(present)、模块身份(module)、已注册的 mod_* 动态工具列表(tools)、"
        "以及模块自主上报数据的统计(data)。"
    ),
)

# Communication
mcp.add_tool(
    _tool_wrapper(_tool_ping),
    name="ping",
    description="Ping FlashKey 设备并返回 magic 标识字符串。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_auth_status),
    name="auth_status",
    description="查询 FK-01 认证状态。⚠️ 已弃用(DEPRECATED)，建议使用 status()。需要认证。",
)

# GPIO control
mcp.add_tool(
    _tool_wrapper(_tool_boot_set),
    name="boot_set",
    description="设置 BOOT 引脚 (PB3) 高(value=True) 或低(value=False)。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_boot_get),
    name="boot_get",
    description="读取 BOOT 引脚 (PB3) 当前状态。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_rst_set),
    name="rst_set",
    description="设置 RST 引脚 (PB4) 高(value=True) 或低(value=False)。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_rst_get),
    name="rst_get",
    description="读取 RST 引脚 (PB4) 当前状态。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_rst_pulse),
    name="rst_pulse",
    description="在 RST 引脚上产生指定毫秒(ms)的负脉冲，默认 50ms。需要认证。",
)

# Power control
mcp.add_tool(
    _tool_wrapper(_tool_v5v_set),
    name="v5v_set",
    description="控制 5V 电源输出 (PB13, 低电平有效)，value=True 开启。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_v5v_get),
    name="v5v_get",
    description="读取 5V 电源当前状态。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_vusb_set),
    name="vusb_set",
    description=(
        "控制外置 USB-A 电源输出 (PA0, 低电平有效)：value=True 拉低 PA0 = 开启/启动，"
        "value=False 拉高 PA0 = 关闭。默认关闭。需要认证。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_vusb_get),
    name="vusb_get",
    description="读取外置 USB-A 电源当前状态 (True=开启/PA0低, False=关闭/PA0高)。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_v3v3_set),
    name="v3v3_set",
    description="控制 3.3V 电源输出 (PB0, 高电平有效)，value=True 开启。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_v3v3_get),
    name="v3v3_get",
    description="读取 3.3V 电源当前状态。需要认证。",
)

# ── flash_guide（烧录前学习流程）─────────────────────────────────────

def _tool_flash_guide(chip: str = "ai-wb2", mode: str = "") -> dict:
    """返回 Ai-WB2 / Ai-M62 标准烧录流程文本（无需认证，供 AI 烧录前学习）。"""
    try:
        messages = _guide._prompt_flash_firmware(
            chip=chip,
            firmware_path="<固件绝对路径>",
            mode=mode,
        )
        guide_text = messages[1].content.text
        return {
            "ok": True,
            "chip": _guide.normalize_chip(chip),
            "guide": guide_text,
        }
    except Exception as exc:
        raise FlashkeyError(
            E.INVALID_ARG,
            f"无法生成烧录指南: {exc}",
            hint="chip 支持 ai-wb2 / ai-m62；mode 可选 break / isp",
        )


mcp.add_tool(
    _tool_wrapper(_tool_flash_guide, require_auth=False),
    name="flash_guide",
    description=(
        "📖 烧录指南：返回 Ai-WB2 / Ai-M62 的标准烧录流程文本（选端口 → 认证 → flash → 验证）。\n"
        "烧录前必须先调用本工具学习正确流程，再按步骤执行（无需认证）。\n"
        "参数:\n"
        "  chip: 模组名称，默认 ai-wb2（支持 ai-wb2 / ai-m62）\n"
        "  mode: 可选；ai-wb2 的 break(默认)/isp\n"
        "返回: ok、chip、guide（完整步骤文本）。"
    ),
)
# Version & UID
mcp.add_tool(
    _tool_wrapper(_tool_get_version),
    name="get_version",
    description="读取 FK-01 固件版本号 (如 '0.1.1')。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_get_uid),
    name="get_uid",
    description="读取 FK-01 设备唯一 ID (16 字符 hex 字符串)。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_get_events, require_auth=False),
    name="get_events",
    description=(
        "读取服务器已记录的 FlashKey 事件（如用户手动操作 PB8/PB9 按键），"
        "每条包含事件名、按键、动作和操作时间戳。无需认证。"
    ),
)

# Deprecated (replaced by status)
mcp.add_tool(
    _tool_wrapper(_tool_get_status),
    name="get_status",
    description="读取引脚状态。⚠️ 已弃用(DEPRECATED)，建议使用 status()。需要认证。",
)

# Convenience
mcp.add_tool(
    _tool_wrapper(_tool_enter_bootloader),
    name="enter_bootloader",
    description=(
        "组合操作: BOOT 拉高 → RST 脉冲 → 目标芯片进入烧录模式。"
        "等效于 boot_set(True) + rst_pulse()。需要认证。\n"
        "常见错误: AUTH_REQUIRED(先完成密钥认证)、DEVICE_NOT_FOUND(设备掉线→recover)。"
    ),
)

# ── NEW tools ───────────────────────────────────────────────────────

mcp.add_tool(
    _tool_wrapper(_tool_flash),
    name="flash",
    description=(
        "⚡ 一键烧录固件到目标芯片 (阻塞操作，耗时 10-120 秒)。\n"
        "\n"
        "⚠️ 调用前必须先调用 list_ports() 获取当前真实端口，禁止硬编码或复用旧端口名。\n"
        "⚠️ 端口选择：先用 list_ports() 查看端口列表，选择 role=fk_log (WCH-LinkE VCP) 的端口。\n"
        "绝对不能使用 role=fk_control 的端口（那是 FK-01 主控口，MCP 内部专用）。\n"
        "不要根据端口名猜测角色，不同系统上名字不同 (COMx / ttyACMx / ttyUSBx / cu.*)。\n"
        "注意：WCH-LinkE VCP (fk_log) 最高仅支持 921600，需要更高波特率时请用外接 USB-UART。\n"
        "\n"
        "支持两种烧录模式:\n"
        "  Ai-WB2 break（默认，串口打断）: 启动 make flash 后，工具会等待模组复位；\n"
        "         FK-01 自动触发一次 RST 脉冲进入烧录（不依赖解析提示文本）；\n"
        "         只烧 App 不烧 boot2，无法触发时改用 ISP。\n"
        "  Ai-WB2 isp: mode=\"isp\" → BOOT↑ + RST 脉冲进入 ISP 模式，执行 make eflash；\n"
        "         全量烧录含 boot2；make erase_flash 擦除芯片后必须用它。\n"
        "  Ai-M62 (isp): BOOT↑ → RST 脉冲 → 烧录工具 → 恢复\n"
        "参数:\n"
        "  firmware_path: 固件文件绝对路径\n"
        "  flash_port: 烧录串口 — 必须选 list_ports() 中 role=fk_log 的端口\n"
        "  chip: 模组名称，支持 Ai-WB2 / Ai-M62\n"
        "  baud_rate: 烧录波特率。FlashKey 自带串口 (fk_log) 最高仅支持 921600，\n"
        "         Ai-WB2 默认 921600；Ai-M62 默认 921600（如需 2000000 必须改用外接 USB-UART）\n"
        "  tool: 可选，自定义烧录命令 (如 'make flash p={port} b={baud}' 或 Ai-WB2 ISP\n"
        "         'make eflash p={port} b={baud}' 占位符)\n"
        "  flash_dir: 可选，烧录命令执行目录（包含 Makefile 的工程目录，如 Ai-WB2 SDK 的 <sdk>/app；用于 make flash / make eflash）\n"
        "  mode: 烧录模式 (break/isp)。Ai-WB2 默认 break，Ai-M62 默认 isp；Ai-WB2 ISP 使用 make eflash。"
        "需要认证。\n"
        "常见错误: PORT_WRONG_ROLE(端口选错→先用 list_ports 按 role 选 fk_log)、"
        "INVALID_ARG(chip/固件路径错→按 hint 修正)、FLASH_VERIFY_FAILED(chip 与固件不匹配→重烧)、"
        "DEVICE_NOT_FOUND(设备掉线→recover(reattach=True))。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_log_open),
    name="log_open",
    description=(
        "📂 打开目标芯片串口日志监控（需要认证，立即返回，不阻塞 AI Agent）。\n"
        "⚠️ 端口选择：先用 list_ports() 选择 role=fk_log (WCH-LinkE VCP)，绝不能使用 fk_control。\n"
        "开启后 server 在后台持续读取串口并写入日志文件；AI 可以继续调用 rst_pulse 等其他工具，"
        "不必持续监控串口。\n"
        "完成后必须调用 log_close() 关闭监控，然后读取资源 flashkey://log 获取日志。\n"
        "参数:\n"
        "  port: 日志串口 — role=fk_log\n"
        "  baud_rate: 日志波特率，默认 115200\n"
        "  project: 可选；项目名（默认 default），用于历史日志归档目录名\n"
        "返回: ok、monitoring、port、baud_rate、project、log_resource\n"
        "与 flash / send 互斥；重复 open 返回 PORT_BUSY。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_log_close, require_auth=False),
    name="log_close",
    description=(
        "🛑 关闭目标芯片串口日志监控（无需认证）。\n"
        "停止后台读取、关闭并释放串口，把本次日志覆盖写入资源 flashkey://log。\n"
        "同时自动归档到历史日志：~/flashkey-logs/<project>/flashkey-log-<时间>.txt，\n"
        "每个项目最多保留 10 份，超出覆盖最旧；可用资源 flashkey://logs/{project} 列出、\n"
        "flashkey://logs/{project}/{file} 读取。调用后 AI 应读取 flashkey://log 获取本次日志。\n"
        "未开启监控时返回成功 no-op。\n"
        "返回: ok、monitoring、port、baud_rate、project、archive、duration_s、lines、bytes、log_resource。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_log_dump, require_auth=False),
    name="log_dump",
    description=(
        "📤 把最近一次采集的日志（log_open → log_close 后，即 flashkey://log 的内容）"
        "转存到本地文件，便于长期保存与分析（无需认证，不触碰串口）。\n"
        "参数:\n"
        "  dest_path: 可选；目标文件路径。留空默认保存到 ~/flashkey-logs/flashkey-log-<时间戳>.txt。\n"
        "返回: success、path、bytes、lines；暂无日志时 success=false。\n"
        "注意: 日志来自最近一次 log_open/log_close，新的 log_open 会覆盖旧日志，请先转存再重新采集。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_send),
    name="send",
    description=(
        "📤 向目标芯片发送串口数据 (需要认证)。\n"
        "⚠️ 端口选择：先用 list_ports() 查看端口列表，选择 role=fk_log (WCH-LinkE VCP) 的端口。绝对不能使用 role=fk_control 的端口。\n"
        "参数:\n"
        "  port: 目标串口 — 必须选 list_ports() 中 role=fk_log 的端口\n"
        "  data: 要发送的数据字符串\n"
        "  baud_rate: 波特率，默认 115200\n"
        "  encoding: 编码方式 — \"text\"(默认，支持 \\n \\r \\t 转义) 或 \"hex\"(十六进制，空格可选)\n"
        "  read_response: 发送后是否读取目标芯片的响应，默认 False\n"
        "  read_timeout: 读取响应的超时秒数，默认 1.0，最大 10.0\n"
        "返回: sent(发送字节数)、data(数据摘要)；若 read_response=True，还包含 response(响应文本)、response_lines(行数)\n"
        "与 flash 互斥，串口忙时返回 isError。\n"
        "示例: send(port=\"/dev/ttyUSB0\", data=\"AT\\r\\n\", read_response=True) 发送 AT 指令并读取响应"
    ),
)


# ── firmware_check / firmware_flash (CH32V203 self-update) ──

def _read_device_version() -> str | None:
    """Best-effort current FK-01 firmware version; None when offline."""
    try:
        _, fk = _require_fk()
        return fk.commands.get_version().get("version")
    except Exception:
        return None


def _tool_firmware_check() -> dict:
    """Check whether a newer FK-01 firmware / flashkey-mcp release exists."""
    return firmware_tools.check_firmware_update(device_version=_read_device_version())


async def _tool_firmware_flash(
    context: Context | None = None,
    hex_path: str = "",
    confirm: bool = False,
    force: bool = False,
    dry_run: bool = False,
    timeout: int = firmware_tools.DEFAULT_FLASH_TIMEOUT_S,
) -> dict:
    """Flash the FK-01 CH32V203 firmware via WCH-LinkE (SDI)."""
    global _flash_active_port
    loop = asyncio.get_running_loop()
    progress = _Progress(context, loop)
    progress.stage(1, "校验烧录参数")
    if not _flash_lock.acquire(blocking=False):
        raise ToolError("烧录/日志会话进行中，请等待当前操作完成后再试")
    _flash_active_port = "<fk203-swd>"
    dm = _get_dm()
    dm.pause_keepalive()  # 长操作期间禁止空闲释放 FK-01 控制口
    heartbeat: asyncio.Task[Any] | None = None
    try:
        heartbeat = asyncio.create_task(
            _progress_heartbeat(progress, 10, 90, 45, "烧录 FK-01 固件中")
        )
        result = await asyncio.to_thread(
            firmware_tools.flash_ch32v203,
            hex_path=hex_path,
            confirm=confirm,
            force=force,
            dry_run=dry_run,
            timeout=timeout,
            get_version_fn=_read_device_version,
            progress_cb=progress.stage,
        )
        progress.stage(100, "烧录完成" if result.get("ok") else "烧录失败")
        return result
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        _flash_active_port = ""
        dm.resume_keepalive()
        _flash_lock.release()


mcp.add_tool(
    _tool_wrapper(_tool_firmware_check, require_auth=False),
    name="firmware_check",
    description=(
        "检查 FK-01 CH32V203 固件是否有更新（无需认证）。\n"
        "返回: device_version(设备当前固件版本，离线为 null)、"
        "installed_mcp_version(已安装 flashkey-mcp 版本)、"
        "latest_mcp_version(最新发布版本)、"
        "bundled_hex_version(当前安装包内置固件版本)、"
        "latest_hex_version(最新发布固件版本)、"
        "update_available(是否有新固件可烧)、"
        "package_update_available(是否需要先升级 flashkey-mcp 包以获取新 hex)、"
        "changelog(更新日志)、release_url(Release 链接)。\n"
        "若 GitHub 不可访问或尚无 Release，latest_* 为 null。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_firmware_flash),
    name="firmware_flash",
    description=(
        "烧录 FK-01 自身 CH32V203 固件（OpenOCD + WCH-LinkE SDI，需要认证）。\n"
        "⚠️ 前置条件：把 FlashKey 自带的 WCH-LinkE 通过 USB 接入电脑，"
        "并将 SWDIO/SWCLK/GND/3V3 接到 CH32V203 的 SWD 接口且目标板上电；"
        "WSL 环境需先把调试器 usbip attach 到 WSL。\n"
        "参数: hex_path(固件路径，默认使用包内内置固件)、"
        "confirm(必须显式传 True 才会执行)、"
        "force(允许烧录比设备当前更低的版本)、"
        "dry_run(只打印将执行的命令，不实际烧录)、"
        "timeout(OpenOCD 超时秒数，默认 90)。\n"
        "普通烧录失败且疑似读保护/写保护时，会自动用带 unlock 的全片擦除+烧录重试一次；"
        "仍失败则返回 WCH-LinkUtility 手动解锁指引。\n"
        "返回: ok、before_version、after_version、unlocked_retried、"
        "output_summary、duration_s；dry_run 时含 commands。\n"
        "常见错误: DEVICE_NOT_FOUND(WCH-LinkE 未挂载/接线→recover(reattach=True))、"
        "FLASH_PROTECTED(已自动解锁重试，仍失败用 WCH-LinkUtility)、AUTH_REQUIRED(先认证)。"
    ),
)


# ======================================================================
# MCP Resources & Prompts
# ======================================================================


def _resource_status() -> dict:
    """实时状态快照；设备离线/异常时返回含 error 字段的 JSON，不抛异常。"""
    try:
        result = _tool_status()
    except Exception as exc:
        result = {
            "authed": False,
            "idle": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if result.get("authed") is not True and "error" not in result:
        result["error"] = "FK-01 未连接/未认证（authed=false）"
    return result


def _resource_ports() -> dict:
    """实时串口列表；异常时返回含 error 字段的 JSON，不抛异常。"""
    try:
        return _tool_list_ports()
    except Exception as exc:
        return {
            "ports": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _resource_log() -> str:
    """读取最近一次日志监控写入的串口日志（不抛异常）。"""
    try:
        text = _LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "(无日志)"
    except Exception as exc:
        return f"(日志读取失败: {exc})"
    return text if text else "(无日志)"


@mcp.resource(
    "flashkey://logs/{project}",
    name="flashkey-log-history",
    title="历史日志列表",
    description="指定项目的历史日志文件列表（每项目最多 10 份，超出覆盖最旧）。",
    mime_type="application/json",
)
def _resource_log_history(project: str) -> dict:
    """列出某项目的历史日志（不抛异常）。"""
    try:
        safe = _sanitize_project(project)
        dest_dir = _LOG_HISTORY_DIR / safe
        files = []
        if dest_dir.is_dir():
            for p in sorted(
                dest_dir.glob("flashkey-log-*.txt"),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            ):
                files.append(
                    {
                        "name": p.name,
                        "path": str(p.resolve()),
                        "bytes": p.stat().st_size,
                        "mtime": time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)
                        ),
                    }
                )
        return {
            "project": safe,
            "max_files": _LOG_HISTORY_MAX,
            "files": files,
        }
    except Exception as exc:
        return {
            "project": project,
            "max_files": _LOG_HISTORY_MAX,
            "files": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


@mcp.resource(
    "flashkey://logs/{project}/{file}",
    name="flashkey-log-history-file",
    title="历史日志内容",
    description="读取指定项目下某一份历史日志的完整内容。",
    mime_type="text/plain",
)
def _resource_log_file(project: str, file: str) -> str:
    """读取某份历史日志内容（防路径穿越，不抛异常）。"""
    try:
        dest_dir = _LOG_HISTORY_DIR / _sanitize_project(project)
        name = Path(file).name
        p = dest_dir / name
        if not p.is_file():
            return f"(未找到历史日志: {project}/{file})"
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"(读取历史日志失败: {exc})"


mcp.add_resource(
    TextResource(
        uri="flashkey://docs/quickstart",
        name="flashkey-docs-quickstart",
        title="快速上手",
        description="FK-01 上手流程：查状态、选端口、认证、烧录/日志。",
        mime_type="text/markdown",
        text=_guide.QUICKSTART_DOC,
    )
)
mcp.add_resource(
    TextResource(
        uri="flashkey://docs/flash-guide",
        name="flashkey-docs-flash-guide",
        title="烧录指南",
        description="Ai-WB2/Ai-M62 烧录端口选择、默认模式/波特率与验证步骤。",
        mime_type="text/markdown",
        text=_guide.FLASH_GUIDE_DOC,
    )
)
mcp.add_resource(
    TextResource(
        uri="flashkey://docs/error-codes",
        name="flashkey-docs-error-codes",
        title="错误码表",
        description="全部错误码的含义、下一步、是否可重试与恢复工具。",
        mime_type="text/markdown",
        text=_guide.ERROR_CODES_DOC,
    )
)
mcp.add_resource(
    FunctionResource(
        uri="flashkey://status",
        name="flashkey-status",
        title="实时状态",
        description="实时设备状态快照（无需认证；离线时包含 error 字段）。",
        mime_type="application/json",
        fn=_resource_status,
    )
)
mcp.add_resource(
    FunctionResource(
        uri="flashkey://ports",
        name="flashkey-ports",
        title="实时串口列表",
        description="实时串口列表（含 role 字段；异常时包含 error 字段）。",
        mime_type="application/json",
        fn=_resource_ports,
    )
)
mcp.add_resource(
    FunctionResource(
        uri="flashkey://log",
        name="flashkey-log",
        title="串口日志",
        description="最近一次 log_open/close 采集的串口日志（文本，覆盖式）。",
        mime_type="text/plain",
        fn=_resource_log,
    )
)

_guide.register_prompts(mcp)


# ======================================================================
# Entry point
# ======================================================================

def _handle_upgrade() -> None:
    """Upgrade flashkey-mcp; install source overridable via FLASHKEY_INSTALL_URL."""
    from flashkey_mcp import __version__

    install_url = os.environ.get(
        "FLASHKEY_INSTALL_URL",
        "git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git",
    )
    print(f"Current version: {__version__}")
    print(f"Upgrading from {install_url} ...")
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "--upgrade", install_url,
        ],
        capture_output=False,
    )
    if result.returncode != 0:
        print("Upgrade failed. Try manually:")
        print(f'  pip install --upgrade "{install_url}"')
        sys.exit(1)

    print("Upgrade complete. Restarting service...")
    subprocess.run(["systemctl", "--user", "restart", "flashkey-mcp"], capture_output=True)
    print("Service restarted. Check status: flashkey-mcp --service status")


def _service_template_dir() -> Path:
    """Locate the systemd unit-template directory.

    Prefers templates bundled inside the installed package
    (``flashkey_mcp/configs/``), falling back to a source checkout
    (``<repo>/configs``) for editable installs.
    """
    pkg_dir = Path(__file__).resolve().parent
    for base in (
        pkg_dir / "configs",                # installed package / editable src
        pkg_dir.parent.parent / "configs",  # legacy: repo top-level configs/
    ):
        if (base / "flashkey-mcp.service").exists():
            return base
    return pkg_dir / "configs"


def _handle_service_command(action: str) -> None:
    """Install / uninstall / check status of systemd user service."""
    import shutil
    import subprocess as _sp

    service_name = "flashkey-mcp"
    unit_file = _service_template_dir() / f"{service_name}.service"
    user_unit_dir = Path.home() / ".config" / "systemd" / "user"

    if action == "status":
        result = _sp.run(
            ["systemctl", "--user", "is-active", service_name],
            capture_output=True, text=True,
        )
        enabled = _sp.run(
            ["systemctl", "--user", "is-enabled", service_name],
            capture_output=True, text=True,
        )
        active = result.stdout.strip()
        auto_start = enabled.stdout.strip()
        print(f"flashkey-mcp service: active={active}, auto-start={auto_start}")
        if active == "active":
            print(f"SSE endpoint: http://127.0.0.1:8100/sse")
        if active != "active":
            print(f"Hint: flashkey-mcp --service install && systemctl --user start {service_name}")
        return

    if action == "install":
        # Resolve the full path to flashkey-mcp binary
        fk_bin = shutil.which("flashkey-mcp")
        if not fk_bin:
            # Try common pip user install locations
            for candidate in [
                Path.home() / ".local" / "bin" / "flashkey-mcp",
                Path.home() / ".local" / "share" / "uv" / "python",
            ]:
                if candidate.exists():
                    fk_bin = str(candidate)
                    break
            else:
                print("Error: cannot find flashkey-mcp binary. Ensure it's on PATH.")
                sys.exit(1)

        user_unit_dir.mkdir(parents=True, exist_ok=True)
        if not unit_file.exists():
            print(f"Error: service template not found at {unit_file}")
            sys.exit(1)

        # Read template and substitute the binary path
        template = unit_file.read_text()
        unit_content = template.replace("__FLASHKEY_MCP_BIN__", fk_bin)
        dest = user_unit_dir / f"{service_name}.service"
        dest.write_text(unit_content)
        print(f"Installed: {dest}")
        print(f"Binary: {fk_bin}")
        _sp.run(["systemctl", "--user", "daemon-reload"], check=True)
        _sp.run(["systemctl", "--user", "enable", service_name], check=True)
        _sp.run(["systemctl", "--user", "start", service_name], check=True)

        # Also install auto-upgrade timer (daily)
        for fname in ("flashkey-mcp-upgrade.service", "flashkey-mcp-upgrade.timer"):
            src = unit_file.parent / fname
            if src.exists():
                dst = user_unit_dir / fname
                content = src.read_text().replace("__FLASHKEY_MCP_BIN__", fk_bin)
                dst.write_text(content)
        _sp.run(["systemctl", "--user", "daemon-reload"], check=True)
        _sp.run(
            ["systemctl", "--user", "enable", "--now", "flashkey-mcp-upgrade.timer"],
            check=True,
        )

        print("Service started. SSE endpoint: http://127.0.0.1:8100/sse")
        print("Auto-upgrade: daily check enabled")
        print("Manual upgrade: flashkey-mcp --upgrade")
        print("MCP config to use in AI tool:")
        print('  {"flashkey": {"type": "sse", "url": "http://127.0.0.1:8100/sse"}}')
        return

    if action == "uninstall":
        _sp.run(["systemctl", "--user", "stop", service_name], capture_output=True)
        _sp.run(["systemctl", "--user", "disable", service_name], capture_output=True)
        # Also remove upgrade timer
        _sp.run(["systemctl", "--user", "disable", "--now", "flashkey-mcp-upgrade.timer"], capture_output=True)
        for fname in (f"{service_name}.service", "flashkey-mcp-upgrade.service", "flashkey-mcp-upgrade.timer"):
            unit = user_unit_dir / fname
            if unit.exists():
                unit.unlink()
                print(f"Removed: {unit}")
        _sp.run(["systemctl", "--user", "daemon-reload"], check=True)
        print("Service uninstalled.")
        return


def main() -> None:
    """Launch the FlashKey MCP server.

    Defaults to SSE (HTTP) transport — one shared daemon serves every
    AI session.  Pass ``--stdio`` for legacy single-session mode.

    Service management::

        flashkey-mcp --service install     # install systemd user service
        flashkey-mcp --service uninstall   # remove systemd user service
        flashkey-mcp --service status      # check if service is running
    """
    # Allow flashkey_mcp imports (runtime guard)
    import os as _os
    _os.environ["FLASHKEY_MCP"] = "1"

    parser = argparse.ArgumentParser(
        description="FlashKey FK-01 MCP Server",
    )
    parser.add_argument(
        "--transport", type=str, choices=["sse", "stdio"], default="sse",
        help=(
            "MCP transport: SSE (default, one shared daemon serves every "
            "AI session) or stdio (legacy, one process per session)"
        ),
    )
    parser.add_argument(
        "--sse", action="store_true",
        help="Run in SSE (HTTP) mode (this is the default)",
    )
    parser.add_argument(
        "--port", type=int, default=8100,
        help="SSE server port (default: 8100)",
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="SSE bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--stdio", action="store_true",
        help="Run in stdio mode (legacy single-session)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable DEBUG-level logging (default: INFO)",
    )
    parser.add_argument(
        "--log-file", type=str, default="",
        help=(
            "Write logs to FILE in addition to stderr.  "
            "Default: $TMPDIR/flashkey-mcp.log  "
            "(tail -f /tmp/flashkey-mcp.log on Linux,  "
            "Get-Content -Wait $env:TEMP\\flashkey-mcp.log on PowerShell)"
        ),
    )
    parser.add_argument(
        "--service", type=str, choices=["install", "uninstall", "status"],
        help="Manage systemd user service (install/uninstall/status)",
    )
    parser.add_argument(
        "--upgrade", action="store_true",
        help="Upgrade flashkey-mcp to latest version from GitHub",
    )
    parser.add_argument(
        "--version", action="store_true",
        help="Show version and exit",
    )
    args = parser.parse_args()

    # -- Transport resolution ----------------------------------------------
    # 默认 SSE；`--sse` / `--stdio` 为兼容旧命令的显式开关。
    transport = args.transport
    if args.sse:
        transport = "sse"
    if args.stdio:
        transport = "stdio"

    # -- Version ---------------------------------------------------------
    if args.version:
        from flashkey_mcp import __version__
        print(f"flashkey-mcp {__version__}")
        return

    # -- Upgrade ----------------------------------------------------------
    if args.upgrade:
        _handle_upgrade()
        return

    # -- Service management commands --------------------------------------
    if args.service:
        _handle_service_command(args.service)
        return

    # -- Resolve log file path ---------------------------------------------
    log_file = args.log_file
    if not log_file:
        log_file = str(Path(tempfile.gettempdir()) / "flashkey-mcp.log")

    # -- Configure logging (always stderr + file) --------------------------
    log_level = logging.DEBUG if args.debug else logging.INFO
    log_fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    log_datefmt = "%H:%M:%S"

    # File handler (always — so users can tail -f to monitor)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_fmt, datefmt=log_datefmt))

    # Stderr handler
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(logging.Formatter(log_fmt, datefmt=log_datefmt))

    logging.basicConfig(level=log_level, handlers=[file_handler, stream_handler])

    logger.info("Log file: %s", log_file)
    if args.debug:
        logger.info("Debug mode enabled")

    # Start DeviceManager immediately — by the time AI makes its first
    # tool call, FK-01 may already be discovered and handshake completed.
    _get_dm()
    # 服务退出时若有未关闭的日志监控会话，先归档再退出（防重启丢失）
    atexit.register(_shutdown_archive_log)

    if transport == "sse":
        # ── SSE mode (default) ──────────────────────────────────────
        logger.info("Transport: SSE (HTTP) on %s:%d", args.host, args.port)
        print(f"FlashKey MCP SSE endpoint: http://{args.host}:{args.port}/sse")
        print(
            'MCP client config: {"flashkey": {"type": "sse", '
            f'"url": "http://{args.host}:{args.port}/sse"}}'
        )
        _run_sse(args.host, args.port)
    else:
        # ── Stdio mode (legacy) ─────────────────────────────────────
        logger.info("Transport: stdio")
        try:
            mcp.run(transport="stdio")
        finally:
            if _dm is not None:
                _dm.stop()


def _run_sse(host: str, port: int) -> None:
    """Run the MCP server over HTTP SSE transport."""
    try:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route
    except ImportError as exc:
        logger.error(
            "SSE mode requires starlette/uvicorn, which are missing from "
            "this install.  Upgrade with: "
            "pip install --upgrade flashkey-mcp[sse]"
        )
        raise SystemExit(1) from exc

    # -- HTTP endpoints (SSE mode only, 兼容旧 API) ------------------

    async def handle_release(_request):
        """POST /release — release FK-01 port for WSL USB remapping."""
        global _dm
        if _dm is not None:
            _dm.stop()
            _dm = None
        return JSONResponse({"status": "released"})

    async def handle_reconnect(_request):
        """POST /reconnect — re-detect FK-01 and re-handshake."""
        global _dm
        if _dm is not None:
            _dm.stop()
        _dm = DeviceManager()
        _dm.start()
        # Wait briefly for handshake
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _dm.authed:
                break
            time.sleep(0.2)
        return JSONResponse({
            "status": "connected" if _dm.connected else "not found",
            "authed": _dm.authed,
        })

    sse_app = mcp.sse_app()
    streamable_app = mcp.streamable_http_app()
    # 兼容把 /sse 当 Streamable HTTP 端点使用的客户端（它们 POST /sse 而不是 /mcp）：
    # 把 /mcp 的处理器同时挂到 /sse 的 POST 上，避免这类客户端一直 405。
    streamable_endpoint = None
    for _route in streamable_app.routes:
        if getattr(_route, "path", None) == "/mcp":
            streamable_endpoint = getattr(_route, "endpoint", None)
            break
    # Streamable HTTP 会话管理器要求 run() 生命周期；SDK 将 lifespan 保存在
    # 返回的 Starlette 的 router.lifespan_context 上，复用到顶层 app。
    streamable_lifespan = streamable_app.router.lifespan_context

    app = Starlette(
        routes=[
            Route("/release", endpoint=handle_release, methods=["POST"]),
            Route("/reconnect", endpoint=handle_reconnect, methods=["POST"]),
            # Streamable HTTP（Codex 等客户端）：SDK 内部已注册 /mcp 路由
            *streamable_app.routes,
            # SSE（兼容旧客户端）：SDK 内部已注册 /sse 与 /messages 路由
            *sse_app.routes,
            # POST /sse → Streamable HTTP 兼容别名（放在 SSE 路由之后，
            # 避免抢占 GET /sse 的经典 SSE 语义）
            *(
                [Route("/sse", endpoint=streamable_endpoint, methods=["POST"])]
                if streamable_endpoint is not None
                else []
            ),
        ],
        lifespan=lambda _app: streamable_lifespan(_app),
    )

    # 端口占用检查：另一个 flashkey-mcp 实例已在运行时给出友好提示，
    # 而不是抛出一串 bind traceback（多会话场景下这是正常情况）。
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            print(
                f"另一个 flashkey-mcp 实例已在 {host}:{port} 运行。\n"
                f"直接使用端点即可: http://{host}:{port}/sse\n"
                f'MCP 客户端配置: {{"flashkey": {{"type": "sse", '
                f'"url": "http://{host}:{port}/sse"}}}}'
            )
            raise SystemExit(0) from None

    logger.info("Starting FlashKey MCP SSE server at http://%s:%d", host, port)
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        if _dm is not None:
            _dm.stop()


if __name__ == "__main__":
    main()
