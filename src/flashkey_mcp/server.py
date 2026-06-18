"""FlashKey FK-01 MCP HTTP SSE Server.

Registers 15 tools covering all FlashKeyCommands plus a convenience
``flashkey_enter_bootloader`` tool (handshake is automatic via HELLO).

Usage::

    flashkey-mcp
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
from typing import Any

from flashkey_mcp import FlashKey, find_port
from flashkey_mcp.commands import CMD_HELLO, STATUS_BIT_BOOT
from flashkey_mcp.protocol import FrameParser

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8100

# ── Singleton / lazy-connection helpers ────────────────────────────────

_fk: FlashKey | None = None
_authed: bool = False
_fk_lock: threading.RLock = threading.RLock()


def _get_device() -> FlashKey:
    """Return the singleton ``FlashKey`` instance, connecting on first call."""
    global _fk
    with _fk_lock:
        if _fk is None:
            info = find_port()
            if info is None:
                raise RuntimeError(
                    "No FlashKey device found. "
                    "Please connect the FK-01 hardware and try again."
                )
            _fk = FlashKey(port=info["port"])
        return _fk


def _require_authed() -> None:
    """Raise ``RuntimeError`` if the device has not completed auth handshake."""
    if not _authed:
        raise RuntimeError(
            "Not authenticated — device handshake not completed."
        )


def _clear_auth() -> None:
    """Clear the authentication flag (e.g. on connection loss)."""
    global _authed
    _authed = False


def _check_connection() -> bool:
    """Check if the device connection is still alive by testing the transport.

    Returns ``True`` if the device responds to a PING, ``False`` otherwise.
    """
    global _fk
    if _fk is None:
        return False
    try:
        _fk.commands.ping()
        return True
    except Exception:
        _clear_auth()
        if _fk is not None:
            try:
                _fk.close()
            except Exception:
                pass
        _fk = None
        return False


# ── Tool implementations ───────────────────────────────────────────────

def tool_ping() -> dict[str, Any]:
    """Ping the device and return its magic string. Requires prior authentication."""
    _require_authed()
    fk = _get_device()
    return fk.commands.ping()


def tool_auth_status() -> dict[str, bool]:
    """Query the current authentication state on the device. Requires prior authentication."""
    _require_authed()
    fk = _get_device()
    return fk.commands.auth_status()


def tool_boot_set(value: bool) -> dict[str, str]:
    """Set the BOOT pin high (True) or low (False)."""
    _require_authed()
    fk = _get_device()
    fk.commands.boot_set(value)
    return {"result": "ok"}


def tool_boot_get() -> dict[str, bool]:
    """Read the current BOOT pin state."""
    _require_authed()
    fk = _get_device()
    return {"value": fk.commands.boot_get()}


def tool_rst_set(value: bool) -> dict[str, str]:
    """Set the RST (reset) pin high (True) or low (False)."""
    _require_authed()
    fk = _get_device()
    fk.commands.rst_set(value)
    return {"result": "ok"}


def tool_rst_get() -> dict[str, bool]:
    """Read the current RST pin state."""
    _require_authed()
    fk = _get_device()
    return {"value": fk.commands.rst_get()}


def tool_rst_pulse(ms: int = 50) -> dict[str, str]:
    """Generate a pulse on the RST pin.

    Args:
        ms: Pulse width in milliseconds (default 50).
    """
    _require_authed()
    fk = _get_device()
    fk.commands.rst_pulse(ms)
    return {"result": "ok"}


def tool_v5v_set(value: bool) -> dict[str, str]:
    """Set the 5V power output on (True) or off (False)."""
    _require_authed()
    fk = _get_device()
    fk.commands.v5v_set(value)
    return {"result": "ok"}


def tool_v5v_get() -> dict[str, bool]:
    """Read the current 5V power state."""
    _require_authed()
    fk = _get_device()
    return {"value": fk.commands.v5v_get()}


def tool_v3v3_set(value: bool) -> dict[str, str]:
    """Set the 3.3V power output on (True) or off (False)."""
    _require_authed()
    fk = _get_device()
    fk.commands.v3v3_set(value)
    return {"result": "ok"}


def tool_v3v3_get() -> dict[str, bool]:
    """Read the current 3.3V power state."""
    _require_authed()
    fk = _get_device()
    return {"value": fk.commands.v3v3_get()}


def tool_get_version() -> dict[str, str]:
    """Read the firmware version string (e.g. "1.0.0")."""
    _require_authed()
    fk = _get_device()
    return fk.commands.get_version()


def tool_get_uid() -> dict[str, str]:
    """Read the device unique identifier as a hex string."""
    _require_authed()
    fk = _get_device()
    return {"uid": fk.commands.get_uid()}


def tool_get_status() -> dict[str, int]:
    """Read combined device status (boot, rst, v5v, v3v3, authed)."""
    _require_authed()
    fk = _get_device()
    return fk.commands.get_status()  # type: ignore[return-value]


def tool_enter_bootloader() -> dict[str, str]:
    """Set BOOT high then pulse RST to enter the bootloader.

    This is a convenience shortcut equivalent to calling
    ``boot_set(True)`` followed by ``rst_pulse()``.
    """
    _require_authed()
    fk = _get_device()
    fk.commands.boot_set(True)
    fk.commands.rst_pulse()
    return {"result": "ok"}


def _wrap_tool(fn: Any) -> Any:
    """Wrap a tool function so that common errors return JSON-able messages
    instead of crashing the MCP server.

    Also monitors connection health: if a transport error occurs,
    the authentication flag is cleared and the connection is reset.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = fn(*args, **kwargs)
            return result
        except RuntimeError as exc:
            return {"error": str(exc)}
        except (TimeoutError, OSError, IOError) as exc:
            global _fk
            _clear_auth()
            if _fk is not None:
                try:
                    _fk.close()
                except Exception:
                    pass
            _fk = None
            return {
                "error": (
                    "Device connection lost — authentication cleared. "
                    f"Please reconnect. ({exc})"
                )
            }
        except Exception as exc:
            logger.exception("Unhandled error in tool %s", fn.__name__)
            return {"error": f"Internal error: {exc}"}

    return wrapper


