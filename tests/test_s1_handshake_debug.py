#!/usr/bin/env python3
"""FlashKey FK-01 S1 test: PING + Auth handshake + verify PB11 trigger (DEBUG)"""
import serial, time, os

def crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc

def build_frame(cmd, data=b""):
    body = bytes([len(data) + 2, cmd]) + data
    return bytes([0x7E]) + body + bytes([crc8(body), 0x7F])

# ====== Main test ======
PORT = "/dev/ttyUSB0"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=3)
time.sleep(0.5)
ser.reset_input_buffer()

def send_and_recv(cmd, data=b""):
    """Send frame and collect complete response"""
    ser.reset_input_buffer()
    time.sleep(0.05)
    frame = build_frame(cmd, data)
    print(f"  TX frame: {frame.hex()} (cmd=0x{cmd:02X}, data={data.hex()})")
    ser.write(frame)
    time.sleep(0.15)
    raw = b""
    deadline = time.time() + 6
    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            raw += chunk
            print(f"  RX chunk: {chunk.hex()}")
            # Try to find a complete frame
            for i in range(len(raw)):
                if raw[i] == 0x7E:
                    if i + 2 >= len(raw):
                        continue
                    flen = raw[i+1]
                    if flen < 2:
                        continue
                    expected = 1 + flen + 1 + 1
                    end_idx = i + expected
                    if end_idx <= len(raw) and raw[end_idx - 1] == 0x7F:
                        body = raw[i+1:i+1+flen]
                        calc = crc8(body)
                        received_crc = raw[i+1+flen]
                        if calc == received_crc:
                            cmd_byte = body[1]
                            data_bytes = bytes(body[2:])
                            print(f"  -> Got frame: cmd=0x{cmd_byte:02X}, data={data_bytes.hex()}, crc_ok")
                            return (flen, cmd_byte, data_bytes)
                        else:
                            print(f"  -> CRC mismatch: calc={calc:02X}, got={received_crc:02X}")
        else:
            if raw:
                break
            time.sleep(0.1)
    # One more attempt
    for i in range(len(raw)):
        if raw[i] == 0x7E:
            if i + 2 >= len(raw):
                continue
            flen = raw[i+1]
            if flen < 2:
                continue
            expected = 1 + flen + 1 + 1
            end_idx = i + expected
            if end_idx <= len(raw) and raw[end_idx - 1] == 0x7F:
                body = raw[i+1:i+1+flen]
                calc = crc8(body)
                received_crc = raw[i+1+flen]
                if calc == received_crc:
                    cmd_byte = body[1]
                    data_bytes = bytes(body[2:])
                    print(f"  -> Late parse: cmd=0x{cmd_byte:02X}, data={data_bytes.hex()}")
                    return (flen, cmd_byte, data_bytes)
    print(f"  -> No valid frame in {len(raw)} raw bytes: {raw.hex()}")
    return None

# 1. PING -> PONG
print("1. PING ->")
f = send_and_recv(0x01)
print("   Result:", "PONG ✅" if f and f[1] == 0x02 else "FAIL" + (f" (got {f[1]} cmd)" if f else ""))

if not f or f[1] != 0x02:
    print("\n*** PING FAILED - chip not responding. Trying reset...")
    ser.dtr = False
    time.sleep(0.05)
    ser.dtr = True
    time.sleep(1.5)
    ser.reset_input_buffer()
    time.sleep(0.3)
    f = send_and_recv(0x01)
    print("   After reset:", "PONG ✅" if f and f[1] == 0x02 else "FAIL" + (f" (got {f[1]} cmd)" if f else ""))

# 2. CHALLENGE
chal = os.urandom(8)
f = send_and_recv(0x10, chal)
print("2. CHALLENGE ->", f"cmd=0x{f[1]:02X}" if f else "NONE")

# 3. RESPONSE (correct)
if f and f[1] == 0x10:
    expected_resp = f[2]  # The challenge response returned by device
    print(f"   Device computed response: {expected_resp.hex()}")
    # Verify our computation matches
    KEY = bytes([0xAB, 0xCD, 0xEF, 0x01, 0x23, 0x45, 0x67, 0x89])
    SBOX = bytes([
        0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
        0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
        0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
        0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
        0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
        0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
        0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
        0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
        0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
        0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
        0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
        0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
        0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
        0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
        0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
        0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16
    ])
    def compute_response(challenge):
        tmp = bytearray(8)
        for i in range(8):
            tmp[i] = challenge[i] ^ KEY[i]
        for i in range(8):
            tmp[i] = SBOX[tmp[i]]
        for i in range(1, 8):
            tmp[i] ^= ((tmp[i-1] << 1) | (tmp[i-1] >> 7)) & 0xFF
        resp = bytearray(8)
        for i in range(8):
            resp[i] = SBOX[tmp[(i + 3) % 8]]
        return bytes(resp)
    
    local_resp = compute_response(chal)
    print(f"   Local computed response: {local_resp.hex()}")
    print(f"   Match: {local_resp == expected_resp}")
    
    # Send our computed response back
    f = send_and_recv(0x11, local_resp)
    if f and f[1] == 0x13:
        print("3. RESPONSE CORRECT -> AUTH_OK(0x13) ✅")
    elif f:
        print(f"3. RESPONSE CORRECT -> cmd=0x{f[1]:02X} (unexpected)")
    else:
        print("3. RESPONSE CORRECT -> NONE")
else:
    print("3. RESPONSE CORRECT -> SKIP (no challenge response)")

# 4. RESPONSE (wrong)
time.sleep(0.1)
f = send_and_recv(0x11, b"\x00" * 8)
if f and f[1] == 0x14:
    print("4. RESPONSE WRONG -> AUTH_FAIL(0x14) ✅")
elif f:
    print(f"4. RESPONSE WRONG -> cmd=0x{f[1]:02X} (unexpected)")
else:
    print("4. RESPONSE WRONG -> NONE")

ser.close()
print("\nTest complete. Check PB11 LED on hardware:")
print("  - Default (no auth): OFF (HIGH)")
print("  - After correct auth: ON (LOW)")
print("  - After wrong auth: OFF (HIGH)")
