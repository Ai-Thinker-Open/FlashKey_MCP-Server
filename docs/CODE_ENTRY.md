[![中文](https://img.shields.io/badge/中文-文档-blue)](CODE_ENTRY.zh.md)

# Code entry

## Process entry

The installed command is declared in `pyproject.toml`:

```toml
[project.scripts]
flashkey-mcp = "flashkey_mcp.server:main"
```

`flashkey_mcp.server.main()` is therefore the process entry. It installs the runtime guard, parses service, upgrade, version, host, port, SSE and stdio options, initializes logging and obtains the shared `DeviceManager`.

## Transport entry

- The default path calls `_run_sse(host, port)`. It serves Streamable HTTP on `/mcp`, classic SSE on `/sse` with its message route, and compatibility endpoints `/release` and `/reconnect`.
- Legacy stdio mode calls `mcp.run(transport="stdio")`.
- The module-level `FastMCP` object registers the MCP tools, resources and prompts before either transport starts.

## Hardware operation path

```text
MCP client
  -> server.py tool handler
  -> DeviceManager.require_authed()
  -> commands.py operation
  -> protocol.py frame/CRC handling
  -> transport.py serial transport
  -> FK-01 hardware
```

The background manager normally advances through `DISCONNECTED -> CONNECTING -> AUTHED`. After the idle timeout it releases the serial port and enters `IDLE`; the next hardware tool call wakes detection and authentication again.

## Starting points for maintenance

- Change MCP request handling or endpoints in `src/flashkey_mcp/server.py`.
- Change device discovery and lifecycle in `src/flashkey_mcp/device_manager.py`.
- Change wire commands, framing or serial I/O in `commands.py`, `protocol.py` and `transport.py` respectively.
- Add unit coverage under `tests/`; hardware-only scripts remain explicit manual checks.