# ── MCP server setup ───────────────────────────────────────────────────

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(
    name="flashkey-mcp",
    instructions="MCP server for FlashKey FK-01 hardware debug tool.",
)

# Register all 15 tools with descriptions and parameter schemas.

mcp.add_tool(_wrap_tool(tool_ping), name="flashkey_ping",
    description="Ping the FlashKey device and return its magic identifier string. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_auth_status), name="flashkey_auth_status",
    description="Query whether the device is currently authenticated. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_boot_set), name="flashkey_boot_set",
    description="Set the BOOT pin high (True) or low (False). Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_boot_get), name="flashkey_boot_get",
    description="Read the current BOOT pin state. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_rst_set), name="flashkey_rst_set",
    description="Set the RST (reset) pin high (True) or low (False). Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_rst_get), name="flashkey_rst_get",
    description="Read the current RST pin state. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_rst_pulse), name="flashkey_rst_pulse",
    description="Generate a pulse on the RST pin with configurable width in ms. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_v5v_set), name="flashkey_v5v_set",
    description="Enable or disable the 5V power output. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_v5v_get), name="flashkey_v5v_get",
    description="Read the current 5V power state. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_v3v3_set), name="flashkey_v3v3_set",
    description="Enable or disable the 3.3V power output. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_v3v3_get), name="flashkey_v3v3_get",
    description="Read the current 3.3V power state. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_get_version), name="flashkey_get_version",
    description="Read the firmware version string (e.g. '1.0.0'). Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_get_uid), name="flashkey_get_uid",
    description="Read the device unique identifier as a hex string. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_get_status), name="flashkey_get_status",
    description="Read the combined device status including boot, rst, v5v, v3v3 pin states and authentication status. Requires prior authentication.")
mcp.add_tool(_wrap_tool(tool_enter_bootloader), name="flashkey_enter_bootloader",
    description="Set BOOT pin high then pulse RST to enter the bootloader. Equivalent to boot_set(True) + rst_pulse(). Requires prior authentication.")


# ── HELLO-based handshake helpers ──────────────────────────────────────


