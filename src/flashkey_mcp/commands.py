"""FlashKey FK-01 high-level command wrappers.

Provides the ``FlashKeyCommands`` class with all 15 device commands
plus the Challenge-Response handshake flow.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from flashkey_mcp.auth import KEY, compute_response
from flashkey_mcp.protocol import build_frame, FrameParser

if TYPE_CHECKING:
    from flashkey_mcp.transport import FlashKeyTransport

# ── Command bytes (host → device) ──────────────────────────────────────
CMD_PING: int = 0x01
CMD_HELLO: int = 0x02
CMD_CHALLENGE: int = 0x10
CMD_RESPONSE: int = 0x11
CMD_AUTH_STATUS: int = 0x12

CMD_BOOT_SET: int = 0x20
CMD_BOOT_GET: int = 0x21
CMD_RST_SET: int = 0x23
CMD_RST_GET: int = 0x24
CMD_RST_PULSE: int = 0x26

CMD_V5V_SET: int = 0x30
CMD_V5V_GET: int = 0x31
CMD_V3V3_SET: int = 0x33
CMD_V3V3_GET: int = 0x34

CMD_GET_VERSION: int = 0x40
CMD_GET_UID: int = 0x42
CMD_GET_STATUS: int = 0x44

# ── Response command bytes (device → host) ────────────────────────────
RSP_PONG: int = 0x02
RSP_AUTH_OK: int = 0x13
RSP_AUTH_FAIL: int = 0x14
RSP_BOOT_VAL: int = 0x22
RSP_RST_VAL: int = 0x25
RSP_V5V_VAL: int = 0x32
RSP_V3V3_VAL: int = 0x35
RSP_VERSION: int = 0x41
RSP_UID: int = 0x43
RSP_STATUS: int = 0x45

# Status bitfield positions (matches FK_GPIO_StatusAll)
STATUS_BIT_BOOT: int = 1 << 0
STATUS_BIT_RST: int = 1 << 1
STATUS_BIT_V5V: int = 1 << 2
STATUS_BIT_V3V3: int = 1 << 3

# Default timeout for fire-and-forget SET commands
_SET_TIMEOUT: float = 0.5
_DEFAULT_TIMEOUT: float = 3.0


class FlashKeyCommands:
    """High-level command interface for FlashKey FK-01.

    Wraps the raw frame protocol into typed Python methods.

    Args:
        transport: An open ``FlashKeyTransport`` instance.
    """

    def __init__(self, transport: FlashKeyTransport) -> None:
        self._transport = transport

    # ── Private helpers ────────────────────────────────────────────────────

    def _transceive(
        self,
        cmd: int,
        data: bytes = b"",
        read_timeout: float = _DEFAULT_TIMEOUT,
    ) -> tuple[int, bytes]:
        """Send a command frame and wait for a response.

        Args:
            cmd: Command byte.
            data: Optional payload bytes.
            read_timeout: Max seconds to wait for a response.

        Returns:
            ``(response_cmd, response_data)`` tuple.

        Raises:
            TimeoutError: If no valid response is received in time.
        """
        frame = build_frame(cmd, data)
        with self._transport._lock:
            self._transport.write(frame)
            parser = FrameParser()
            deadline = time.time() + read_timeout
            while time.time() < deadline:
                byte_data = self._transport.read(1)
                if not byte_data:
                    break
                result = parser.feed(byte_data[0])
                if result is not None:
                    return result  # (cmd, data)

        raise TimeoutError(
            f"No response received for command 0x{cmd:02X}"
        )

    def _send_only(self, cmd: int, data: bytes = b"") -> None:
        """Send a command frame without waiting for a response.

        Used for SET commands that do not generate a response on success.

        Args:
            cmd: Command byte.
            data: Optional payload bytes.
        """
        frame = build_frame(cmd, data)
        self._transport.write(frame)

    # ── Communication commands (3) ─────────────────────────────────────────

    def ping(self, read_timeout: float = 1.0) -> dict:
        """Send a PING and expect a PONG.

        Returns:
            ``{"ok": True, "magic": "FK-01!"}`` on success.

        Raises:
            TimeoutError: If no PONG is received.
        """
        _rsp_cmd, data = self._transceive(CMD_PING, read_timeout=read_timeout)
        magic = data[:6].decode("ascii", errors="replace")
        return {"ok": True, "magic": magic}

    def handshake(self, key: bytes | None = None) -> bool:
        """Perform a full Challenge-Response authentication handshake.

        Protocol:
            1. Generate a random 8-byte challenge.
            2. Send ``CHALLENGE`` → device computes and returns its response.
            3. Compute local response and send ``RESPONSE``.
            4. Device returns ``AUTH_OK`` (0x13) or ``AUTH_FAIL`` (0x14).

        Args:
            key: 8-byte secret key. Defaults to the standard ``KEY``.

        Returns:
            ``True`` if authentication succeeded, ``False`` otherwise.
        """
        if key is None:
            key = KEY

        # Step 1: generate random challenge
        challenge = os.urandom(8)

        # Step 2: send CHALLENGE, get device-computed response
        _rsp_cmd, dev_response = self._transceive(CMD_CHALLENGE, challenge)

        # Step 3: verify device response matches local computation
        local_response = compute_response(challenge, key)
        if dev_response != local_response:
            return False  # device returned wrong response

        # Step 4: send local response
        rsp_cmd, _rsp_data = self._transceive(CMD_RESPONSE, local_response)

        # Step 5: check result
        return rsp_cmd == RSP_AUTH_OK

    def auth_status(self) -> dict:
        """Query the current authentication state on the device.

        Returns:
            ``{"authed": True}`` if the device is authenticated,
            ``{"authed": False}`` otherwise.
        """
        _rsp_cmd, data = self._transceive(CMD_AUTH_STATUS)
        return {"authed": bool(data[0]) if data else False}

    # ── GPIO commands (6) ──────────────────────────────────────────────────

    def boot_set(self, value: bool) -> None:
        """Set the BOOT pin.

        Args:
            value: ``True`` for high, ``False`` for low.
        """
        self._send_only(CMD_BOOT_SET, bytes([1 if value else 0]))

    def boot_get(self) -> bool:
        """Read the current BOOT pin state.

        Returns:
            ``True`` if high, ``False`` if low.
        """
        _rsp_cmd, data = self._transceive(CMD_BOOT_GET)
        return bool(data[0]) if data else False

    def rst_set(self, value: bool) -> None:
        """Set the RST (reset) pin.

        Args:
            value: ``True`` for high, ``False`` for low.
        """
        self._send_only(CMD_RST_SET, bytes([1 if value else 0]))

    def rst_get(self) -> bool:
        """Read the current RST pin state.

        Returns:
            ``True`` if high, ``False`` if low.
        """
        _rsp_cmd, data = self._transceive(CMD_RST_GET)
        return bool(data[0]) if data else False

    def rst_pulse(self, ms: int = 50) -> None:
        """Generate a pulse on the RST pin.

        Args:
            ms: Pulse width in milliseconds (little-endian 2 bytes).
        """
        data = bytes([ms & 0xFF, (ms >> 8) & 0xFF])
        self._send_only(CMD_RST_PULSE, data)

    # ── Power commands (4) ─────────────────────────────────────────────────

    def v5v_set(self, value: bool) -> None:
        """Set the 5V power output.

        Args:
            value: ``True`` to enable, ``False`` to disable.
        """
        self._send_only(CMD_V5V_SET, bytes([1 if value else 0]))

    def v5v_get(self) -> bool:
        """Read the current 5V power state.

        Returns:
            ``True`` if enabled, ``False`` if disabled.
        """
        _rsp_cmd, data = self._transceive(CMD_V5V_GET)
        return bool(data[0]) if data else False

    def v3v3_set(self, value: bool) -> None:
        """Set the 3.3V power output.

        Args:
            value: ``True`` to enable, ``False`` to disable.
        """
        self._send_only(CMD_V3V3_SET, bytes([1 if value else 0]))

    def v3v3_get(self) -> bool:
        """Read the current 3.3V power state.

        Returns:
            ``True`` if enabled, ``False`` if disabled.
        """
        _rsp_cmd, data = self._transceive(CMD_V3V3_GET)
        return bool(data[0]) if data else False

    # ── Query commands (3) ─────────────────────────────────────────────────

    def get_version(self) -> dict:
        """Read the firmware version.

        The firmware returns 4 bytes ``[major, minor, patch, _reserved]``.

        Returns:
            ``{"version": "major.minor.patch"}``.
        """
        _rsp_cmd, data = self._transceive(CMD_GET_VERSION)
        if len(data) >= 3:
            version = f"{data[0]}.{data[1]}.{data[2]}"
        else:
            version = "0.0.0"
        return {"version": version}

    def get_uid(self) -> str:
        """Read the device unique identifier.

        The firmware returns 8 raw bytes from the MCU UID.

        Returns:
            16-character hex string (e.g. ``"a1b2c3d4e5f67890"``).
        """
        _rsp_cmd, data = self._transceive(CMD_GET_UID)
        return data.hex()

    def get_status(self) -> dict:
        """Read the combined device pin status from firmware.

        The firmware returns 3 bytes:
            ``[boot_value, rst_value, bitfield]``

        where the bitfield encodes:
            bit 0 = boot, bit 1 = rst, bit 2 = v5v, bit 3 = v3v3

        Note: ``authed`` is NOT included — the caller (DeviceManager)
        merges auth state locally to avoid an extra round-trip.

        Returns:
            A dict with keys ``boot``, ``rst``, ``v5v``, ``v3v3`` —
            each ``1`` or ``0``.
        """
        _rsp_cmd, data = self._transceive(CMD_GET_STATUS)

        boot = data[0] if len(data) > 0 else 0
        rst = data[1] if len(data) > 1 else 0
        bf = data[2] if len(data) > 2 else 0

        # Extract from bitfield (bits 2 and 3)
        v5v = 1 if (bf & STATUS_BIT_V5V) else 0
        v3v3 = 1 if (bf & STATUS_BIT_V3V3) else 0

        return {
            "boot": boot,
            "rst": rst,
            "v5v": v5v,
            "v3v3": v3v3,
        }
