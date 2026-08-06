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
CMD_VUSB_SET: int = 0x36
CMD_VUSB_GET: int = 0x37

CMD_GET_VERSION: int = 0x40
CMD_GET_UID: int = 0x42
CMD_GET_STATUS: int = 0x44

# ── Extension module commands (H→D / D→H) ─────────────────────────────
CMD_MODULE_GET_INFO: int = 0x60   # H→D: request cached manifest (needs auth)
RSP_MODULE_INFO: int = 0x61       # D→H: [seq, more, data...] fragments
CMD_MODULE_IO: int = 0x62         # H→D: <=252B raw bytes forwarded to USART2
CMD_EVT_MODULE_DATA: int = 0x63   # D→H: module autonomous data event
CMD_MODULE_IOCTL: int = 0x64      # H→D: configure baud/I2C addr/reset

# 0x61 fragment layout
MODULE_INFO_SEQ: int = 0
MODULE_INFO_MORE: int = 1
MODULE_INFO_DATA: int = 2
MODULE_INFO_CHUNK: int = 250

# 0x62 / 0x63 single-frame payload limit (matches firmware)
MODULE_IO_MAX: int = 252

# ── Device → Host event notifications (v0.1.1) ────────────────────────
CMD_EVT_BUTTON: int = 0x51
EVT_BTN_ID_BOOT: int = 0x01
EVT_BTN_ID_RST: int = 0x02
EVT_ACTION_PRESSED: int = 0x01
EVT_ACTION_RELEASED: int = 0x02

# ── Response command bytes (device → host) ────────────────────────────
RSP_PONG: int = 0x02
RSP_AUTH_OK: int = 0x13
RSP_AUTH_FAIL: int = 0x14
RSP_BOOT_VAL: int = 0x22
RSP_RST_VAL: int = 0x25
RSP_V5V_VAL: int = 0x32
RSP_V3V3_VAL: int = 0x35
RSP_VUSB_VAL: int = 0x38
RSP_VERSION: int = 0x41
RSP_UID: int = 0x43
RSP_STATUS: int = 0x45

# Expected response command(s) per request.  Fire-and-forget SET commands
# are intentionally absent (they never get a response frame).
_EXPECTED_RESPONSE: dict[int, int | tuple[int, ...]] = {
    CMD_PING: RSP_PONG,
    CMD_CHALLENGE: CMD_CHALLENGE,        # firmware echoes 0x10 as response cmd
    CMD_RESPONSE: (RSP_AUTH_OK, RSP_AUTH_FAIL),
    CMD_AUTH_STATUS: (RSP_AUTH_OK, RSP_AUTH_FAIL),
    CMD_BOOT_GET: RSP_BOOT_VAL,
    CMD_RST_GET: RSP_RST_VAL,
    CMD_V5V_GET: RSP_V5V_VAL,
    CMD_V3V3_GET: RSP_V3V3_VAL,
    CMD_VUSB_GET: RSP_VUSB_VAL,
    CMD_GET_VERSION: RSP_VERSION,
    CMD_GET_UID: RSP_UID,
    CMD_GET_STATUS: RSP_STATUS,
    CMD_MODULE_GET_INFO: RSP_MODULE_INFO,
}

