"""USB CDC serial port discovery and communication for FlashKey FK-01."""

import glob
import os
import time

import serial
import serial.tools.list_ports


FLASHKEY_VID = 0x1A86
FLASHKEY_PID = 0xFE0D  # firmware usb_desc.c: idProduct = 0xFE0D


def list_all_ports() -> "list[dict]":
    """List all available serial ports with metadata.

    Returns:
        A list of dicts, each with keys ``port``, ``description``,
        ``vid``, ``pid``.
    """
    result: list[dict] = []
    for p in serial.tools.list_ports.comports():
        result.append({
            "port": p.device,
            "description": (p.description or "").strip(),
            "vid": f"{p.vid:04X}" if p.vid else "",
            "pid": f"{p.pid:04X}" if p.pid else "",
        })
    return result


def find_port() -> "dict | None":
    """Auto-discover the FlashKey FK-01 serial port and return device info.

    Scans all serial ports matching the FlashKey VID/PID, and verifies
    the device responds to a PING before returning. This prevents
    stale /dev/ttyACM* entries from being selected.

    Returns:
        A dict with keys ``port``, ``vendor``, ``model``, ``serial``,
        ``vid``, ``pid`` if a live device is found, else ``None``.
    """
    ports = serial.tools.list_ports.comports()

    def _try_port(p) -> "dict | None":
        """Try to open *p* and send a PING. Return device info dict on success."""
        vendor = getattr(p, "manufacturer", None) or getattr(p, "vendor", None) or ""
        model = getattr(p, "product", None) or getattr(p, "description", None) or ""
        serial_num = getattr(p, "serial_number", None) or ""
        info = {
            "port": p.device,
            "vid": f"0x{p.vid:04X}",
            "pid": f"0x{p.pid:04X}",
            "vendor": vendor.strip(),
            "model": model.strip(),
            "serial": serial_num.strip(),
        }
        try:
            ser = serial.Serial(port=p.device, baudrate=115200, timeout=0.3)
            try:
                # PING frame: SOF(0x7E) LEN(1) CMD(0x01) CRC(0xE8) EOF(0x7F)
                ser.write(bytes([0x7E, 0x02, 0x01, 0xE8, 0x7F]))
                time.sleep(0.15)
                resp = ser.read(64)
                if len(resp) >= 5 and resp[2] == 0x02:
                    return info
            finally:
                ser.close()
        except Exception:
            pass
        return None

    for p in ports:
        if p.vid == FLASHKEY_VID and p.pid == FLASHKEY_PID:
            result = _try_port(p)
            if result is not None:
                return result

    for path in sorted(glob.glob("/dev/ttyACM*")):
        if os.path.exists(path):
            from serial.tools.list_ports_linux import SysFS
            try:
                sysfs = SysFS(path)
                p = sysfs
            except Exception:
                p = type("obj", (object,), {
                    "device": path, "vid": 0, "pid": 0,
                    "manufacturer": "", "product": "", "serial_number": ""
                })()
            p.device = path
            result = _try_port(p)
            if result is not None:
                return result

    return None


class FlashKeyTransport:
    """Serial transport for communicating with a FlashKey FK-01 device."""

    def __init__(self, port: str, timeout: float = 0.1):
        import threading
        self._lock = threading.RLock()
        self._ser = serial.Serial(
            port=port,
            baudrate=115200,
            timeout=timeout,
            write_timeout=timeout,
        )

    def write(self, data: bytes) -> None:
        """Write raw bytes to the serial port."""
        with self._lock:
            self._ser.write(data)

    def read(self, n: int = 1) -> bytes:
        """Read up to *n* bytes from the serial port."""
        with self._lock:
            return self._ser.read(n)

    def transceive(self, tx: bytes, rx_n: int = 1) -> bytes:
        """Write then read atomically under a single lock."""
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(tx)
            return self._ser.read(rx_n)

    def reset_input_buffer(self) -> None:
        """Flush the serial input buffer."""
        with self._lock:
            self._ser.reset_input_buffer()

    def close(self) -> None:
        """Close the serial port."""
        if self._ser and self._ser.is_open:
            self._ser.close()

    @property
    def is_open(self) -> bool:
        return self._ser.is_open
