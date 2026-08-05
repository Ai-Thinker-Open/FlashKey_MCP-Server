"""FlashKey FK-01 MCP Server — Stdio (default) or SSE transport.

Usage::

    flashkey-mcp              # stdio mode (default)
    flashkey-mcp --sse        # SSE on :8100
    flashkey-mcp --sse --port 8200  # SSE on custom port

On startup the server launches :class:`DeviceManager` which immediately
begins scanning for FK-01, performs the HELLO handshake on detection,
and maintains a PING keepalive.  By the time the AI makes its first
tool call, FK-01 may already be authenticated and ready.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from flashkey_mcp.transport import list_all_ports, FLASHKEY_VID, FLASHKEY_PID
from flashkey_mcp.device_manager import DeviceManager
from flashkey_mcp.modules import ModuleRegistry, module_timeout_ms
from flashkey_mcp import firmware_tools

logger = logging.getLogger(__name__)

# ── Port validation ───────────────────────────────────────────────────


def _validate_flash_port(port: str) -> None:
    """Raise ``ToolError`` if *port* is the FK-01 control port.

    FK-01 has two ports identified by VID/PID, not device name:
      - ``fk_control`` (VID=1A86, PID=FE0D) → FK-01 main controller, MCP only.
      - ``fk_log``     (VID=1A86, PID=8010) → WCH-LinkE VCP on v0.1.1, log/flash use this.

    Always use ``flashkey_list_ports()`` and match by ``role`` field.
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
                raise ToolError(
                    f"{port} 是 FK-01 主控端口 (role=fk_control, MCP 内部专用)，"
                    f"不能用于烧录或日志。{hint}"
                )
            return  # port found, not FK-01 control — OK
    # Port not found in system — let the actual serial open fail naturally

# ── Singleton device manager ─────────────────────────────────────────
_dm: DeviceManager | None = None
_module_registry = ModuleRegistry()
# Flash/log mutual exclusion lock (per serial port)
_flash_lock = threading.Lock()
_flash_active_port: str = ""


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

