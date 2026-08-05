"""L1 tests for extension-module commands (0x60-0x64) — no hardware."""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flashkey_mcp.commands import (
    CMD_EVT_MODULE_DATA,
    CMD_MODULE_GET_INFO,
    CMD_MODULE_IO,
    CMD_MODULE_IOCTL,
    FlashKeyCommands,
    RSP_MODULE_INFO,
)
from flashkey_mcp.protocol import build_frame, FrameParser


class MockTransport:
    """Simulates a FlashKeyTransport with scripted incoming frames."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sent_frames: list[bytes] = []
        self._response_data: bytes = b""

    def write(self, data: bytes) -> None:
        self._sent_frames.append(data)

    def read(self, n: int = 1) -> bytes:
        chunk = self._response_data[:n]
        self._response_data = self._response_data[n:]
        return chunk

    def close(self) -> None:
        pass

    def inject_response(self, cmd: int, data: bytes = b"") -> None:
        self._response_data += build_frame(cmd, data)

    @property
    def last_frame(self) -> bytes | None:
        return self._sent_frames[-1] if self._sent_frames else None


class SlowTransport(MockTransport):
    """Transport whose read() blocks like pyserial (for window timeout tests)."""

    def __init__(self, idle_block: float = 0.05) -> None:
        super().__init__()
        self._idle_block = idle_block

    def read(self, n: int = 1) -> bytes:
        if not self._response_data:
            time.sleep(self._idle_block)
            return b""
        return super().read(n)


def parse_sent_frame(frame: bytes) -> tuple[int, bytes]:
    parser = FrameParser()
    results = parser.feed_all(frame)
    return results[0] if results else (0, b"")


def test_module_get_info_reassembles_fragments():
    t = MockTransport()
    cmd = FlashKeyCommands(t)

    manifest = b'{"module":{"name":"sensor"},"tools":[]}' * 3  # > 250B
    # fragment into [seq, more, data...] chunks of 250
    chunks = [manifest[i:i + 250] for i in range(0, len(manifest), 250)]
    for idx, chunk in enumerate(chunks):
        more = 0 if idx == len(chunks) - 1 else 1
        t.inject_response(RSP_MODULE_INFO, bytes([idx, more]) + chunk)

    result = cmd.module_get_info(read_timeout=1.0)
    assert result == manifest, f"Reassembly mismatch: {result!r}"

    # verify request frame
    rcmd, rdata = parse_sent_frame(t.last_frame)
    assert rcmd == CMD_MODULE_GET_INFO and rdata == b"", f"Got 0x{rcmd:02X} {rdata!r}"
    print("  MODULE_GET_INFO fragments ✅")


def test_module_get_info_empty_means_no_module():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(RSP_MODULE_INFO, b"")  # empty fragment = no module
    result = cmd.module_get_info(read_timeout=1.0)
    assert result is None, f"Expected None, got {result!r}"
    print("  MODULE_GET_INFO empty ✅")


def test_module_get_info_timeout():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    try:
        cmd.module_get_info(read_timeout=0.05)
        assert False, "Expected TimeoutError"
    except TimeoutError:
        pass
    print("  MODULE_GET_INFO timeout ✅")


def test_module_io_frame_and_window():
    t = MockTransport()
    cmd = FlashKeyCommands(t)

    payload = b'{"pin":3,"mode":"out"}'
    # module replies with two 0x63 events
    t.inject_response(CMD_EVT_MODULE_DATA, b"OK:" + payload)
    t.inject_response(CMD_EVT_MODULE_DATA, b"done")

    result = cmd.module_io(payload, window_ms=500)
    rcmd, rdata = parse_sent_frame(t.last_frame)
    assert rcmd == CMD_MODULE_IO
    assert rdata == payload
    assert result["sent"] == len(payload)
    assert result["chunks"] == [b"OK:" + payload, b"done"]
    assert result["timed_out"] is False
    print("  MODULE_IO window ✅")


def test_module_io_oversize_rejected():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    try:
        cmd.module_io(b"x" * 253)
        assert False, "Expected ValueError"
    except ValueError:
        pass
    print("  MODULE_IO oversize ✅")


def test_module_io_window_timeout_flag():
    t = SlowTransport(idle_block=0.03)
    cmd = FlashKeyCommands(t)
    result = cmd.module_io(b"ping", window_ms=120)
    assert result["sent"] == 4
    assert result["chunks"] == []
    assert result["timed_out"] is True
    print("  MODULE_IO timed_out ✅")


def test_module_ioctl_payload():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    cmd.module_ioctl(baud_rate=921600, i2c_addr=0x21, reset=True)
    rcmd, rdata = parse_sent_frame(t.last_frame)
    assert rcmd == CMD_MODULE_IOCTL
    assert rdata == bytes([0x00, 0x10, 0x0E, 0x00, 0x21, 0x01]), rdata.hex()
    print("  MODULE_IOCTL payload ✅")


if __name__ == "__main__":
    test_module_get_info_reassembles_fragments()
    test_module_get_info_empty_means_no_module()
    test_module_get_info_timeout()
    test_module_io_frame_and_window()
    test_module_io_oversize_rejected()
    test_module_io_window_timeout_flag()
    test_module_ioctl_payload()
    print("\nAll module command tests passed ✅")
