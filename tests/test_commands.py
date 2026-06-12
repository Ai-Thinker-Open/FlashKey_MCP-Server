"""Test commands.py: public API and frame format verification (no hardware)."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flashkey_mcp.commands import FlashKeyCommands
from flashkey_mcp.protocol import build_frame, FrameParser


class MockTransport:
    """Simulates a FlashKeyTransport for testing command frame format."""

    def __init__(self):
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


def parse_sent_frame(frame: bytes) -> tuple[int, bytes]:
    """Parse a sent frame to get (cmd, data)."""
    parser = FrameParser()
    results = parser.feed_all(frame)
    if results:
        return results[0]
    return (0, b"")


def test_ping():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(0x02, b"FK-01!\x00\x00")
    result = cmd.ping()
    assert result == {"ok": True, "magic": "FK-01!"}, f"Got {result}"
    print("  PING ✅")


def test_handshake_success():
    import flashkey_mcp.auth as auth

    # Fix challenge to be deterministic so we can inject the matching response
    fixed_challenge = b"\x00\x01\x02\x03\x04\x05\x06\x07"
    original_urandom = os.urandom
    os.urandom = lambda n: fixed_challenge  # type: ignore[method-assign]
    try:
        t = MockTransport()
        cmd = FlashKeyCommands(t)

        # Device must return the correct response to our fixed challenge
        expected_dev_response = auth.compute_response(fixed_challenge, auth.KEY)
        t.inject_response(0x10, expected_dev_response)
        # Then device returns AUTH_OK after we send our RESPONSE
        t.inject_response(0x13, b"")

        result = cmd.handshake()
        assert result is True, f"Expected True, got {result}"

        # Verify frames were sent
        assert t.last_frame is not None
        rsp_cmd, rsp_data = parse_sent_frame(t.last_frame)
        assert rsp_cmd == 0x11, f"Expected RESPONSE cmd, got 0x{rsp_cmd:02X}"
        assert len(rsp_data) == 8, f"Expected 8-byte response, got {len(rsp_data)}"
        print("  HANDSHAKE success ✅")
    finally:
        os.urandom = original_urandom


def test_handshake_fail():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(0x10, b"\x22" * 8)
    t.inject_response(0x14, b"")
    result = cmd.handshake()
    assert result is False, f"Expected False, got {result}"
    print("  HANDSHAKE fail ✅")


def test_auth_status():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(0x13, b"\x01")
    result = cmd.auth_status()
    assert result == {"authed": True}, f"Got {result}"

    t2 = MockTransport()
    cmd2 = FlashKeyCommands(t2)
    t2.inject_response(0x14, b"\x00")
    result2 = cmd2.auth_status()
    assert result2 == {"authed": False}, f"Got {result2}"
    print("  AUTH_STATUS ✅")


def test_boot_set():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    cmd.boot_set(True)
    rsp_cmd, rsp_data = parse_sent_frame(t.last_frame)
    assert rsp_cmd == 0x20
    assert rsp_data == b"\x01"
    print("  BOOT_SET ✅")


def test_boot_get():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(0x22, b"\x01")
    result = cmd.boot_get()
    assert result is True
    assert t.last_frame is not None
    rsp_cmd, _ = parse_sent_frame(t.last_frame)
    assert rsp_cmd == 0x21
    print("  BOOT_GET ✅")


def test_rst_set():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    cmd.rst_set(False)
    rsp_cmd, rsp_data = parse_sent_frame(t.last_frame)
    assert rsp_cmd == 0x23
    assert rsp_data == b"\x00"
    print("  RST_SET ✅")


def test_rst_get():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(0x25, b"\x00")
    result = cmd.rst_get()
    assert result is False
    print("  RST_GET ✅")


def test_rst_pulse():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    cmd.rst_pulse(100)
    rsp_cmd, rsp_data = parse_sent_frame(t.last_frame)
    assert rsp_cmd == 0x26
    assert rsp_data == bytes([100 & 0xFF, (100 >> 8) & 0xFF])
    print("  RST_PULSE ✅")


def test_v5v_set():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    cmd.v5v_set(True)
    rsp_cmd, rsp_data = parse_sent_frame(t.last_frame)
    assert rsp_cmd == 0x30
    assert rsp_data == b"\x01"
    print("  V5V_SET ✅")


def test_v5v_get():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(0x32, b"\x01")
    result = cmd.v5v_get()
    assert result is True
    print("  V5V_GET ✅")


def test_v3v3_set():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    cmd.v3v3_set(False)
    rsp_cmd, rsp_data = parse_sent_frame(t.last_frame)
    assert rsp_cmd == 0x33
    assert rsp_data == b"\x00"
    print("  V3V3_SET ✅")


def test_v3v3_get():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(0x35, b"\x01")
    result = cmd.v3v3_get()
    assert result is True
    print("  V3V3_GET ✅")


def test_get_version():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(0x41, bytes([0x01, 0x02, 0x03, 0x00]))
    result = cmd.get_version()
    assert result == {"version": "1.2.3"}, f"Got {result}"
    print("  GET_VERSION ✅")


def test_get_uid():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    t.inject_response(0x43, bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]))
    result = cmd.get_uid()
    assert result == "123456789abcdef0", f"Got {result}"
    print("  GET_UID ✅")


def test_get_status():
    t = MockTransport()
    cmd = FlashKeyCommands(t)
    # status[3] = [boot=1, rst=0, bitfield: boot|v5v|v3v3 = 0b1101 = 0x0D]
    t.inject_response(0x45, bytes([0x01, 0x00, 0x0D]))
    # Need a second response for auth_status call inside get_status
    t.inject_response(0x13, b"\x01")
    result = cmd.get_status()
    assert result == {"boot": 1, "rst": 0, "v5v": 1, "v3v3": 1, "authed": 1}, f"Got {result}"
    print("  GET_STATUS ✅")


def test_15_methods_available():
    """Verify all 15 expected method names exist."""
    expected = {
        "ping", "handshake", "auth_status",
        "boot_set", "boot_get", "rst_set", "rst_get", "rst_pulse",
        "v5v_set", "v5v_get", "v3v3_set", "v3v3_get",
        "get_version", "get_uid", "get_status",
    }
    actual = {m for m in dir(FlashKeyCommands) if not m.startswith("_")}
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Missing methods: {missing}"
    assert not extra - {"get_status"}, f"Unexpected methods: {extra}"
    print(f"  All 15 methods present (got {len(actual)}) ✅")


if __name__ == "__main__":
    print("Running commands.py unit tests...\n")
    test_15_methods_available()
    test_ping()
    test_handshake_success()
    test_handshake_fail()
    test_auth_status()
    test_boot_set()
    test_boot_get()
    test_rst_set()
    test_rst_get()
    test_rst_pulse()
    test_v5v_set()
    test_v5v_get()
    test_v3v3_set()
    test_v3v3_get()
    test_get_version()
    test_get_uid()
    test_get_status()
    print("\nAll commands.py tests PASSED ✅")