def _wait_for_hello(fk: "FlashKey", timeout: float = 12) -> bool:
    """Listen for HELLO frames and perform challenge-response handshake.

    Args:
        fk: Open FlashKey device instance.
        timeout: Max seconds to wait for a HELLO frame.

    Returns:
        ``True`` if handshake succeeded, ``False`` otherwise.
    """
    parser = FrameParser()
    fk.transport._ser.reset_input_buffer()
    deadline = time.time() + timeout
    while time.time() < deadline:
        byte_data = fk.transport.read(1)
        if byte_data:
            result = parser.feed(byte_data[0])
            if result is not None:
                cmd, _data = result
                if cmd == CMD_HELLO:
                    try:
                        if fk.commands.handshake():
                            return True
                    except Exception:
                        pass
    return False


def _try_reconnect() -> None:
    """Re-detect the FlashKey device and re-authenticate (called from keepalive)."""
    global _fk, _authed
    try:
        new_info = find_port()
        if new_info:
            new_port = new_info["port"]
            logger.info("Reconnecting on port %s (%s %s)",
                         new_port, new_info.get("vendor", ""), new_info.get("model", ""))
            new_fk = FlashKey(port=new_port, timeout=0.1)
            _authed = _wait_for_hello(new_fk)
            if not _authed:
                _authed = new_fk.commands.handshake()
            with _fk_lock:
                if _authed:
                    # Close old connection before replacing
                    if _fk is not None:
                        try:
                            _fk.close()
                        except Exception:
                            pass
                    _fk = new_fk
                    logger.info("Reconnect + handshake succeeded")
                else:
                    new_fk.close()
                    logger.warning("Reconnect failed to authenticate")
        else:
            logger.warning("No FlashKey device found on any port")
    except Exception as exc:
        logger.warning("Reconnect failed: %s", exc)


def _keepalive() -> None:
    """Background thread: PING every 3 seconds to prevent heartbeat timeout.
    If PING fails, immediately try to reconnect.
    """
    global _fk, _authed
    # Give handshake time to settle before first PING
    time.sleep(0.5)  # allow handshake to settle before first PING
    while True:
        time.sleep(3)
        try:
            if _fk is not None:
                _fk.commands.ping()
            else:
                # No connection yet — try to find and connect
                _try_reconnect()
        except Exception as exc:
            logger.warning("Keepalive PING failed: %s", exc)
            _try_reconnect()


# ── Entry point ────────────────────────────────────────────────────────


def main() -> None:
    """Run the FlashKey MCP server as a background HTTP SSE service.

    On startup:
    1. Detect the FK-01 USB device
    2. Listen for HELLO frames and perform handshake
    3. Fall back to active handshake if no HELLO within 12s
    4. Start keepalive PING thread
    5. Serve MCP tools over HTTP SSE on port 8100
    """
    global _fk, _authed
    try:
        info = find_port()
        if info is None:
            raise RuntimeError("No FlashKey device found")
        logger.info("Device found: %s %s on %s (VID=%s PID=%s, S/N=%s)",
                     info.get("vendor", "?"),
                     info.get("model", "FlashKey-FK01"),
                     info["port"],
                     info.get("vid", "?"),
                     info.get("pid", "?"),
                     info.get("serial", "-"))
        fk = FlashKey(port=info["port"], timeout=0.1)
        _fk = fk

        _authed = _wait_for_hello(fk, timeout=4)
        if not _authed:
            logger.info("No HELLO within timeout — active handshake")
            try:
                _authed = fk.commands.handshake()
                if _authed:
                    logger.info("Active handshake succeeded")
                else:
                    logger.warning("Active handshake failed")
            except Exception as exc:
                logger.warning("Active handshake error: %s", exc)

        # Start keepalive thread
        t_keep = threading.Thread(target=_keepalive, daemon=True)
        t_keep.start()

    except Exception as exc:
        logger.warning("FlashKey device not available at startup: %s", exc)

    parser = argparse.ArgumentParser(description="FlashKey FK-01 MCP Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="HTTP SSE server port (default: %d)" % DEFAULT_PORT)
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1)")
    args = parser.parse_args()

    logger.info("Starting FlashKey MCP server at http://%s:%d (SSE)", args.host, args.port)
    import uvicorn
    app = mcp.sse_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
