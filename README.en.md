# 🔑 flashkey-mcp

> FlashKey FK-01 MCP Server — a universal USB flashing & debugging tool for any MCP-compatible AI assistant.

[![English](https://img.shields.io/badge/English-README-blue)](README.en.md)
[![简体中文](https://img.shields.io/badge/简体中文-中文文档-brightgreen)](README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Introduction

FlashKey FK-01 is a dual-chip USB flashing and debugging adapter from Ai-Thinker. **flashkey-mcp** is its MCP (Model Context Protocol) server plugin that lets AI tools such as Claude Code, Claude Desktop, Cline, Hermes Agent, and MiMo Code control the FK-01 directly for flashing, log collection, and debugging:

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

**Tools supported by automatic configuration**: Claude Code, Claude Desktop, Cline, Hermes Agent, MiMo Code

### Install via pip (from Git)

```bash
pip install git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git
```

For SSE support:

```bash
pip install "flashkey-mcp[sse] @ git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git"
```

### Build / install from source (developers)

```bash
git clone git@github.com:Ai-Thinker-Open/FlashKey_MCP-Server.git
cd flashkey-mcp
pip install -e .
```

---

## Usage

flashkey-mcp runs as a stdio MCP server. **The MCP configuration is essentially the same for every tool: tell the tool to launch a stdio subprocess with the `flashkey-mcp` command.**

### Manual configuration

#### JSON format (Claude Code / Claude Desktop / Cline / VS Code)

Add the following to your tool's MCP config file:

```json
{
  "mcpServers": {
    "flashkey": {
      "command": "flashkey-mcp"
    }
  }
}
```

| Tool | Config file path |
|------|------------------|
| Claude Code | `~/.claude/mcp.json` |
| Claude Desktop macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cline (VS Code) | `~/.cline/mcp.json` |

#### YAML format (Hermes Agent)

`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  flashkey:
    command: flashkey-mcp
    args: []
    enabled: true
```

#### Cursor

Settings → MCP → Add new MCP server:
- Name: `flashkey`
- Type: `command`
- Command: `flashkey-mcp`

#### MiMo Code

```bash
mimo mcp add flashkey-mcp --command flashkey-mcp
```

Or add to `mimocode.json` in the project root:

```json
{
  "mcp": {
    "flashkey-mcp": {
      "type": "local",
      "command": ["flashkey-mcp"]
    }
  }
}
```

### Verify the installation

```bash
# Version check
flashkey-mcp --version

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

Returns whether the FK-01 is online, its firmware version, and available serial ports.

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

`flashkey_firmware_check()` compares the device's current firmware version, the firmware bundled with the installed package, and the latest firmware release on GitHub; `flashkey_firmware_flash()` flashes the CH32V203 via WCH-LinkE (SDI), using the bundled hex by default (a custom `hex_path` is also supported).

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

**Q: The tool says an update is available?**

A: Run `pip install --upgrade git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git` and restart the MCP server.

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
