"""USB CDC serial port discovery and communication for FlashKey FK-01."""

import sys
import glob
import os

import serial
import serial.tools.list_ports


FLASHKEY_VID = 0x1A86
FLASHKEY_PID = 0xFE0C


def find_port() -> "str | None":
    """Auto-discover the FlashKey FK-01 serial port.

    Returns:
        Port path (e.g. ``COM3`` or ``/dev/ttyACM0``) if found, else ``None``.
    """
    if sys.platform.startswith("win"):
        # Windows — scan by VID/PID
        ports = serial.tools.list_ports.comports()
        for p in ports:
            if p.vid == FLASHKEY_VID and p.pid == FLASHKEY_PID:
                return p.device
    else:
        # Linux / macOS — scan by VID/PID first, then fallback to glob
        ports = serial.tools.list_ports.comports()
        for p in ports:
            if p.vid == FLASHKEY_VID and p.pid == FLASHKEY_PID:
                return p.device
        # Fallback: direct glob (no VID/PID matching)
        for path in glob.glob("/dev/ttyACM*"):
            if os.path.exists(path):
                return path

    return None


class FlashKeyTransport:
    """Serial transport for communicating with a FlashKey FK-01 device."""

    def __init__(self, port: str, timeout: float = 3):
        self._ser = serial.Serial(
            port=port,
            baudrate=115200,
            timeout=timeout,
            write_timeout=timeout,
        )

    def write(self, data: bytes) -> None:
        """Write raw bytes to the serial port."""
        self._ser.write(data)

    def read(self, n: int = 1) -> bytes:
        """Read up to *n* bytes from the serial port."""
        return self._ser.read(n)

    def close(self) -> None:
        """Close the serial port."""
        if self._ser and self._ser.is_open:
            self._ser.close()

    @property
    def is_open(self) -> bool:
        return self._ser.is_open
