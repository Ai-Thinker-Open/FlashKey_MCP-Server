"""flashkey-mcp: FlashKey FK-01 MCP communication library."""

from __future__ import annotations

from flashkey_mcp._guard import _require_mcp_runtime
from flashkey_mcp.transport import FlashKeyTransport, find_port, list_all_ports
from flashkey_mcp.protocol import (
    build_frame,
    crc8_dallas,
    FrameParser,
    send_frame,
)
from flashkey_mcp.auth import SBOX, KEY, compute_response
from flashkey_mcp.commands import FlashKeyCommands

__all__ = [
    "FlashKeyTransport",
    "find_port",
    "list_all_ports",
    "crc8_dallas",
    "build_frame",
    "FrameParser",
    "send_frame",
    "compute_response",
    "FlashKeyCommands",
    "FlashKey",
]


class FlashKey:
    """High-level interface for FlashKey FK-01 device."""

    def __init__(self, port: str | None = None, timeout: float = 0.1):
        _require_mcp_runtime()
        if port is None:
            info = find_port()
            if info is None:
                raise RuntimeError("No FlashKey device found")
            port = info["port"]
            self.port_info = info
        else:
            self.port_info = {"port": port, "vid": "", "pid": "", "vendor": "", "model": ""}
        self.transport = FlashKeyTransport(port, timeout)
        self.commands = FlashKeyCommands(self.transport)

    def send_command(self, cmd: int, data: bytes = b"") -> bytes:
        return send_frame(self.transport, cmd, data)

    def close(self):
        self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
