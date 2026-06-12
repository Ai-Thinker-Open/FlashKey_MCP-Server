1|#!/usr/bin/env python3
2|"""FlashKey FK-01 S1 test: PING + Auth handshake + verify PB11 trigger (DEBUG)"""
3|from flashkey_mcp.colors import green, red, yellow, cyan, gray, bold, bold_yellow
import serial, time, os
4|
5|def crc8(data):
6|    crc = 0
7|    for b in data:
8|        crc ^= b
9|        for _ in range(8):
10|            if crc & 0x80:
11|                crc = ((crc << 1) ^ 0x31) & 0xFF
12|            else:
13|                crc = (crc << 1) & 0xFF
14|    return crc
15|
16|def build_frame(cmd, data=b""):
17|    body = bytes([len(data) + 2, cmd]) + data
18|    return bytes([0x7E]) + body + bytes([crc8(body), 0x7F])
19|
20|# ====== Main test ======
21|PORT = "/dev/ttyUSB0"
22|BAUD = 115200
23|
24|ser = serial.Serial(PORT, BAUD, timeout=3)
25|time.sleep(0.5)
26|ser.reset_input_buffer()
27|
28|def send_and_recv(cmd, data=b""):
29|    """Send frame and collect complete response"""
30|    ser.reset_input_buffer()
31|    time.sleep(0.05)
32|    frame = build_frame(cmd, data)
33|    print(f"  TX frame: {frame.hex()} (cmd=0x{cmd:02X}, data={data.hex()})")
34|    ser.write(frame)
35|    time.sleep(0.15)
36|    raw = b""
37|    deadline = time.time() + 6
38|    while time.time() < deadline:
39|        chunk = ser.read(256)
40|        if chunk:
41|            raw += chunk
42|            print(f"  RX chunk: {chunk.hex()}")
43|            # Try to find a complete frame
44|            for i in range(len(raw)):
45|                if raw[i] == 0x7E:
46|                    if i + 2 >= len(raw):
47|                        continue
48|                    flen = raw[i+1]
49|                    if flen < 2:
50|                        continue
51|                    expected = 1 + flen + 1 + 1
52|                    end_idx = i + expected
53|                    if end_idx <= len(raw) and raw[end_idx - 1] == 0x7F:
54|                        body = raw[i+1:i+1+flen]
55|                        calc = crc8(body)
56|                        received_crc = raw[i+1+flen]
57|                        if calc == received_crc:
58|                            cmd_byte = body[1]
59|                            data_bytes = bytes(body[2:])
60|                            print(f"  -> Got frame: cmd=0x{cmd_byte:02X}, data={data_bytes.hex()}, crc_ok")
61|                            return (flen, cmd_byte, data_bytes)
62|                        else:
63|                            print(f"  -> CRC mismatch: calc={calc:02X}, got={received_crc:02X}")
64|        else:
65|            if raw:
66|                break
67|            time.sleep(0.1)
68|    # One more attempt
69|    for i in range(len(raw)):
70|        if raw[i] == 0x7E:
71|            if i + 2 >= len(raw):
72|                continue
73|            flen = raw[i+1]
74|            if flen < 2:
75|                continue
76|            expected = 1 + flen + 1 + 1
77|            end_idx = i + expected
78|            if end_idx <= len(raw) and raw[end_idx - 1] == 0x7F:
79|                body = raw[i+1:i+1+flen]
80|                calc = crc8(body)
81|                received_crc = raw[i+1+flen]
82|                if calc == received_crc:
83|                    cmd_byte = body[1]
84|                    data_bytes = bytes(body[2:])
85|                    print(f"  -> Late parse: cmd=0x{cmd_byte:02X}, data={data_bytes.hex()}")
86|                    return (flen, cmd_byte, data_bytes)
87|    print(f"  -> No valid frame in {len(raw)} raw bytes: {raw.hex()}")
88|    return None
89|
90|# 1. PING -> PONG
91|print("1. PING ->")
92|f = send_and_recv(0x01)
93|print("   Result:", "PONG ✅" if f and f[1] == 0x02 else "FAIL" + (f" (got {f[1]} cmd)" if f else ""))
94|
95|if not f or f[1] != 0x02:
96|    print("\n*** PING FAILED - chip not responding. Trying reset...")
97|    ser.dtr = False
98|    time.sleep(0.05)
99|    ser.dtr = True
100|    time.sleep(1.5)
101|    ser.reset_input_buffer()
102|    time.sleep(0.3)
103|    f = send_and_recv(0x01)
104|    print("   After reset:", "PONG ✅" if f and f[1] == 0x02 else "FAIL" + (f" (got {f[1]} cmd)" if f else ""))
105|
106|# 2. CHALLENGE
107|chal = os.urandom(8)
108|f = send_and_recv(0x10, chal)
109|print("2. CHALLENGE ->", f"cmd=0x{f[1]:02X}" if f else "NONE")
110|
111|# 3. RESPONSE (correct)
112|if f and f[1] == 0x10:
113|    expected_resp = f[2]  # The challenge response returned by device
114|    print(f"   Device computed response: {expected_resp.hex()}")
115|    # Verify our computation matches
116|    KEY = bytes([0xAB, 0xCD, 0xEF, 0x01, 0x23, 0x45, 0x67, 0x89])
117|    SBOX = bytes([
118|        0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
119|        0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
120|        0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
121|        0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
122|        0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
123|        0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
124|        0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
125|        0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
126|        0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
127|        0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
128|        0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
129|        0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
130|        0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
131|        0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
132|        0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
133|        0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16
134|    ])
135|    def compute_response(challenge):
136|        tmp = bytearray(8)
137|        for i in range(8):
138|            tmp[i] = challenge[i] ^ KEY[i]
139|        for i in range(8):
140|            tmp[i] = SBOX[tmp[i]]
141|        for i in range(1, 8):
142|            tmp[i] ^= ((tmp[i-1] << 1) | (tmp[i-1] >> 7)) & 0xFF
143|        resp = bytearray(8)
144|        for i in range(8):
145|            resp[i] = SBOX[tmp[(i + 3) % 8]]
146|        return bytes(resp)
147|    
148|    local_resp = compute_response(chal)
149|    print(f"   Local computed response: {local_resp.hex()}")
150|    print(f"   Match: {local_resp == expected_resp}")
151|    
152|    # Send our computed response back
153|    f = send_and_recv(0x11, local_resp)
154|    if f and f[1] == 0x13:
155|        print("3. RESPONSE CORRECT -> AUTH_OK(0x13) ✅")
156|    elif f:
157|        print(f"3. RESPONSE CORRECT -> cmd=0x{f[1]:02X} (unexpected)")
158|    else:
159|        print("3. RESPONSE CORRECT -> NONE")
160|else:
161|    print("3. RESPONSE CORRECT -> SKIP (no challenge response)")
162|
163|# 4. RESPONSE (wrong)
164|time.sleep(0.1)
165|f = send_and_recv(0x11, b"\x00" * 8)
166|if f and f[1] == 0x14:
167|    print("4. RESPONSE WRONG -> AUTH_FAIL(0x14) ✅")
168|elif f:
169|    print(f"4. RESPONSE WRONG -> cmd=0x{f[1]:02X} (unexpected)")
170|else:
171|    print("4. RESPONSE WRONG -> NONE")
172|
173|ser.close()
174|print("\nTest complete. Check PB11 LED on hardware:")
175|print("  - Default (no auth): OFF (HIGH)")
176|print("  - After correct auth: ON (LOW)")
177|print("  - After wrong auth: OFF (HIGH)")
178|
from colors import green, red, yellow, cyan, gray, bold, bold_yellow
