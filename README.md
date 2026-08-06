<h1 align="center">FlashKey MCP Server</h1>

> FlashKey FK-01 MCP Server — a universal USB flashing & debugging tool for any MCP-compatible AI assistant.

[![简体中文|README](https://img.shields.io/badge/简体中文-README-brightgreen)](README.zh.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Introduction

FlashKey FK-01 is a dual-chip USB flashing and debugging adapter from Ai-Thinker. **flashkey-mcp** is its MCP (Model Context Protocol) server plugin that lets AI tools such as Cline, Hermes Agent, and MiMo Code control the FK-01 directly for flashing, log collection, and debugging:

- ⚡ One-click firmware flashing for BL602 / BL616 / BL618
- 📋 Collect target chip serial logs
- 🔘 Control BOOT / RST pins and 5V / 3.3V / VUSB power
- 🔄 Upgrade FK-01's own CH32V203 firmware (OpenOCD + WCH-LinkE)

Automatic handshake within 5 seconds after plugging in the FK-01; `flashkey_status()` provides unified status without authentication, ready to use out of the box.

> Source repository: [Ai-Thinker-Open/FlashKey_MCP-Server](https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server)

---

## Installation / Build

### One-click install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/Ai-Thinker-Open/FlashKey_MCP-Server/master/setup.sh | bash
```

The script will:
1. Install flashkey-mcp
2. Detect AI tools on your system and write the matching MCP configuration automatically
3. Show you the next steps

**Tools supported by automatic configuration**: Cline, Hermes Agent, MiMo Code

### Install via pip (from Git)

```bash
pip install git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git
```

> The starlette / uvicorn dependencies required by SSE mode are included in
> the default install. The `flashkey-mcp[sse]` extra is kept only for
> compatibility with older install commands.

### Install from source (developers)

```bash
git clone git@github.com:Ai-Thinker-Open/FlashKey_MCP-Server.git
cd FlashKey_MCP-Server
pip install -e .
```

---

## Usage

flashkey-mcp runs in **SSE (HTTP) mode by default**: one long-lived daemon
serves every AI session, so multiple sessions share the same FK-01 without
serial-port preemption or interleaved handshakes.

### Step 1: Start the daemon

**Linux (recommended — systemd user service, auto-start + auto-restart)**

```bash
flashkey-mcp --service install
```

**Windows / macOS (run manually)**

```bash
flashkey-mcp --sse --host 127.0.0.1 --port 8100
```

> `--sse` is optional (SSE is the default). The endpoint is
> `http://127.0.0.1:8100/sse`. If the port is already in use by another
> flashkey-mcp instance, the program prints a friendly hint and exits 0
> instead of binding again.

### Step 2: Point your AI tools at the endpoint

Every tool's MCP config is essentially the same: connect to the SSE endpoint
above instead of launching a subprocess.

#### JSON format (Cline / VS Code)

Add the following to your tool's MCP config file:

```json
{
  "mcpServers": {
    "flashkey": {
      "type": "sse",
      "url": "http://127.0.0.1:8100/sse"
    }
  }
}
```

| Tool | Config file path |
|------|------------------|
| Cline (VS Code) | `~/.cline/mcp.json` |

#### YAML format (Hermes Agent)

`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  flashkey:
    type: sse
    url: http://127.0.0.1:8100/sse
    enabled: true
```

#### Cursor

Settings → MCP → Add new MCP server:
- Name: `flashkey`
- Type: `sse`
- URL: `http://127.0.0.1:8100/sse`

#### MiMo Code

```bash
mimo mcp add flashkey-mcp --transport sse --url http://127.0.0.1:8100/sse
```

Or add to `mimocode.json` in the project root:

```json
{
  "mcp": {
    "flashkey-mcp": {
      "type": "sse",
      "url": "http://127.0.0.1:8100/sse"
    }
  }
}
```

#### Codex (OpenAI)

```bash
codex mcp add flashkey-mcp --url http://127.0.0.1:8100/mcp
```

### Multiple AI sessions / clients sharing one FK-01

Any number of sessions on the same machine (Cursor, Codex, …) simply
use the same URL. The device is held by the single daemon, so sessions never
interfere with each other:

- Linux: the service restarts automatically after a crash; no action needed.
- Windows / macOS: keep the daemon running; watch logs with
  `tail -f /tmp/flashkey-mcp.log`.

### Idle port release (heartbeat note)

After **30 seconds** without a tool call, the daemon closes the FK-01
serial port and the firmware heartbeat (PING) pauses with it. The next
tool call reconnects automatically and re-runs the ~5s handshake, then
the heartbeat resumes. This keeps the port free for other programs (or
WSL USB remapping) while the daemon is idle:

- Adjust the idle timeout with `FLASHKEY_IDLE_TIMEOUT=60 flashkey-mcp`
- Set it to `0` to disable idle release (always keep the connection)
- `flashkey_status()` reports `idle: true` while the port is released
- Long operations (flash / log capture) never trigger a release

### Legacy stdio mode (single session)

For a single session you can still run `flashkey-mcp --stdio` and configure
`{"command": "flashkey-mcp"}`. Note that stdio means "one process per
session" — multiple sessions will fight over the same FK-01, so prefer SSE.

### Verify the installation

```bash
# Version check
flashkey-mcp --version

# Service status (Linux)
flashkey-mcp --service status

# Config check: restart your AI tool, then call it in a conversation
flashkey_status()
```

---

## Examples

### Example 1: Check device status

Call this in your AI tool's conversation:

```
flashkey_status()
```

Returns the FK-01's authentication status, firmware version, pin states, and module info.

### Example 2: Flash firmware in one click

```
flashkey_flash(firmware_path="/path/to/firmware.bin", chip="bl602", flash_port="ttyACM1")
```

> ⚠️ Port selection: first call `flashkey_list_ports()` to list the ports and pick the one with `role=fk_log`; **do not** use the `role=fk_control` port (that is the FK-01 control port, reserved for the MCP server).

### Example 3: Collect target chip logs

```
flashkey_log(port="ttyACM1", baud_rate=115200, duration=5, grep="ERROR")
```

---

## How it works

FlashKey FK-01 is a dual-chip USB flashing and debugging adapter. The MCP plugin exposes 27 tools:

```
flashkey_status()          ← unified status, no authentication required
flashkey_list_ports()      ← list all serial ports

flashkey_flash()           ← one-click flashing for BL602/BL616/BL618
flashkey_log()             ← collect target chip logs
flashkey_firmware_check()  ← check for FK-01 firmware updates
flashkey_firmware_flash()  ← flash FK-01's own CH32V203 firmware (OpenOCD + WCH-LinkE)

flashkey_boot_set/get()    ← BOOT pin control
flashkey_rst_set/get/pulse()  ← RST pin control
flashkey_v5v_set/get()     ← 5V power
flashkey_v3v3_set/get()    ← 3.3V power
flashkey_enter_bootloader()   ← combined ISP-mode entry
flashkey_ping() / flashkey_get_version() / flashkey_get_uid()
```

Automatic handshake within 5 seconds after plugging in the FK-01.

---

## Upgrading FK-01 firmware (CH32V203)

`flashkey_firmware_check()` checks the device's current firmware version, the firmware bundled with the installed package, and the installed plugin version, comparing them with the latest GitHub release; `flashkey_firmware_flash()` flashes the CH32V203 via WCH-LinkE (SDI), using the bundled hex by default (a custom `hex_path` is also supported).

⚠️ Prerequisites: connect the FlashKey's built-in WCH-LinkE to your computer via USB and wire SWDIO/SWCLK/GND/3V3 to the CH32V203's SWD interface with the target powered on; in WSL you need `usbip attach` first. Flashing requires an explicit `confirm=True`. If a normal flash fails with a suspected read/write protection error, the tool automatically retries with an unlocked full-chip erase + flash; if it still fails, it will suggest manually unlocking with **WCH-LinkUtility** on a Windows host.

The OpenOCD binaries (WCH v1.6, Linux x64 / Windows x64) are bundled in `flashkey_mcp/openocd/` — no separate installation needed. You can override the path with the `FLASHKEY_OPENOCD` environment variable. Licenses for bundled components are in `NOTICE-OPENOCD.md`.

---

## FAQ / Troubleshooting

**Q: FK-01 is not recognized in WSL?**

A: Attach the USB device to WSL first (e.g. `usbip attach -r 127.0.0.1 -b <bus-id>`), then call the tools.

**Q: I picked the wrong port for flashing?**

A: First call `flashkey_list_ports()` and always pick the port with `role=fk_log` (WCH-LinkE VCP). The `role=fk_control` port is the FK-01 control port and must not be used for flashing or logging.

**Q: Flashing fails with a read/write protection error?**

A: The tool automatically retries with an unlocked full-chip erase + flash; if it still fails, manually unlock it with WCH-LinkUtility on a Windows host.

**Q: Unstable logs at high baud rates?**

A: The WCH-LinkE VCP (fk_log) only supports up to 921600; use an external USB-UART for higher baud rates.

**Q: The serial port was released after sitting idle?**

A: This is by design: after 30s without tool calls the port closes and the heartbeat pauses; the next call reconnects (~5s handshake). Disable with `FLASHKEY_IDLE_TIMEOUT=0` or adjust the idle seconds.

**Q: The tool says an update is available?**

A: Run `pip install --upgrade git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git` and restart the MCP server. For the Linux daemon, run `systemctl --user restart flashkey-mcp` (or check with `flashkey-mcp --service status`).

---

## Contributing

Issues and pull requests are welcome:

1. Fork this repository and create a feature branch;
2. Follow Conventional Commits for commit messages (e.g. `feat:`, `fix:`, `docs:`);
3. Run `pytest` to make sure tests pass;
4. Describe the purpose and verification in your PR.

---

## License

[MIT](LICENSE) © 2026 Ai-Thinker Open
