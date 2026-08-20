[![中文](https://img.shields.io/badge/中文-文档-blue)](ARCHITECTURE.zh.md)

# Architecture

## Runtime overview

```text
MCP clients
    |  stdio / SSE / Streamable HTTP
server.py  -------------------- guide.py
    |                           resources, prompts, error hints
DeviceManager
    |---- auth.py               challenge-response authentication
    |---- commands.py           FK-01 operations
    |       `-- protocol.py     frame encoding, parsing and CRC
    |               `-- transport.py   serial discovery and I/O
    |---- modules.py            extension manifests and dynamic tools
    `---- events.py             event recording and webhooks

firmware_tools.py               bundled firmware/OpenOCD and upgrades
singleton.py                    one server process per host
```

## Responsibilities

| Area | Source | Responsibility |
| --- | --- | --- |
| MCP boundary | `server.py` | CLI, transports, tools, resources, prompts and HTTP routes |
| Device lifecycle | `device_manager.py` | Discovery, handshake, state transitions, keepalive and idle release |
| Hardware commands | `commands.py` | High-level FK-01 commands |
| Wire protocol | `protocol.py` | Frames, parsing, CRC and request/response exchange |
| Serial layer | `transport.py` | Port roles, serial transport and device selection |
| Authentication | `auth.py` | Challenge-response handshake |
| Extensions | `modules.py` | Module manifests and dynamically exposed tools |
| Guidance | `guide.py` | MCP resources, prompts and actionable error guidance |
| Events | `events.py` | Operation events, recording and webhook delivery |
| Firmware support | `firmware_tools.py` | Packaged firmware, OpenOCD, update and flash helpers |
| Process safety | `singleton.py` | Cross-process lock on POSIX and Windows |

Runtime assets live under `src/flashkey_mcp/firmware/`; service templates and host configuration live under `src/flashkey_mcp/configs/`. They are packaged by the declarations in `pyproject.toml`.

## State ownership

`DeviceManager` owns the serial device state and is shared by server tool handlers. Hardware operations must call its authenticated access path rather than opening ports independently. Long operations mark activity so the idle-release mechanism does not close the device midway.

The process lock prevents two local server instances from competing for the same FK-01. It uses `fcntl.flock` on POSIX and `msvcrt.locking` on Windows.

## Extension boundaries

New protocol behavior should be separated by layer: add frame behavior in `protocol.py`, device commands in `commands.py`, lifecycle behavior in `device_manager.py`, and only the MCP-facing orchestration in `server.py`. Keep real-device tests separate from unattended tests so CI and local validation cannot flash or power hardware implicitly.
