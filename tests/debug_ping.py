#!/usr/bin/env python3
"""Debug PING: debug the frame parser and test PING on /dev/ttyACM1"""
import serial, time

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

class FrameParser:
    SOF = 0x7E
    EOF = 0x7F
    STATE_IDLE, STATE_SOF, STATE_LEN, STATE_CMD, STATE_DATA, STATE_CRC, STATE_EOF = range(7)

    def __init__(self):
        self.reset()

    def reset(self):
        self.state = self.STATE_IDLE
        self.buf = bytearray()
        self.frame_len = 0
        self.data_len = 0

    def feed(self, byte):
        if self.state == self.STATE_IDLE:
            if byte == self.SOF:
                self.state = self.STATE_SOF
                self.buf = bytearray()
        elif self.state == self.STATE_SOF:
            self.frame_len = byte
            self.buf.append(byte)
            self.state = self.STATE_LEN
        elif self.state == self.STATE_LEN:
            self.buf.append(byte)
            self.data_len = self.frame_len - 2
            if self.data_len == 0:
                self.state = self.STATE_CRC
            else:
                self.state = self.STATE_DATA
        elif self.state == self.STATE_DATA:
            self.buf.append(byte)
            if len(self.buf) >= self.frame_len:
                self.state = self.STATE_CRC
        elif self.state == self.STATE_CRC:
            expected = crc8(self.buf)
            if byte == expected:
                self.state = self.STATE_EOF
            else:
                print(f"  CRC MISMATCH: expected=0x{expected:02X}, got=0x{byte:02X}")
                self.reset()
        elif self.state == self.STATE_EOF:
            self.reset()
            if byte == self.EOF:
                if len(self.buf) >= 2:
                    cmd = self.buf[1]
                    data = bytes(self.buf[2:]) if len(self.buf) > 2 else b""
                    return (self.frame_len, cmd, data)
        return None

# Test with known PONG frame
pong_bytes = bytes([0x7E, 0x0A, 0x02, 0x46, 0x4B, 0x2D, 0x30, 0x31, 0x21, 0x00, 0x00, 0x23, 0x7F])
print(f"PONG frame: {pong_bytes.hex()}")
body = pong_bytes[1:-2]  # remove SOF, CRC, EOF
print(f"Body: {body.hex()}, length={len(body)}")
calc = crc8(body)
print(f"CRC: expected=0x{pong_bytes[-2]:02X}, calculated=0x{calc:02X}")
print()

# Feed through parser
parser = FrameParser()
for i, b in enumerate(pong_bytes):
    result = parser.feed(b)
    sname = ["IDLE","SOF","LEN","CMD","DATA","CRC","EOF"][parser.state]
    print(f"  Byte {i}: 0x{b:02X} -> state={sname}, buf={bytes(parser.buf).hex()}")
    if result:
        print(f"  >>> FRAME: flen={result[0]}, cmd=0x{result[1]:02X}, data={result[2].hex() if result[2] else '(empty)'}")

print()

# Now test with actual device
print("=== Testing with /dev/ttyACM1 ===")
ser = serial.Serial("/dev/ttyACM1", 115200, timeout=3)
time.sleep(0.5)
ser.reset_input_buffer()
time.sleep(0.2)

# Drain stale
ser.timeout = 0.3
while ser.read(4096):
    pass
ser.timeout = 2

def build_frame(cmd, data=b""):
    body = bytes([len(data) + 2, cmd]) + data
    return bytes([0x7E]) + body + bytes([crc8(body), 0x7F])

# Send PING
ping_frame = build_frame(0x01)
print(f"Sending PING frame: {ping_frame.hex()}")
ser.write(ping_frame)
time.sleep(0.2)

# Read all response
raw = ser.read(100)
print(f"Received {len(raw)} bytes: {raw.hex()}")

# Parse
parser2 = FrameParser()
for i, b in enumerate(raw):
    result = parser2.feed(b)
    sname = ["IDLE","SOF","LEN","CMD","DATA","CRC","EOF"][parser2.state]
    if result:
        print(f"  >>> FRAME: flen={result[0]}, cmd=0x{result[1]:02X}, data={result[2].hex() if result[2] else '(empty)'}")

ser.close()

# Try multiple PINGs without reset_input_buffer
print("\n=== Multiple PINGs (no buffer reset between sends) ===")
ser = serial.Serial("/dev/ttyACM1", 115200, timeout=3)
time.sleep(0.5)
ser.reset_input_buffer()
time.sleep(0.2)
ser.timeout = 0.3
while ser.read(4096):
    pass
ser.timeout = 2

parser3 = FrameParser()
for n in range(5):
    ser.write(build_frame(0x01))
    time.sleep(0.05)

time.sleep(0.3)
raw2 = ser.read(200)
print(f"Received {len(raw2)} bytes: {raw2.hex()}")

for i, b in enumerate(raw2):
    result = parser3.feed(b)
    if result:
        print(f"  >>> FRAME {i}: flen={result[0]}, cmd=0x{result[1]:02X}, data={result[2].hex() if result[2] else '(empty)'}")
        print(f"      data decoded: {result[2]}")

ser.close()
print("\nDone.")