# Status bitfield positions (matches FK_GPIO_StatusAll)
STATUS_BIT_BOOT: int = 1 << 0
STATUS_BIT_RST: int = 1 << 1
STATUS_BIT_V5V: int = 1 << 2
STATUS_BIT_V3V3: int = 1 << 3
STATUS_BIT_VUSB: int = 1 << 4

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
        from flashkey_mcp._guard import _require_mcp_runtime
        _require_mcp_runtime()
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
        expected = _EXPECTED_RESPONSE.get(cmd)
        expected_set = {expected} if isinstance(expected, int) else expected
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
                    rcmd, rdata = result
                    if expected_set is None or rcmd in expected_set:
                        return (rcmd, rdata)
                    # 非预期帧（HELLO/按键事件等）→ 转交事件队列，不当作响应
                    enqueue = getattr(self._transport, "enqueue_event", None)
                    if enqueue is not None:
                        enqueue(rcmd, rdata)

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
            2. Send ``CHALLENGE`` → device stores it and returns an empty
               acknowledgment (v0.1.2: the device no longer echoes its own
               computed response back — that made the response replayable).
            3. Compute the response locally and send ``RESPONSE``.
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

        # Step 2: send CHALLENGE — device stores it, returns empty ACK.
        #         (Do not rely on any payload: v0.1.2 replies empty.)
        _rsp_cmd, _ack = self._transceive(CMD_CHALLENGE, challenge)

        # Step 3: compute response locally (only the host knows the key)
        local_response = compute_response(challenge, key)

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

    def vusb_set(self, value: bool) -> None:
        """Set the external USB-A power output (PA0, active-low).

        On the FK-01 v0.1.1 hardware PA0 is pulled **low to enable** the
        USB-A port power and **high to disable** it.

        Args:
            value: ``True`` to enable (PA0 low), ``False`` to disable (PA0 high).
        """
        self._send_only(CMD_VUSB_SET, bytes([1 if value else 0]))

    def vusb_get(self) -> bool:
        """Read the current external USB-A power state.

        Returns:
            ``True`` if enabled (PA0 low), ``False`` if disabled (PA0 high).
        """
        _rsp_cmd, data = self._transceive(CMD_VUSB_GET)
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
            bit 0 = boot, bit 1 = rst, bit 2 = v5v, bit 3 = v3v3, bit 4 = vusb

        Note: ``authed`` is NOT included — the caller (DeviceManager)
        merges auth state locally to avoid an extra round-trip.

        Returns:
            A dict with keys ``boot``, ``rst``, ``v5v``, ``v3v3``, ``vusb`` —
            each ``1`` or ``0``.
        """
        _rsp_cmd, data = self._transceive(CMD_GET_STATUS)

        boot = data[0] if len(data) > 0 else 0
        rst = data[1] if len(data) > 1 else 0
        bf = data[2] if len(data) > 2 else 0

        # Extract from bitfield (bits 2, 3 and 4)
        v5v = 1 if (bf & STATUS_BIT_V5V) else 0
        v3v3 = 1 if (bf & STATUS_BIT_V3V3) else 0
        vusb = 1 if (bf & STATUS_BIT_VUSB) else 0

        return {
            "boot": boot,
            "rst": rst,
            "v5v": v5v,
            "v3v3": v3v3,
            "vusb": vusb,
        }

    # ── Extension module commands (4) ────────────────────────────────────

    def module_get_info(self, read_timeout: float = 2.0) -> bytes | None:
        """Request the extension-module manifest and reassemble 0x61 fragments.

        The firmware returns ``[seq, more, data...]`` fragments; this method
        keeps reading until ``more == 0``.  An empty first fragment means
        "no module / manifest unavailable" and returns ``None``.

        Args:
            read_timeout: Max seconds to wait for the whole manifest.

        Returns:
            Reassembled manifest bytes, or ``None`` when the firmware reports
            no module.

        Raises:
            TimeoutError: No 0x61 fragment received in time (e.g. firmware
                without module support, or a mid-manifest stall).
        """
        frame = build_frame(CMD_MODULE_GET_INFO)
        chunks = bytearray()
        seen_first = False
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
                    rcmd, rdata = result
                    if rcmd == RSP_MODULE_INFO:
                        seen_first = True
                        if len(rdata) == 0:
                            return None  # 空载荷 = 无模块
                        if len(rdata) >= 2:
                            chunks += rdata[MODULE_INFO_DATA:]
                            if not rdata[MODULE_INFO_MORE]:
                                return bytes(chunks) if chunks else None
                    else:
                        # 非预期帧（HELLO/按键事件/模块数据等）→ 事件队列
                        enqueue = getattr(self._transport, "enqueue_event", None)
                        if enqueue is not None:
                            enqueue(rcmd, rdata)

        if seen_first:
            raise TimeoutError("MODULE_GET_INFO: manifest fragments incomplete")
        raise TimeoutError(f"No response received for command 0x{CMD_MODULE_GET_INFO:02X}")

    def module_io(self, data: bytes, window_ms: int = 1500) -> dict:
        """Forward raw bytes to the extension module (0x62) and collect
        0x63 data events during a short response window.

        The module is the actual executor of a tool call; its reply arrives
        as unsolicited 0x63 events which this method collects until the
        window expires.  The transport lock is held for the whole window so
        no other frame transaction interleaves.

        Args:
            data: Raw payload (<= 252 bytes).
            window_ms: Response collection window in milliseconds.

        Returns:
            ``{"sent": int, "chunks": [bytes...], "timed_out": bool}``.

        Raises:
            ValueError: If *data* exceeds the single-frame limit.
        """
        if len(data) > MODULE_IO_MAX:
            raise ValueError(
                f"模块单帧载荷上限 {MODULE_IO_MAX}B，收到 {len(data)}B"
            )

        frame = build_frame(CMD_MODULE_IO, data)
        chunks: list[bytes] = []
        deadline = time.monotonic() + (window_ms / 1000.0)
        last_rx = time.monotonic()  # 最近一次收到模块数据的时间
        silence_s = 0.15            # 数据静止阈值：超过即认为响应完整

        with self._transport._lock:
            self._transport.write(frame)
            parser = FrameParser()
            while time.monotonic() < deadline:
                byte_data = self._transport.read(1)
                if not byte_data:
                    continue  # 空读 = 等待更多数据（read 内部按串口超时阻塞）
                result = parser.feed(byte_data[0])
                if result is not None:
                    rcmd, rdata = result
                    if rcmd == CMD_EVT_MODULE_DATA:
                        chunks.append(rdata)
                        last_rx = time.monotonic()
                    else:
                        enqueue = getattr(self._transport, "enqueue_event", None)
                        if enqueue is not None:
                            enqueue(rcmd, rdata)
                elif chunks and time.monotonic() - last_rx >= silence_s:
                    # 已有响应数据且静止超过阈值 → 视为响应完整
                    break

        return {
            "sent": len(data),
            "chunks": chunks,
            "timed_out": not chunks and time.monotonic() >= deadline,
        }

    def module_ioctl(
        self,
        baud_rate: int = 0,
        i2c_addr: int = 0,
        reset: bool = False,
    ) -> None:
        """Configure the module bridge (0x64, fire-and-forget).

        Payload: ``[baud_le32, i2c_addr, reset]`` — 0 keeps the current value.

        Args:
            baud_rate: USART2 baud rate, 0 = keep current.
            i2c_addr: Module I2C 7-bit address, 0 = keep current.
            reset: If True, firmware resets module state and re-reads manifest.
        """
        payload = bytes([
            baud_rate & 0xFF,
            (baud_rate >> 8) & 0xFF,
            (baud_rate >> 16) & 0xFF,
            (baud_rate >> 24) & 0xFF,
            i2c_addr & 0xFF,
            1 if reset else 0,
        ])
        self._send_only(CMD_MODULE_IOCTL, payload)
