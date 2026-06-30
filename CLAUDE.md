# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

flashkey-mcp is an MCP server for the FlashKey FK-01, a dual-MCU USB programmer/debugger for BL602/BL616/BL618 chips. It lets AI agents flash firmware and capture logs via natural language — plug in FK-01, automatic handshake completes in 5s, no manual connection step.

## Commands

```bash
# Install in editable mode
pip install -e .

# Install with SSE transport support
pip install -e ".[sse]"

# Run (stdio mode — default for MCP)
flashkey-mcp

# Run (SSE mode on :8100)
flashkey-mcp --sse --port 8100

# Debug logging — monitor runtime in another terminal
flashkey-mcp --debug                              # DEBUG level, log to /tmp/flashkey-mcp.log
flashkey-mcp --debug --log-file /path/to/fk.log   # custom log path

# Monitor in real time:
#   Linux/macOS:  tail -f /tmp/flashkey-mcp.log
#   PowerShell:   Get-Content -Wait $env:TEMP\flashkey-mcp.log

# Tests — manual integration scripts (require physical FK-01)
python tests/test_s1_handshake.py
python tests/test_flash_break_mode.py
python tests/test_mcp_server.py          # uses mcp.client.stdio to call tools
python tests/test_commands.py
python tests/test_auth.py
python tests/debug_ping.py
```

There is no pytest/tox automation. All tests are standalone scripts meant to be run with a real FK-01 connected.

## Architecture

### Layered design (bottom → top)

```
transport.py     — serial port I/O, VID/PID discovery (1A86:FE0D), FlashKeyTransport
protocol.py      — frame framing: SOF/LEN/CMD/DATA/CRC-8(MAXIM)/EOF, FrameParser state machine
auth.py          — challenge-response auth: SBOX + XOR + rotate-left-1 diffusion, 8-byte KEY
commands.py      — 15 command bytes → typed Python methods; handshake() wires auth to protocol
__init__.py      — FlashKey class: bundles FlashKeyTransport + FlashKeyCommands
device_manager.py — background thread: inotify-driven hotplug → HELLO wait → handshake → PING keepalive
server.py        — FastMCP server: 19 registered tools, flash/log mutual exclusion, main() entry
```

### Key architectural decisions

**DeviceManager is a singleton.** `_get_dm()` creates it once, `_dm.start()` launches one daemon thread. That thread runs a state machine: `DISCONNECTED → CONNECTING → AUTHED`. The `require_authed()` guard is called by every authenticated tool before execution.

**No auth tools.** `flashkey_status()` and `flashkey_list_ports()` skip `require_authed()` — they always work. This is intentional so the AI can diagnose connection issues without a device.

**HELLO-driven handshake.** The firmware sends a HELLO frame (cmd=0x02) on boot. The DeviceManager waits for it (3s timeout), then initiates challenge-response (2s). Total budget: 5s. There is no `flashkey_handshake` tool — the old one was removed in commit `11cc7e1`.

**PING keepalive.** Once authed, the DeviceManager pings every 2s. Two consecutive failures → transition to DISCONNECTED. The firmware has a heartbeat timeout; without PINGs the device drops auth state.

**Flash/log mutual exclusion.** `_flash_lock` (threading.Lock) prevents `flashkey_flash` and `flashkey_log` from using the same CH340C serial port simultaneously. `_flash_active_port` tracks which port is busy.

### The two flash modes

`flashkey_flash` supports two modes, selected by `mode` param — defaults per chip:

| Chip | Default mode | Behavior |
|------|-------------|----------|
| BL602 | `break` | Start `make flash` first → watch stdout for reset prompt keywords → RST pulse → wait for tool exit |
| BL616/BL618 | `isp` | BOOT↑ → RST pulse → run `make flash` → recover |

In `break` mode, keyword matching (`reset`, `rest`, `press`, `uart`, `复位`) is case-insensitive. 30s timeout if no prompt is seen, then suggests trying `mode='isp'`.

`_resolve_flash_tool()` has a 3-tier fallback: user-supplied `tool` string → `make flash` from `sdk_path` → error with SDK clone instructions.

### Serial protocol details

- Baud: always 115200 (device firmware)
- Frame: `SOF(0x7E) LEN CMD DATA[N] CRC-8 EOF(0x7F)`
- CRC: CRC-8/MAXIM (poly 0x31, init 0x00) — same as Dallas 1-Wire
- SET commands (BOOT, RST, V5V, V3V3) are fire-and-forget — no response expected
- GET commands (and PING/CHALLENGE/RESPONSE) use `_transceive()` which waits for a response frame

### Platform-specific code

- **Linux**: `device_manager.py` uses `ctypes` to call `inotify_init1`/`inotify_add_watch` on `/dev` for event-driven hotplug. Falls back to 1s poll if inotify fails.
- **Windows**: `find_port()` scans `serial.tools.list_ports.comports()` matching VID=0x1A86, PID=0xFE0D, then falls back to `/dev/ttyACM*` glob on Linux.
- **WSL**: SSE mode provides `POST /release` and `POST /reconnect` endpoints so the serial port can be released for `usbipd` remapping.

## Domain knowledge (not derivable from tool schemas)

- **Dual-port architecture**: FK-01 exposes TWO serial ports identified by VID/PID, not device name. `list_all_ports()` returns a `role` field: `fk_control` (1A86:FE0D, MCP only, never flash/log), `fk_flash` (1A86:7523, CH340C bridge, flash/log use this), `unknown` (other). Different OS use different names (ttyACMx/ttyUSBx/COMx/cu.*) — always match by `role`, not by device name. `_validate_flash_port()` in server.py rejects `fk_control` ports with a clear error.
- **v5v is active-low**: `v5v_set(True)` pulls PB1 LOW → 5V ON. This is counterintuitive.
- **Windows COM10+**: must use `\\.\COM10` syntax.
- **Serial port mutex**: CH340C is shared between flash and log; they cannot run concurrently.
- **BL602 boot log**: look for `Booting BL602...` or `[OS] Starting`.
- **BL616/BL618 boot log**: look for `Starting ...` or `Hello World!`.
- **Flash failure triage**: check `flashkey_status()` first → lower baud_rate → verify BOOT level → close other serial monitors.
- **Chip SDKs**: BL602 → Ai-Thinker-WB2; BL616/BL618 → bouffalo_sdk.