def _tool_wrapper(fn: Any, require_auth: bool = True) -> Any:
    """Wrap a tool function with common error handling.

    ``require_auth=True`` tools call ``DeviceManager.require_authed()``
    before the tool body.  Errors are raised as ``ToolError`` so FastMCP
    can set ``isError: true`` on the MCP response.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        if require_auth:
            _get_dm().require_authed()
        return fn(*args, **kwargs)

    return wrapper


def _require_fk():
    """Return the FlashKey device handle or raise ToolError."""
    dm = _get_dm()
    fk = dm.fk
    if fk is None:
        raise ToolError("设备未连接，请插入 FlashKey FK-01")
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

# ── flashkey_status (NEW, no auth required) ──────────────────────────

def _tool_status() -> dict:
    """Get unified device status — always callable, no auth needed."""
    return _get_dm().get_status()


# ── flashkey_list_ports (NEW, no auth required) ──────────────────────

def _tool_list_ports() -> dict:
    """List all available serial ports on the system."""
    return {"ports": list_all_ports()}


# ── flashkey_ping ────────────────────────────────────────────────────

def _tool_ping() -> dict:
    _, fk = _require_fk()
    return fk.commands.ping()


# ── flashkey_auth_status (DEPRECATED) ────────────────────────────────

def _tool_auth_status() -> dict:
    _, fk = _require_fk()
    result = fk.commands.auth_status()
    result["_deprecated"] = "请使用 flashkey_status() 代替"
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

# ── flashkey_get_events (v0.1.1) ─────────────────────────────────────

def _tool_get_events(limit: int = 20) -> dict:
    """Return recorded device events (e.g. manual PB8/PB9 button operations)."""
    dm = _get_dm()
    count = max(1, min(int(limit), 100))
    events = dm.get_recent_events(count)
    return {"count": len(events), "events": events}


# ── flashkey_get_status (DEPRECATED — use flashkey_status) ──────────

def _tool_get_status() -> dict:
    _, fk = _require_fk()
    result = fk.commands.get_status()
    result["authed"] = 1
    result["_deprecated"] = "请使用 flashkey_status() 代替"
    return result


def _tool_enter_bootloader() -> dict:
    _, fk = _require_fk()
    fk.commands.boot_set(True)
    fk.commands.rst_pulse()
    return {"result": "ok"}


# ======================================================================
# flashkey_flash (NEW) — 需求三
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


def _flash_break_mode(
    fk: Any,
    flash_cmd: list[str],
    sdk_path: str,
    flash_timeout: int = 120,
) -> tuple[bool, list[str]]:
    """BL602 serial break mode: run flash tool → detect prompt → RST pulse.

    The flash tool (bflb_iot_tool) sends a sync pattern on the flash port TX, then
    prints "Please Press Reset Key!" and waits.  FK-01 pulses its RST pin
    to reset the BL602 — the boot ROM detects the sync pattern at reset and
    enters bootloader.  No BOOT pin manipulation needed.

    Sequence:
    1. Start ``make flash`` (Popen), monitor stdout
    2. Detect "Please Press Reset Key!" prompt
    3. Pulse FK-01 RST → BL602 resets, boot ROM enters bootloader
    4. Wait for flash tool to complete handshake and write
    5. Recovery: RST pulse to boot normally

    Returns:
        ``(success, output_lines)``.
    """
    import threading as _threading

    # Ensure FK-01 GPIOs don't conflict with the flash port DTR/RTS control.
    # BOOT low = default, the serial bridge handles reset signalling via RTS.
    fk.commands.boot_set(False)

    proc = None
    output_lines: list[str] = []

    try:
        proc = subprocess.Popen(
            flash_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=sdk_path if sdk_path else None,
        )

        prompt_seen = _threading.Event()

        def _read_stdout():
            try:
                for line in iter(proc.stdout.readline, ""):
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

        reader = _threading.Thread(target=_read_stdout, daemon=True)
        reader.start()

        # Wait for reset prompt (30 s max)
        if not prompt_seen.wait(timeout=30):
            logger.warning("Break mode: no reset prompt within 30 s")
            if proc.poll() is None:
                proc.kill()
            reader.join(timeout=2)
            return False, output_lines + [
                "[错误] 未在 30 秒内检测到烧录工具的复位提示"
            ]

        logger.info("Break mode: reset prompt detected, pulsing FK-01 RST")
        fk.commands.rst_pulse(50)
        output_lines.append("[FlashKey] RST 脉冲已发出")

        # Wait for flash tool to finish
        remaining = flash_timeout - 30
        try:
            proc.wait(timeout=max(remaining, 0) or flash_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            reader.join(timeout=2)
            return False, output_lines + [f"[错误] 烧录超时 ({flash_timeout} 秒)"]

        reader.join(timeout=3)

        # Collect any remaining stderr
        try:
            stderr_data = proc.stderr.read()
            if stderr_data:
                output_lines.append(stderr_data)
        except Exception:
            pass

        success = proc.returncode == 0
        return success, output_lines

    except Exception as exc:
        logger.exception("Break mode internal error: %s", exc)
        return False, output_lines + [f"[错误] 烧录异常: {exc}"]


# ── Chip → default mode ──────────────────────────────────────────────

_FLASH_DEFAULT_MODE: dict[str, str] = {
    # BL602: always tool-first (flash tool runs first, RST pulse on prompt).
    # BL616/BL618: BOOT+RST first, then flash tool.
    "bl602": "break",
    "bl616": "isp",
    "bl618": "isp",
}


def _tool_flash(
    firmware_path: str,
    flash_port: str,
    chip: str = "bl616",
    baud_rate: int = 2000000,
    tool: str = "",
    sdk_path: str = "",
    mode: str = "",
) -> dict:
    """Single-call flash workflow.

    Two modes are supported:

    **break** (default for BL602) — serial break / 串口打断:
        Run flash tool → wait for "please reset" prompt →
        RST pulse → wait for completion → recovery.

    **isp** (default for BL616/BL618):
        BOOT↑ → RST pulse → run flash tool → RST → BOOT↓.

    FK-01 handles BOOT/RST timing.  The actual firmware write is delegated
    to an external tool::

        BL602:  ``make -C <sdk_path> flash p=<port> b=<baud>``
        BL616:  ``make -C <sdk_path> flash CHIP=bl616 COMX=<port> BAUDRATE=<baud_rate>``
        BL618:  same as BL616 with CHIP=bl618

    This is a **blocking** call.  Depending on firmware size, it may
    take 10–120 seconds.
    """
    global _flash_active_port, _flash_cleanup_needed, _flash_cleanup_dm

    # -- Validate params early ─────────────────────────────────────
    if not mode:
        mode = _FLASH_DEFAULT_MODE.get(chip, "isp")

    if mode not in ("break", "isp"):
        raise ToolError(f"不支持的烧录模式: {mode}。可选: break, isp")

    # Reject FK-01 control port — must use fk_log (WCH-LinkE VCP)
    _validate_flash_port(flash_port)

    fw_path = Path(firmware_path).expanduser().resolve()
    if not fw_path.is_file():
        raise ToolError(f"固件文件不存在: {firmware_path}")

    dm, fk = _require_fk()

    # -- Resolve flash tool command ----------------------------------
    flash_cmd = _resolve_flash_tool(chip, tool, sdk_path, flash_port, baud_rate, fw_path)

    # -- Acquire flash lock (mutual exclusion with flashkey_log) ------
    if not _flash_lock.acquire(blocking=False):
        raise ToolError("烧录进行中，请等待当前烧录完成后再试")

    _flash_active_port = flash_port
    start_time = time.monotonic()
    output_lines: list[str] = []

    # ── BREAK mode (BL602 serial interrupt) ──────────────────────────
    if mode == "break":
        _flash_cleanup_needed = True
        _flash_cleanup_dm = dm

        try:
            success, output_lines = _flash_break_mode(fk, flash_cmd, sdk_path)
        finally:
            _flash_cleanup_needed = False
            try:
                # RST 引脚应连接到 BL602 CHIP_EN — 烧录完成后复位使芯片正常启动
                fk.commands.rst_pulse(50)
            except Exception as exc:
                logger.error("Target recovery failed: %s", exc)
                output_lines.append(f"[警告] 目标芯片复位失败: {exc}")
            _flash_active_port = ""
            _flash_lock.release()

        duration = time.monotonic() - start_time
        return {
            "success": success,
            "output": "\n".join(output_lines),
            "duration": round(duration, 1),
            "chip": chip,
            "mode": mode,
        }

    # ── BL602 with mode=isp (still uses serial break, same as above) ──
    if chip == "bl602":
        _flash_cleanup_needed = True
        _flash_cleanup_dm = dm

        try:
            success, output_lines = _flash_break_mode(fk, flash_cmd, sdk_path)
        finally:
            _flash_cleanup_needed = False
            try:
                fk.commands.rst_pulse(50)
            except Exception as exc:
                logger.error("Target recovery failed: %s", exc)
                output_lines.append(f"[警告] 目标芯片复位失败: {exc}")
            _flash_active_port = ""
            _flash_lock.release()

        duration = time.monotonic() - start_time
        return {
            "success": success,
            "output": "\n".join(output_lines),
            "duration": round(duration, 1),
            "chip": chip,
            "mode": mode,
        }

    # ── ISP mode (BL616/BL618) ────────────────────────────────────────
    try:
        # Enter bootloader mode: BOOT=HIGH + RST pulse before flash tool
        fk.commands.boot_set(True)
        fk.commands.rst_pulse(50)
        time.sleep(0.2)  # ISP mode settling time

        # -- Run external flash tool -----------------------------------
        logger.info("Flashing %s (ISP): %s", chip, " ".join(flash_cmd))

        _flash_cleanup_needed = True
        _flash_cleanup_dm = dm

        try:
            proc = subprocess.run(
                flash_cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=sdk_path if sdk_path else None,
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
        # -- ALWAYS recover target -----------------------------------
        _flash_cleanup_needed = False
        try:
            fk.commands.rst_pulse(50)
            fk.commands.boot_set(False)
        except Exception as exc:
            logger.error("Target recovery failed: %s", exc)
            output_lines.append(f"[警告] 目标芯片复位失败: {exc}")
        _flash_active_port = ""
        _flash_lock.release()

    duration = time.monotonic() - start_time
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
    "bl616": 2000000,
    "bl618": 2000000,
}

_FLASH_MAKE_ARGS_MAP: dict[str, str] = {
    "bl602": "p={port} b={baud}",
    "bl616": "CHIP=bl616 COMX={port} BAUDRATE={baud}",
    "bl618": "CHIP=bl618 COMX={port} BAUDRATE={baud}",
}


def _resolve_flash_tool(
    chip: str,
    tool: str,
    sdk_path: str,
    flash_port: str,
    baud_rate: int,
    fw_path: Path,
) -> list[str]:
    """Resolve the flash tool command for the target chip.

    Priority:
    1. User-supplied ``tool`` (run as-is with args substitued)
    2. ``make flash`` from SDK (if ``sdk_path`` is set)
    3. ``make flash`` from current directory (if Makefile has 'flash' target)
    4. Error with install instructions
    """
    supported = sorted(_FLASH_MAKE_ARGS_MAP.keys())
    if chip not in _FLASH_MAKE_ARGS_MAP:
        raise ToolError(
            f"不支持的芯片类型: {chip}。当前支持: {', '.join(supported)}"
        )

    # -- 1. User-supplied custom tool ---------------------------------
    if tool:
        return _build_custom_cmd(tool, chip, flash_port, baud_rate, fw_path)

    # -- 2. make flash from SDK ---------------------------------------
    make_dir = sdk_path or "."
    makefile = Path(make_dir) / "Makefile"

    if makefile.is_file():
        # Verify the Makefile has a 'flash' target
        try:
            result = subprocess.run(
                ["make", "-C", make_dir, "-n", "flash"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 2:  # 2 = no such target
                args_tpl = _FLASH_MAKE_ARGS_MAP[chip]
                args_str = args_tpl.format(port=flash_port, baud=baud_rate)
                return ["make", "-C", make_dir, "flash"] + args_str.split()
        except Exception:
            pass

    # -- 3. No tool found → error with instructions ------------------
    if chip == "bl602":
        raise ToolError(
            "未找到 BL602 烧录工具。请克隆 Ai-Thinker-WB2 SDK 并设置 sdk_path，"
            "或通过 tool 参数指定烧录命令。\n"
            "SDK: https://github.com/Ai-Thinker-Open/Ai-Thinker-WB2"
        )
    else:
        raise ToolError(
            f"未找到 {chip.upper()} 烧录工具。请克隆 Bouffalo SDK 并设置 sdk_path，"
            f"或通过 tool 参数指定烧录命令。\n"
            "SDK: https://github.com/bouffalolab/bouffalo_sdk"
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
# flashkey_log (NEW) — 需求四
# ======================================================================

def _tool_log(
    port: str,
    baud_rate: int = 115200,
    duration: int = 2,
    max_lines: int = 50,
    grep: str | None = None,
) -> dict:
    """Capture serial log output from the target chip.

    Opens *port* (the WCH-LinkE VCP used for flashing/logging),
    reads for *duration* seconds, optionally filters with *grep*, and
    truncates to *max_lines* lines.
    """
    import serial as pyserial

    # Reject FK-01 control port
    _validate_flash_port(port)

    # Mutual exclusion with flashkey_flash on the same port
    if _flash_lock.locked() and _flash_active_port == port:
        raise ToolError("烧录进行中，串口正忙，请等待烧录完成")

    duration = min(max(duration, 1), 30)  # clamp 1–30 s (NFR-4)
    max_lines = max(max_lines, 1)

    actual_duration: float = 0.0
    lines: list[str] = []

    try:
        ser = pyserial.Serial(port=port, baudrate=baud_rate, timeout=0.1)
    except Exception as exc:
        raise ToolError(f"无法打开串口 {port}: {exc}")

    try:
        ser.reset_input_buffer()
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            try:
                data = ser.readline()
            except Exception:
                break
            if data:
                try:
                    line = data.decode("utf-8", errors="replace").rstrip("\r\n")
                except Exception:
                    line = str(data)
                lines.append(line)
        actual_duration = duration
    finally:
        ser.close()

    # Apply grep filter (case-insensitive substring match)
    if grep and grep.strip():
        grep_lower = grep.strip().lower()
        lines = [ln for ln in lines if grep_lower in ln.lower()]

    # Truncate to max_lines (filter first, then take last N)
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[-max_lines:]

    content = "\n".join(lines) if lines else "(无日志输出)"

    return {
        "lines": len(lines),
        "duration": round(actual_duration, 1),
        "truncated": truncated,
        "content": content,
    }


# ======================================================================
# flashkey_send (NEW) — 串口数据发送
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

    # Mutual exclusion with flashkey_flash on the same port
    if _flash_lock.locked() and _flash_active_port == port:
        raise ToolError("烧录进行中，串口正忙，请等待烧录完成")

    # Decode data based on encoding
    if encoding == "text":
        # unicode_escape interprets literal \n \r \t \\ etc. as control chars,
        # while leaving already-decoded control chars from JSON unchanged.
        raw = data.encode("utf-8").decode("unicode_escape").encode("latin-1")
    elif encoding == "hex":
        hex_str = data.replace(" ", "").replace("\n", "").replace("\t", "")
        if len(hex_str) % 2 != 0:
            raise ToolError("hex 编码数据长度必须为偶数")
        try:
            raw = bytes.fromhex(hex_str)
        except ValueError as exc:
            raise ToolError(f"hex 解码失败: {exc}")
    else:
        raise ToolError(f"不支持的编码: {encoding}。可选: text, hex")

    if not raw:
        raise ToolError("发送数据不能为空")

    # Clamp read_timeout
    read_timeout = min(max(read_timeout, 0.1), 10.0)

    response_lines: list[str] = []
    actual_sent: int = 0

    try:
        ser = pyserial.Serial(port=port, baudrate=baud_rate, timeout=0.1)
    except Exception as exc:
        raise ToolError(f"无法打开串口 {port}: {exc}")

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

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

mcp = FastMCP(
    name="flashkey-mcp",
    instructions="MCP server for FlashKey FK-01 — AI-native USB programmer & debugger.  "
    "Plug in FK-01 for automatic handshake; use flashkey_status() to check state.",
)

# ── Register 19 tools ───────────────────────────────────────────────
# Note: each tool function's signature is used by FastMCP to generate
# JSON Schema.  Only bool / int / str / float / Optional[str] types
# are allowed — no custom class arguments.

# Status & discovery (no auth required)
mcp.add_tool(
    _tool_wrapper(_tool_status, require_auth=False),
    name="flashkey_status",
    description=(
        "查询 FlashKey FK-01 统一状态。不需要认证，始终可调用。"
        "返回认证状态(authed)、固件版本(version)、引脚状态(boot/rst/v5v/v3v3)。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_list_ports, require_auth=False),
    name="flashkey_list_ports",
    description=(
        "列出系统所有可用串口。每项包含 port、description、VID、PID、role。\n"
        "role=fk_control → FK-01 主控口 (MCP 内部使用，不能用于烧录/日志)\n"
        "role=fk_log     → WCH-LinkE VCP (FK-01 v0.1.1 日志/烧录口，最高 921600)\n"
        "role=unknown    → 其他设备\n"
        "烧录或采集日志前，务必先调用此工具确认端口 role。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_module_info, require_auth=False),
    name="flashkey_module_info",
    description=(
        "查询 FlashKey 扩展模块状态（无需认证）。"
        "返回模块是否在线(present)、模块身份(module)、已注册的 mod_* 动态工具列表(tools)、"
        "以及模块自主上报数据的统计(data)。"
    ),
)

# Communication
mcp.add_tool(
    _tool_wrapper(_tool_ping),
    name="flashkey_ping",
    description="Ping FlashKey 设备并返回 magic 标识字符串。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_auth_status),
    name="flashkey_auth_status",
    description="查询 FK-01 认证状态。⚠️ 已弃用(DEPRECATED)，建议使用 flashkey_status()。需要认证。",
)

# GPIO control
mcp.add_tool(
    _tool_wrapper(_tool_boot_set),
    name="flashkey_boot_set",
    description="设置 BOOT 引脚 (PB3) 高(value=True) 或低(value=False)。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_boot_get),
    name="flashkey_boot_get",
    description="读取 BOOT 引脚 (PB3) 当前状态。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_rst_set),
    name="flashkey_rst_set",
    description="设置 RST 引脚 (PB4) 高(value=True) 或低(value=False)。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_rst_get),
    name="flashkey_rst_get",
    description="读取 RST 引脚 (PB4) 当前状态。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_rst_pulse),
    name="flashkey_rst_pulse",
    description="在 RST 引脚上产生指定毫秒(ms)的负脉冲，默认 50ms。需要认证。",
)

# Power control
mcp.add_tool(
    _tool_wrapper(_tool_v5v_set),
    name="flashkey_v5v_set",
    description="控制 5V 电源输出 (PB1, 低电平有效)，value=True 开启。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_v5v_get),
    name="flashkey_v5v_get",
    description="读取 5V 电源当前状态。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_vusb_set),
    name="flashkey_vusb_set",
    description=(
        "控制外置 USB-A 电源输出 (PA0, 低电平有效)：value=True 拉低 PA0 = 开启/启动，"
        "value=False 拉高 PA0 = 关闭。默认关闭。需要认证。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_vusb_get),
    name="flashkey_vusb_get",
    description="读取外置 USB-A 电源当前状态 (True=开启/PA0低, False=关闭/PA0高)。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_v3v3_set),
    name="flashkey_v3v3_set",
    description="控制 3.3V 电源输出 (PB0, 高电平有效)，value=True 开启。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_v3v3_get),
    name="flashkey_v3v3_get",
    description="读取 3.3V 电源当前状态。需要认证。",
)

# ── flashkey_flash_monitor ─────────────────────────────────────────

def _tool_flash_monitor(
    command: str,
    sdk_path: str = "",
    flash_timeout: int = 120,
) -> dict:
    """Run a flash command, monitor stdout for 'Please Press Reset Key!',
    pulse FK-01 RST to trigger bootloader, wait for completion.

    This is the low-level building block for BL602 serial break mode.
    The command runs in a subprocess, FK-01 watches stdout for the reset
    prompt, then pulses RST at the right moment.

    Args:
        command: Shell command to run (e.g. 'make -C /path flash p=/dev/ttyUSB0 b=921600')
        sdk_path: Working directory for the command
        flash_timeout: Max seconds to wait (default 120)
    """
    dm, fk = _require_fk()

    # Acquire flash lock
    if not _flash_lock.acquire(blocking=False):
        raise ToolError("烧录进行中，请等待当前烧录完成后再试")

    global _flash_active_port, _flash_cleanup_needed, _flash_cleanup_dm
    _flash_cleanup_needed = True
    _flash_cleanup_dm = dm

    try:
        flash_cmd = command.split()
        success, output_lines = _flash_break_mode(fk, flash_cmd, sdk_path, flash_timeout)
    finally:
        _flash_cleanup_needed = False
        try:
            fk.commands.rst_pulse(50)
        except Exception as exc:
            output_lines.append(f"[警告] 复位失败: {exc}")
        _flash_lock.release()

    return {
        "success": success,
        "output": "\n".join(output_lines),
    }


mcp.add_tool(
    _tool_wrapper(_tool_flash_monitor),
    name="flashkey_flash_monitor",
    description=(
        "🔍 运行烧录命令并监控输出，检测到复位提示时自动通过 FK-01 RST 引脚复位芯片。\n"
        "用于 BL602 串口打断烧录模式：make flash 先发 sync 信号，然后打印复位提示等待用户复位，\n"
        "此工具自动检测提示并发送 RST 脉冲，烧录完成后再次复位使芯片正常启动。\n"
        "参数:\n"
        "  command: 烧录命令 (如 'make -C /path flash p=/dev/ttyUSB0 b=921600')\n"
        "  sdk_path: 命令执行的工作目录\n"
        "  flash_timeout: 超时秒数，默认 120\n"
        "返回: success(是否成功)、output(命令完整输出)\n"
        "需要认证。"
    ),
)

# Version & UID
mcp.add_tool(
    _tool_wrapper(_tool_get_version),
    name="flashkey_get_version",
    description="读取 FK-01 固件版本号 (如 '0.1.1')。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_get_uid),
    name="flashkey_get_uid",
    description="读取 FK-01 设备唯一 ID (16 字符 hex 字符串)。需要认证。",
)
mcp.add_tool(
    _tool_wrapper(_tool_get_events, require_auth=False),
    name="flashkey_get_events",
    description=(
        "读取服务器已记录的 FlashKey 事件（如用户手动操作 PB8/PB9 按键），"
        "每条包含事件名、按键、动作和操作时间戳。无需认证。"
    ),
)

# Deprecated (replaced by flashkey_status)
mcp.add_tool(
    _tool_wrapper(_tool_get_status),
    name="flashkey_get_status",
    description="读取引脚状态。⚠️ 已弃用(DEPRECATED)，建议使用 flashkey_status()。需要认证。",
)

# Convenience
mcp.add_tool(
    _tool_wrapper(_tool_enter_bootloader),
    name="flashkey_enter_bootloader",
    description=(
        "组合操作: BOOT 拉高 → RST 脉冲 → 目标芯片进入烧录模式。"
        "等效于 boot_set(True) + rst_pulse()。需要认证。"
    ),
)

# ── NEW tools ───────────────────────────────────────────────────────

mcp.add_tool(
    _tool_wrapper(_tool_flash),
    name="flashkey_flash",
    description=(
        "⚡ 一键烧录固件到目标芯片 (阻塞操作，耗时 10-120 秒)。\n"
        "\n"
        "⚠️ 端口选择：先用 flashkey_list_ports() 查看端口列表，选择 role=fk_log (WCH-LinkE VCP) 的端口。\n"
        "绝对不能使用 role=fk_control 的端口（那是 FK-01 主控口，MCP 内部专用）。\n"
        "不要根据端口名猜测角色，不同系统上名字不同 (COMx / ttyACMx / ttyUSBx / cu.*)。\n"
        "注意：WCH-LinkE VCP (fk_log) 最高仅支持 921600，需要更高波特率时请用外接 USB-UART。\n"
        "\n"
        "支持两种烧录模式:\n"
        "  BL602: 串口打断模式 (BOOT 拉高 → make flash 通过 DTR 复位并握手 → 烧录完成)。\n"
        "         FK-01 只控制 BOOT，复位由串口桥 (WCH-LinkE VCP) 的 DTR 处理。\n"
        "         mode 参数对 BL602 无效。\n"
        "  BL616/BL618 (isp): BOOT↑ → RST 脉冲 → 烧录工具 → 恢复\n"
        "参数:\n"
        "  firmware_path: 固件文件绝对路径\n"
        "  flash_port: 烧录串口 — 必须选 flashkey_list_ports() 中 role=fk_log 的端口\n"
        "  chip: 芯片类型，支持 bl602/bl616/bl618\n"
        "  baud_rate: 烧录波特率 (bl602 默认 921600, bl616/bl618 默认 2000000)\n"
        "  tool: 可选，自定义烧录命令 (如 'make flash p={port} b={baud}' 占位符)\n"
        "  sdk_path: 可选，芯片 SDK 根目录 (用于 make flash)\n"
        "  mode: 烧录模式 (仅 BL616/BL618 有效，默认 isp)。BL602 忽略此参数，始终 tool-first。"
        "需要认证。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_log),
    name="flashkey_log",
    description=(
        "📋 采集目标芯片串口日志 (需要认证)。\n"
        "⚠️ 端口选择：先用 flashkey_list_ports() 查看端口列表，选择 role=fk_log (WCH-LinkE VCP) 的端口。绝对不能用 role=fk_control 的端口。\n"
        "参数:\n"
        "  port: 日志串口 — 必须选 flashkey_list_ports() 中 role=fk_log 的端口\n"
        "  baud_rate: 日志波特率，默认 115200\n"
        "  duration: 采集时长(秒)，默认 2，最大 30\n"
        "  max_lines: 返回最大行数，grep 过滤后截取，默认 50\n"
        "  grep: 过滤关键词(子串匹配，不区分大小写)，None 表示不过滤\n"
        "返回: lines(实际行数)、duration(采集时长)、truncated(是否截断)、content(日志文本)\n"
        "与 flashkey_flash 互斥，串口忙时返回 isError。"
    ),
)
mcp.add_tool(
    _tool_wrapper(_tool_send),
    name="flashkey_send",
    description=(
        "📤 向目标芯片发送串口数据 (需要认证)。\n"
        "⚠️ 端口选择：先用 flashkey_list_ports() 查看端口列表，选择 role=fk_log (WCH-LinkE VCP) 的端口。绝对不能使用 role=fk_control 的端口。\n"
        "参数:\n"
        "  port: 目标串口 — 必须选 flashkey_list_ports() 中 role=fk_log 的端口\n"
        "  data: 要发送的数据字符串\n"
        "  baud_rate: 波特率，默认 115200\n"
        "  encoding: 编码方式 — \"text\"(默认，支持 \\n \\r \\t 转义) 或 \"hex\"(十六进制，空格可选)\n"
        "  read_response: 发送后是否读取目标芯片的响应，默认 False\n"
        "  read_timeout: 读取响应的超时秒数，默认 1.0，最大 10.0\n"
        "返回: sent(发送字节数)、data(数据摘要)；若 read_response=True，还包含 response(响应文本)、response_lines(行数)\n"
        "与 flashkey_flash 互斥，串口忙时返回 isError。\n"
        "示例: flashkey_send(port=\"/dev/ttyUSB0\", data=\"AT\\r\\n\", read_response=True) 发送 AT 指令并读取响应"
    ),
)


# ── flashkey_firmware_check / flashkey_firmware_flash (CH32V203 self-update) ──

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


def _tool_firmware_flash(
    hex_path: str = "",
    confirm: bool = False,
    force: bool = False,
    dry_run: bool = False,
    timeout: int = firmware_tools.DEFAULT_FLASH_TIMEOUT_S,
) -> dict:
    """Flash the FK-01 CH32V203 firmware via WCH-LinkE (SDI)."""
    global _flash_active_port
    if not _flash_lock.acquire(blocking=False):
        raise ToolError("烧录/日志会话进行中，请等待当前操作完成后再试")
    _flash_active_port = "<fk203-swd>"
    try:
        return firmware_tools.flash_ch32v203(
            hex_path=hex_path,
            confirm=confirm,
            force=force,
            dry_run=dry_run,
            timeout=timeout,
            get_version_fn=_read_device_version,
        )
    finally:
        _flash_active_port = ""
        _flash_lock.release()


mcp.add_tool(
    _tool_wrapper(_tool_firmware_check, require_auth=False),
    name="flashkey_firmware_check",
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
    name="flashkey_firmware_flash",
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
        "output_summary、duration_s；dry_run 时含 commands。"
    ),
)


# ======================================================================
# Entry point
# ======================================================================

def _handle_upgrade() -> None:
    """Upgrade flashkey-mcp to latest version from GitHub."""
    from flashkey_mcp import __version__

    print(f"Current version: {__version__}")
    print("Upgrading from GitHub...")
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "--upgrade",
            "git+https://github.com/Ai-Thinker-Open/flashkey-mcp.git",
        ],
        capture_output=False,
    )
    if result.returncode != 0:
        print("Upgrade failed. Try manually:")
        print("  pip install --upgrade git+https://github.com/Ai-Thinker-Open/flashkey-mcp.git")
        sys.exit(1)

    print("Upgrade complete. Restarting service...")
    subprocess.run(["systemctl", "--user", "restart", "flashkey-mcp"], capture_output=True)
    print("Service restarted. Check status: flashkey-mcp --service status")


def _handle_service_command(action: str) -> None:
    """Install / uninstall / check status of systemd user service."""
    import shutil
    import subprocess as _sp

    service_name = "flashkey-mcp"
    unit_file = Path(__file__).resolve().parent.parent.parent / "configs" / f"{service_name}.service"
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

    Defaults to stdio transport.  Pass ``--sse`` for HTTP SSE mode
    (requires ``pip install flashkey-mcp[sse]``).

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
        "--sse", action="store_true",
        help="Run in SSE (HTTP) mode instead of default stdio",
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
        help="Run in stdio mode (this is the default)",
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

    if args.sse:
        # ── SSE mode ────────────────────────────────────────────────
        logger.info("Transport: SSE (HTTP) on %s:%d", args.host, args.port)
        _run_sse(args.host, args.port)
    else:
        # ── Stdio mode (default) ────────────────────────────────────
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
            "SSE mode requires extra dependencies.  "
            "Install with: pip install flashkey-mcp[sse]"
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
    app = Starlette(
        routes=[
            Route("/release", endpoint=handle_release, methods=["POST"]),
            Route("/reconnect", endpoint=handle_reconnect, methods=["POST"]),
            Mount("/", app=sse_app),
        ],
    )

    logger.info("Starting FlashKey MCP SSE server at http://%s:%d", host, port)
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        if _dm is not None:
            _dm.stop()


if __name__ == "__main__":
    main()
