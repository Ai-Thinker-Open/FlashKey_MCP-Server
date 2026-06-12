"""FlashKey FK-01 frame protocol (CRC-8, framing, parser)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flashkey_mcp.transport import FlashKeyTransport

# Frame constants
SOF = 0x7E
EOF = 0x7F

# Parser states
_STATE_IDLE = 0
_STATE_SOF = 1
_STATE_LEN = 2
_STATE_CMD = 3
_STATE_DATA = 4
_STATE_EOF = 5
_STATE_CRC = 6


def crc8_dallas(data: bytes) -> int:
    """Compute CRC-8/MAXIM (poly 0x31, init 0x00) over *data*.

    This is the same CRC-8 used by Dallas/Maxim 1-Wire devices.
    """
    crc = 0x00
    poly = 0x31
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ poly
            else:
                crc <<= 1
            crc &= 0xFF
    return crc


def _crc8_dallas_table() -> list[int]:
    """Pre-compute CRC-8 lookup table (lazy)."""
    table: list[int] = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc <<= 1
            crc &= 0xFF
        table.append(crc)
    return table


# Pre-computed lookup table for fast CRC-8
_CRC_TABLE = _crc8_dallas_table()


def crc8_dallas_fast(data: bytes) -> int:
    """CRC-8/MAXIM computed via lookup table (faster)."""
    crc = 0x00
    for byte in data:
        crc = _CRC_TABLE[(crc ^ byte) & 0xFF]
    return crc


def build_frame(cmd: int, data: bytes = b"") -> bytes:
    """Build a FlashKey frame.

    Frame format::

        SOF=0x7E | LEN(=data_len+2) | CMD | DATA[N] | CRC-8(0x31) | EOF=0x7F

    Args:
        cmd: Command byte (0-255).
        data: Optional payload bytes.

    Returns:
        Complete frame as bytes.
    """
    length = len(data) + 2  # CMD(1) + CRC(1)
    payload = bytes([length, cmd]) + data
    crc = crc8_dallas(payload)
    return bytes([SOF, length, cmd]) + data + bytes([crc, EOF])


class FrameParser:
    """State-machine parser for FlashKey frames (feed parser).

    Usage::

        parser = FrameParser()
        for byte in incoming_bytes:
            result = parser.feed(byte)
            if result is not None:
                # complete frame received: (cmd, data)
                cmd, data = result
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset the parser to idle state."""
        self._state = _STATE_IDLE
        self._cmd: int = 0
        self._data: bytearray = bytearray()
        self._len: int = 0
        self._crc: int = 0
        self._idx: int = 0
        self._original_data: bytes = b""

    def feed(self, byte: int) -> tuple[int, bytes] | None:
        """Feed one byte into the parser.

        Args:
            byte: Incoming byte (0-255).

        Returns:
            ``(cmd, data)`` tuple when a complete valid frame is received,
            ``None`` otherwise.
        """
        if self._state == _STATE_IDLE:
            if byte == SOF:
                self._state = _STATE_SOF
            return None

        elif self._state == _STATE_SOF:
            # Byte is LEN
            if byte < 2:  # Minimum: CMD(1) + CRC(1)
                self.reset()
                return None
            self._len = byte
            self._state = _STATE_LEN
            return None

        elif self._state == _STATE_LEN:
            # Byte is CMD
            self._cmd = byte
            self._data = bytearray()
            self._idx = 0
            data_len = self._len - 2
            if data_len == 0:
                self._state = _STATE_CRC
            else:
                self._state = _STATE_DATA
            return None

        elif self._state == _STATE_DATA:
            self._data.append(byte)
            self._idx += 1
            if self._idx >= self._len - 2:
                self._state = _STATE_CRC
            return None

        elif self._state == _STATE_CRC:
            self._crc = byte
            # Save a copy of the data before any reset
            saved_data = bytes(self._data)
            # Build payload = LEN + CMD + DATA, verify CRC
            payload = bytes([self._len, self._cmd]) + saved_data
            expected_crc = crc8_dallas(payload)
            if self._crc != expected_crc:
                self.reset()
                return None
            # CRC passed, wait for EOF
            self._state = _STATE_EOF
            self._original_data = saved_data
            return None

        elif self._state == _STATE_EOF:
            self._state = _STATE_IDLE
            if byte == EOF:
                return (self._cmd, self._original_data)
            return None

        return None

    def feed_all(self, data: bytes) -> list[tuple[int, bytes]]:
        """Feed multiple bytes at once.

        Returns:
            List of ``(cmd, data)`` tuples for all complete frames found.
        """
        frames: list[tuple[int, bytes]] = []
        for byte in data:
            result = self.feed(byte)
            if result is not None:
                frames.append(result)
        return frames


def send_frame(
    transport: FlashKeyTransport,
    cmd: int,
    data: bytes = b"",
    read_timeout: float = 3,
) -> bytes:
    """Send a frame and read the response.

    Args:
        transport: An open ``FlashKeyTransport`` instance.
        cmd: Command byte.
        data: Optional payload.
        read_timeout: Read timeout in seconds.

    Returns:
        Response payload bytes (without frame header/trailer).

    Raises:
        TimeoutError: If no valid response frame is received.
    """
    frame = build_frame(cmd, data)
    transport.write(frame)

    parser = FrameParser()
    deadline = __import__("time").time() + read_timeout

    while __import__("time").time() < deadline:
        byte_data = transport.read(1)
        if not byte_data:
            # Timeout
            break
        result = parser.feed(byte_data[0])
        if result is not None:
            resp_cmd, resp_data = result
            return resp_data

    raise TimeoutError(
        f"No response received for command 0x{cmd:02X}"
    )
