<h1 align="center">FlashKey MCP Server</h1>

> FlashKey FK-01 MCP Server — a universal USB flashing & debugging tool for any MCP-compatible AI assistant.

[![简体中文|README](https://img.shields.io/badge/简体中文-README-brightgreen)](README.zh.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Introduction

FlashKey FK-01 is a dual-chip USB flashing and debugging adapter from Ai-Thinker. **flashkey-mcp** is its MCP (Model Context Protocol) server plugin that lets AI tools such as Cline, Hermes Agent, and OpenCode control the FK-01 directly for flashing, log collection, and debugging:

- ⚡ One-click firmware flashing for Ai-WB2 / Ai-M62
- 📋 Collect target chip serial logs
- 🔘 Control BOOT / RST pins and 5V / 3.3V / VUSB power
- 🔄 Upgrade FK-01's own CH32V203 firmware (OpenOCD + WCH-LinkE)

Automatic handshake within 5 seconds after plugging in the FK-01; `status()` provides unified status without authentication, ready to use out of the box.

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

**Tools supported by automatic configuration**: Cline, Hermes Agent, OpenCode

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

### China network / mirrors (国内网络/镜像)

If GitHub is unreachable, both the install source and the firmware-update
source can be switched via environment variables:

```bash
# Install via the Gitee mirror (Gitee auto-syncs from the GitHub source repo)
FLASHKEY_INSTALL_URL="flashkey-mcp[sse] @ git+https://gitee.com/Ai-Thinker-Open/FlashKey_MCP-Server.git" bash setup.sh

# firmware_check update source: auto (default, GitHub first then Gitee) / github / gitee
FLASHKEY_UPDATE_SOURCE=gitee flashkey-mcp
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

#### OpenCode

Write to the global config `~/.config/opencode/opencode.json` (or `opencode.json` in the project root):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "flashkey": {
      "type": "remote",
      "url": "http://127.0.0.1:8100/sse",
      "enabled": true
    }
  }
}
```

> OpenCode supports `type: "local"` / `"remote"`: to connect to a local SSE endpoint use
> `type: "remote"` with the `/sse` URL (OpenCode tries Streamable HTTP first, then falls
> back to the legacy SSE transport). Do **not** use `type: "local"` with
> `command: ["flashkey-mcp", "--sse"]` — that spawns a separate server process and OpenCode
> waits for a stdio handshake that never comes (`Connection closed`).

Verify with `opencode mcp list` — `flashkey` should show `connected`.

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
- `status()` reports `idle: true` while the port is released
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
status()
```

---

## Examples

### Example 1: Check device status

Call this in your AI tool's conversation:

```
status()
```

Returns the FK-01's authentication status, firmware version, pin states, and module info.

### Example 2: Flash firmware in one click

```
flash(firmware_path="/path/to/firmware.bin", chip="ai-wb2", flash_port="ttyACM1")
```

> ⚠️ Port selection: first call `list_ports()` to list the ports and pick the one with `role=fk_log`; **do not** use the `role=fk_control` port (that is the FK-01 control port, reserved for the MCP server).

### Example 3: Collect target chip logs

```
log_open(port="ttyACM1", baud_rate=115200, project="my_app")  # background capture starts, returns immediately
rst_pulse(50)                               # post-flash verification: must reboot the module to capture the full boot log
log_close()                                 # close and release the serial port
# read resource flashkey://log for this capture
# to keep it long-term: log_dump(dest_path="~/logs/boot.txt")
# log_close already archives to ~/flashkey-logs/my_app/ (max 10 per project, oldest is replaced)
```

---

## How it works

FlashKey FK-01 is a dual-chip USB flashing and debugging adapter. The MCP plugin exposes 31 tools:

```
status()          ← unified status, no authentication required
list_ports()      ← list all serial ports
recover()         ← one-stop recovery (USB re-attach + re-handshake)

flash()           ← one-click flashing for Ai-WB2/Ai-M62
flash_guide()     ← call before flashing; returns the standard procedure (visible to almost any AI agent)
log_open() / log_close()  ← background log capture + close
log_dump()        ← export the latest capture to a local file
firmware_check()  ← check for FK-01 firmware updates
firmware_flash()  ← flash FK-01's own CH32V203 firmware (OpenOCD + WCH-LinkE)

boot_set/get()    ← BOOT pin control
rst_set/get/pulse()  ← RST pin control
v5v_set/get()     ← 5V power
v3v3_set/get()    ← 3.3V power
enter_bootloader()   ← combined ISP-mode entry
ping() / get_version() / get_uid()
```

Automatic handshake within 5 seconds after plugging in the FK-01.

---

## MCP Resources & Prompts

The MCP server exposes authoritative references as **resources** and correct
call-sequence templates as **prompts**. Clients that support `resources/prompts`
can read them directly; other clients still receive the same condensed guidance
through the injected server instructions and tool descriptions.

### Resources

| URI | Content |
| --- | --- |
| `flashkey://docs/quickstart` | Quick start: status → pick port → auth → flash/log |
| `flashkey://docs/flash-guide` | Flash guide: port roles, per-chip default mode/baud, post-flash verification |
| `flashkey://docs/error-codes` | Authoritative error-code table (same source as the README table below) |
| `flashkey://status` | Live device status (dynamic JSON; includes `error` when offline) |
| `flashkey://ports` | Live serial-port list with `role` field (dynamic JSON) |
| `flashkey://log` | Latest log capture (text, overwritten on the next capture) |

### Resource Templates (historical logs)

| Template | Purpose |
| --- | --- |
| `flashkey://logs/{project}` | List all historical logs for a project (JSON, up to 10) |
| `flashkey://logs/{project}/{file}` | Read the full content of a specific historical log |

Capturing with `log_open(..., project="<project>")` and closing with `log_close()`
automatically archives logs to `~/flashkey-logs/<project>/flashkey-log-<timestamp>.txt`
(the directory can be overridden with `FLASHKEY_LOG_HISTORY_DIR`). Each project keeps at
most **10** files; the oldest is removed when the cap is exceeded. A new `log_open()`
still overwrites the temporary `flashkey://log`; historical archives are not affected.

### Prompts

> MCP prompts are triggered on demand by the client (slash commands in OpenCode); the AI agent
> does not invoke them automatically. Automatic adherence to the flashing workflow comes
> from the injected server instructions and tool descriptions (same source as the
> `flashkey://docs/*` resources), both of which embed “list ports first, pick `fk_log`,
> never hardcode port names, use `flash` for normal flashing”.

> Cross-client practice: call the **`flash_guide(chip)`** tool before flashing (tools are
> visible and callable in every MCP client), and copy this repo's `AGENTS.md` to your
> flashing project root so Codex / Claude Code / Cursor / OpenCode / Cline inject the workflow.

| Prompt | Purpose |
| --- | --- |
| `flash-firmware` | Generates flashing steps: pick `fk_log` → auth → flash with chip defaults → verify |
| `recover-device` | Recovery decision tree from an error code |
| `collect-logs` | Generates log collection steps (the AI reads the log and analyzes the runtime status itself) |

### Ai-WB2 / Ai-M62 usage summary

- Call `list_ports()` first and pick the port with `role=fk_log` (WCH-LinkE VCP).
  **Never** use `role=fk_control`, and never guess or hardcode port names
  (`/dev/ttyACM0` / `COM3` change across systems and plug order).
- For normal flashing call only `flash(firmware_path, chip, flash_port)`; custom flash
  commands (e.g. `make eflash`) go through the `tool` parameter (supports `{port}`/`{baud}`/
  `{firmware}`/`{chip}` placeholders) — no extra low-level tool is needed.
- `chip="ai-wb2"` defaults to **break** / `baud_rate=921600` (`make flash`): serial-break
  flashing writes only the App, not boot2; if the firmware does not support serial break or
  the chip was erased with `make erase_flash`, use `mode="isp"` + `make eflash`
  (full flash incl. boot2, BOOT↑ + RST enters ISP).
- `chip="ai-m62"` uses **isp** mode with `baud_rate=921600` (FlashKey's own serial port
  supports at most 921600; 2000000 requires an external USB-UART).
- Verify after flashing: open `log_open()` first, **then `rst_pulse()` to reboot the module**
  so the full boot log is captured, then `log_close()`, read `flashkey://log` and **analyze
  it yourself to decide whether the boot is healthy** (investigate anomalies instead of just
  quoting the log), or send `AT+GMR` on AT modules.

---

## Upgrading FK-01 firmware (CH32V203)

`firmware_check()` checks the device's current firmware version, the firmware bundled with the installed package, and the installed plugin version, comparing them with the latest GitHub release; `firmware_flash()` flashes the CH32V203 via WCH-LinkE (SDI), using the bundled hex by default (a custom `hex_path` is also supported).

> Versioning policy: the bundled hex (FK-01 device firmware) is **hardened** and is
> independent of the flashkey-mcp package version — the package can release often while
> the hex only changes when the FK-01 firmware gets a new build.
>
> Firmware update source: `firmware_check` detects hex updates from the **FlashKey repo**
> (`https://github.com/Ai-Thinker-Open/FlashKey`, overridable via `FLASHKEY_FIRMWARE_REPO`),
> using the `releases/latest` tag as the firmware version; when that repo has no release
> yet, it falls back to the flashkey-mcp repo's `firmware.json` manifest.

⚠️ Prerequisites: connect the FlashKey's built-in WCH-LinkE to your computer via USB and wire SWDIO/SWCLK/GND/3V3 to the CH32V203's SWD interface with the target powered on; in WSL you need `usbip attach` first. Flashing requires an explicit `confirm=True`. If a normal flash fails with a suspected read/write protection error, the tool automatically retries with an unlocked full-chip erase + flash; if it still fails, it will suggest manually unlocking with **WCH-LinkUtility** on a Windows host.

The OpenOCD binaries (WCH v1.6, Linux x64 / Windows x64) are bundled in `flashkey_mcp/openocd/` — no separate installation needed. You can override the path with the `FLASHKEY_OPENOCD` environment variable. Licenses for bundled components are in `NOTICE-OPENOCD.md`.

---

## FAQ / Troubleshooting

**Q: FK-01 is not recognized in WSL?**

A: Attach the USB device to WSL first (e.g. `usbip attach -r 127.0.0.1 -b <bus-id>`), then call the tools.

**Q: I picked the wrong port for flashing?**

A: First call `list_ports()` and always pick the port with `role=fk_log` (WCH-LinkE VCP). The `role=fk_control` port is the FK-01 control port and must not be used for flashing or logging.

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

## Error Codes & Hints

Tool failures always return `[ERROR_CODE] message + next step: ...` (MCP `isError`),
so the agent and the user can tell what happened, whether to retry directly, and
which tool to call next. This table shares its source with the
`flashkey://docs/error-codes` resource (generated from `ERROR_GUIDE`).

| code | Meaning | Suggested next step |
| --- | --- | --- |
| DEVICE_NOT_FOUND | Device not plugged in / mounted | Plug in FK-01 and wait for the handshake; on WSL run `usbip attach` first, then retry |
| HANDSHAKE_FAILED | Handshake failed / reconnect timeout | Retry shortly; check the USB link |
| PORT_BUSY | Serial port busy / flashing in progress | Close programs holding the port and wait for flashing to finish, then retry |
| PORT_WRONG_ROLE | Wrong port role | Pick the port by `role` with `list_ports()` |
| AUTH_REQUIRED | Authentication required | Complete key authentication first (SET_KEY / flashkey_auth) |
| AUTH_FAILED | Authentication failed | Re-run SET_KEY to overwrite the flashing key |
| FLASH_PROTECTED | Flash read protection | The server already retries with auto-unlock; if it still fails, unlock with WCH-LinkUtility |
| FLASH_VERIFY_FAILED | Flash verification mismatch | Confirm `chip` matches the firmware, then reflash |
| MODULE_NO_RESPONSE | Module not responding | Check wiring / baud rate / whether it entered Boot |
| MODULE_MANIFEST_INVALID | Invalid module manifest | Check the I2C connection and the module manifest |
| TIMEOUT | Response timeout | Retry once; if it keeps timing out, check the connection and baud rate |
| FRAME_CRC | Frame CRC error | Retry directly |
| INVALID_ARG | Invalid argument | Fix the argument per the hint |
| INTERNAL_ERROR | Unclassified error | Retry; if it still fails, check the server log |
