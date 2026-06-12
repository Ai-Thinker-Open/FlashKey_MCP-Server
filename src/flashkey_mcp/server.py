"""FlashKey FK-01 MCP StdioServer.

Registers 16 tools covering all FlashKeyCommands plus a convenience
``flashkey_enter_bootloader`` tool.

Usage::

    python -m flashkey_mcp.server
"""

from __future__ import annotations

import logging
from typing import Any

from flashkey_mcp import FlashKey, find_port
from flashkey_mcp.commands import STATUS_BIT_BOOT

logger = logging.getLogger(__name__)

# ── Singleton / lazy-connection helpers ────────────────────────────────

_fk: FlashKey | None = None
_authed: bool = False
_fk_lock: Any = None  # lazily created threading.Lock


def _get_device() -> FlashKey:
    """Return the singleton ``FlashKey`` instance, connecting on first call."""
    global _fk, _fk_lock
    if _fk_lock is None:
        import threading
        _fk_lock = threading.Lock()
    if _fk is None:
        if find_port() is None:
            raise RuntimeError(
                "No FlashKey device found. "
                "Please connect the FK-01 hardware and try again."
            )
        _fk = FlashKey()
    return _fk


def _require_authed() -> None:
    """Raise ``RuntimeError`` if the device has not completed auth handshake."""
    if not _authed:
        raise RuntimeError(
            "Not authenticated — call flashkey_handshake first."
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
        # Connection lost — clear auth and close transport
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


def tool_handshake(key: str | None = None) -> dict[str, bool]:
    """Perform Challenge-Response authentication handshake.

    Args:
        key: Optional 8-byte key as a hex string (16 hex chars).
             Defaults to built-in standard key.
    """
    global _authed
    fk = _get_device()
    key_bytes: bytes | None = None
    if key is not None:
        try:
            key_bytes = bytes.fromhex(key)
        except ValueError:
            raise ValueError(
                "Invalid key format — expected 16-character hex string."
            )
        if len(key_bytes) != 8:
            raise ValueError(
                f"Key must be 8 bytes (16 hex chars), got {len(key_bytes)} bytes."
            )
    _authed = fk.commands.handshake(key_bytes)
    return {"authed": _authed}


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
    """Read the firmware version string (e.g. \"1.0.0\")."""
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
            # Handles "not found" and "not authenticated"
            return {"error": str(exc)}
        except (TimeoutError, OSError, IOError) as exc:
            # Connection lost — clear auth and reset transport
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
                    f"Please reconnect and call flashkey_handshake again. ({exc})"
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

# Register all 16 tools with descriptions and parameter schemas.

mcp.add_tool(
    _wrap_tool(tool_ping),
    name="flashkey_ping",
    description="Ping the FlashKey device and return its magic identifier string. Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_handshake),
    name="flashkey_handshake",
    description=(
        "Perform Challenge-Response authentication handshake with the device. "
        "Must be called before any GPIO/power/query command."
    ),
)

mcp.add_tool(
    _wrap_tool(tool_auth_status),
    name="flashkey_auth_status",
    description="Query whether the device is currently authenticated. Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_boot_set),
    name="flashkey_boot_set",
    description="Set the BOOT pin high (True) or low (False). Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_boot_get),
    name="flashkey_boot_get",
    description="Read the current BOOT pin state. Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_rst_set),
    name="flashkey_rst_set",
    description="Set the RST (reset) pin high (True) or low (False). Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_rst_get),
    name="flashkey_rst_get",
    description="Read the current RST pin state. Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_rst_pulse),
    name="flashkey_rst_pulse",
    description=(
        "Generate a pulse on the RST pin with configurable width in ms. "
        "Requires prior authentication."
    ),
)

mcp.add_tool(
    _wrap_tool(tool_v5v_set),
    name="flashkey_v5v_set",
    description="Enable or disable the 5V power output. Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_v5v_get),
    name="flashkey_v5v_get",
    description="Read the current 5V power state. Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_v3v3_set),
    name="flashkey_v3v3_set",
    description="Enable or disable the 3.3V power output. Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_v3v3_get),
    name="flashkey_v3v3_get",
    description="Read the current 3.3V power state. Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_get_version),
    name="flashkey_get_version",
    description="Read the firmware version string (e.g. '1.0.0'). Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_get_uid),
    name="flashkey_get_uid",
    description="Read the device unique identifier as a hex string. Requires prior authentication.",
)

mcp.add_tool(
    _wrap_tool(tool_get_status),
    name="flashkey_get_status",
    description=(
        "Read the combined device status including boot, rst, v5v, v3v3 pin "
        "states and authentication status. Requires prior authentication."
    ),
)

mcp.add_tool(
    _wrap_tool(tool_enter_bootloader),
    name="flashkey_enter_bootloader",
    description=(
        "Set BOOT pin high then pulse RST to enter the bootloader. "
        "Equivalent to boot_set(True) + rst_pulse(). Requires prior authentication."
    ),
)


# ── Entry point ────────────────────────────────────────────────────────

def main() -> None:
    """Run the FlashKey MCP stdio server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
