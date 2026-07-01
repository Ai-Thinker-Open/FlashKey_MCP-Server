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

logger = logging.getLogger(__name__)

# ── Port validation ───────────────────────────────────────────────────


def _validate_flash_port(port: str) -> None:
    """Raise ``ToolError`` if *port* is the FK-01 control port.

    FK-01 has two ports identified by VID/PID, not device name:
      - ``fk_control`` (VID=1A86, PID=FE0D) → FK-01 main controller, MCP only.
      - ``fk_flash``   (VID=1A86, PID=7523) → CH340C bridge, flash/log use this.

    Always use ``flashkey_list_ports()`` and match by ``role`` field.
    """
    import serial.tools.list_ports as _list_ports
    for p in _list_ports.comports():
        if p.device == port:
            if p.vid == FLASHKEY_VID and p.pid == FLASHKEY_PID:
                # Find the correct flash port to suggest
                flash_ports = [
                    pp.device for pp in _list_ports.comports()
                    if pp.vid == 0x1A86 and pp.pid == 0x7523
                ]
                hint = ""
                if flash_ports:
                    hint = f" 请改用 role=fk_flash 的烧录端口: {', '.join(flash_ports)}"
                raise ToolError(
                    f"{port} 是 FK-01 主控端口 (role=fk_control, MCP 内部专用)，"
                    f"不能用于烧录或日志。{hint}"
                )
            return  # port found, not FK-01 control — OK
    # Port not found in system — let the actual serial open fail naturally

# ── Singleton device manager ─────────────────────────────────────────
_dm: DeviceManager | None = None
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

    The flash tool (bflb_iot_tool) sends a sync pattern on CH340C TX, then
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

    # Ensure FK-01 GPIOs don't conflict with CH340C DTR/RTS control.
    # BOOT low = default, CH340C handles reset signalling via RTS.
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

    # Reject FK-01 control port — must use CH340C flash port
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

    Opens *port* (the same CH340C / USB-UART bridge used for flashing),
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
        "role=fk_flash   → CH340C 烧录口 (flashkey_flash / flashkey_log 用这个)\n"
        "role=unknown    → 其他设备\n"
        "烧录或采集日志前，务必先调用此工具确认端口 role。"
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
        "⚠️ 端口选择：先用 flashkey_list_ports() 查看端口列表，选择 role=fk_flash 的端口。\n"
        "绝对不能使用 role=fk_control 的端口（那是 FK-01 主控口，MCP 内部专用）。\n"
        "不要根据端口名猜测角色，不同系统上名字不同 (COMx / ttyACMx / ttyUSBx / cu.*)。\n"
        "\n"
        "支持两种烧录模式:\n"
        "  BL602: 串口打断模式 (BOOT 拉高 → make flash 通过 DTR 复位并握手 → 烧录完成)。\n"
        "         FK-01 只控制 BOOT，复位由 CH340C 的 DTR 处理。\n"
        "         mode 参数对 BL602 无效。\n"
        "  BL616/BL618 (isp): BOOT↑ → RST 脉冲 → 烧录工具 → 恢复\n"
        "参数:\n"
        "  firmware_path: 固件文件绝对路径\n"
        "  flash_port: 烧录串口 — 必须选 flashkey_list_ports() 中 role=fk_flash 的端口\n"
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
        "⚠️ 端口选择：先用 flashkey_list_ports() 查看端口列表，选择 role=fk_flash 的端口。绝对不能用 role=fk_control 的端口。\n"
        "参数:\n"
        "  port: 日志串口 — 必须选 flashkey_list_ports() 中 role=fk_flash 的端口 (与 flash_port 相同)\n"
        "  baud_rate: 日志波特率，默认 115200\n"
        "  duration: 采集时长(秒)，默认 2，最大 30\n"
        "  max_lines: 返回最大行数，grep 过滤后截取，默认 50\n"
        "  grep: 过滤关键词(子串匹配，不区分大小写)，None 表示不过滤\n"
        "返回: lines(实际行数)、duration(采集时长)、truncated(是否截断)、content(日志文本)\n"
        "与 flashkey_flash 互斥，串口忙时返回 isError。"
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
