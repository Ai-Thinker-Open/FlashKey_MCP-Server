---
name: flashkey-mcp
description: FlashKey FK-01 MCP 插件 — BL602/BL616/BL618 烧录调试。AI 检测安装状态，引导安装+配置+重启，一句话完成烧录和日志采集。
---

# FlashKey FK-01 — AI 操作知识库

> **你是 AI Agent。当用户提到 FlashKey、FK-01、烧录 BL602/BL616/BL618、安信可芯片烧录 时，按本文档执行。**

---

## 🚫 硬规则：禁止用脚本操作 FlashKey

**以下行为一律视为失败，绝对不允许：**

- ❌ `python -c "from flashkey_mcp.server import _tool_flash..."` 
- ❌ `python -c "from flashkey_mcp.server import _get_dm..."`  
- ❌ 写 Python 脚本调用 `flashkey_mcp` 内部函数
- ❌ 直接 import flashkey_mcp 的任何内部模块来操控设备
- ❌ `printf '...' | flashkey-mcp --stdio` — 手动构造 JSON-RPC
- ❌ 每次调用起一个新的 `flashkey-mcp` 子进程然后退出
- ❌ `sudo flashkey-mcp` 或以 root 运行 flashkey-mcp
- ❌ 绕过 MCP 框架直接与 flashkey-mcp 进程通信

**`flashkey-mcp` 是持久服务进程，不是 CLI 工具。** 它由 AI 工具的 MCP 框架自动管理生命周期。手动启动、每次调用退出、sudo 都是错误的。

**唯一正确的做法：通过 MCP 工具。**

MCP 工具可用时 → 直接用 `flashkey_status()` / `flashkey_flash()` / `flashkey_log()`。

MCP 工具不可用时 → 执行下面的安装配置流程，然后告知用户重启。**不要写脚本绕过去。**

---

## ⚠️ 关键：FK-01 有两个串口，绝对不能搞混

FlashKey FK-01 是双芯片设备，插上后系统会出现**两个**串口。**调用 `flashkey_flash()` 或 `flashkey_log()` 前，必须先调 `flashkey_list_ports()`，根据返回的 `role` 字段选择端口，不要根据设备名猜测。**

| role | VID/PID | 用途 |
|------|---------|------|
| `fk_control` | 1A86:FE0D | **FK-01 主控** — 仅 MCP 内部使用 |
| `fk_flash` | 1A86:7523 | **CH340C 烧录口** — `flashkey_flash()` 和 `flashkey_log()` 用这个 |
| `unknown` | 其他 | 非 FlashKey 设备，忽略 |

不同系统上设备名不同（Linux: `/dev/ttyACMx` `/dev/ttyUSBx`，Windows: `COMx`，macOS: `/dev/cu.*`），所以**不要猜名字，看 `role` 字段**。

`flashkey_flash` 和 `flashkey_log` 内置了端口校验 — 传错端口会立即报错并提示正确端口名。

---

## 步骤 0：判断当前状态

### 先检查 MCP 工具是否可用

**直接用 AI 工具的原生 function call 调用 `flashkey_status()`**。不要用 shell 命令、不要 ps 查进程、不要检查配置文件。

- 调用成功 → MCP 已连接。**直接跳到步骤 3。**
- 返回 `tool not found` / `unknown tool` → MCP 未连接，继续步骤 1 检查服务状态。

---

## 步骤 1：确认 Python 版本 + 安装

### 1a. 检查 Python 版本

```bash
python3 --version
```

- **>= 3.10** → 用 `pip install` / `python3 -m pip install`，继续 1b
- **< 3.10** → 先检查有没有其他 Python 版本：

```bash
python3.11 --version 2>/dev/null || python3.12 --version 2>/dev/null || python3.10 --version 2>/dev/null
```

如果有 3.10+，所有命令用该版本代替，例如 `python3.11 -m pip install ...`。

如果都没有，安装 Python 3.10+：

| 系统 | 命令 |
|------|------|
| Ubuntu/Debian | `sudo apt install python3.12 python3.12-venv` |
| macOS | `brew install python@3.12` |
| Windows | `winget install Python.Python.3.12` |

安装后用 `python3.12 -m pip install ...` 代替 `pip install ...`。

### 1b. 安装 flashkey-mcp

```bash
pip install git+https://github.com/Ai-Thinker-Open/flashkey-mcp.git
```

安装后验证：

```bash
which flashkey-mcp && flashkey-mcp --version
```

如果 `which flashkey-mcp` 找不到（pip 装到了非 PATH 目录），链接到 PATH：

```bash
# 找到 flashkey-mcp 的实际位置
find / -name flashkey-mcp -type f 2>/dev/null | head -3
# 链接到 ~/.local/bin
ln -sf <实际路径> ~/.local/bin/flashkey-mcp
```

如果失败，检查 Python 版本（必须 >= 3.10），或尝试：

```bash
pip install --user git+https://github.com/Ai-Thinker-Open/flashkey-mcp.git
```

---

## 步骤 2：选择运行模式

flashkey-mcp 支持两种运行模式。**只能选一种，不能同时跑。** 两个进程会争抢 FK-01 串口，导致其中一个 `device busy`。config 格式必须和运行模式匹配。

### 路径 A：stdio 模式（默认 — 兼容所有 AI 工具）

```bash
pip install git+https://github.com/Ai-Thinker-Open/flashkey-mcp.git
```

MCP config（不要写死路径，用命令名）：

```json
{"flashkey": {"type": "stdio", "command": "flashkey-mcp", "args": []}}
```

AI 工具启动时自动拉起 flashkey-mcp 子进程。重启后生效。**所有 AI 工具都支持 stdio。**

### 路径 B：SSE 服务模式（服务独立运行，开机自启，需工具支持 SSE）

```bash
pip install "flashkey-mcp[sse] @ git+https://github.com/Ai-Thinker-Open/flashkey-mcp.git"
flashkey-mcp --service install    # 安装 systemd 用户服务，立即启动 + 开机自启
```

MCP config：

```json
{"flashkey": {"type": "sse", "url": "http://127.0.0.1:8100/sse"}}
```

优点：服务独立，重启 AI 工具不丢失设备状态。**需要 AI 工具支持 SSE 传输。**

### ⚠️ 模式不能混用，也不能同时跑

- SSE 服务 + stdio config → 工具不可用（config 不匹配）
- 同时配了 SSE 服务 AND stdio config → 两个 flashkey-mcp 进程抢串口，必有一个报 `device busy`
- 切换模式时：先停掉旧的（`--service uninstall` 或删 MCP config），再启新的

### 配置文件位置

| 工具 | 配置文件 |
|------|---------|
| Claude Code (CLI) | `~/.claude/mcp.json` |
| MiMo Code | `~/.mimocode/mcp.json` |
| Claude Desktop macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop Win | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cline (VS Code) | `~/.cline/mcp.json` |
| Hermes Agent | `~/.hermes/config.yaml` |

### 诊断：服务在跑但工具不可用？

```bash
flashkey-mcp --service status    # 检查 SSE 服务状态
journalctl --user -u flashkey-mcp -f   # 查看服务日志
tail -f /tmp/flashkey-mcp.log          # 查看文件日志
```

常见原因：config 格式和运行模式不匹配。例如服务跑 SSE 但 config 写的 `"command"`。修正 config 后 AI 工具应该自动重连。

---

## 步骤 3：触发握手 + 烧录 + 日志

### 3a. 先触发设备发现和握手

**调用 `flashkey_status()`。** 这是 MCP 连接建立后第一个应该调用的工具。它触发 DeviceManager 启动后台线程，扫描 FK-01，自动完成 HELLO 握手。调用后等 3-5 秒再调一次确认 `authed: true`。

```
flashkey_status()  → 触发 DeviceManager 初始化 → 扫描 FK-01 → HELLO 握手 → 返回 authed
```

### 3b. 烧录

当 `flashkey_status()` 返回 `authed: true` 时：

### 烧录

```
flashkey_flash(firmware_path="/path/to/firmware.bin", flash_port="自动检测的端口", chip="bl616")
```

`flashkey_flash` 自动处理：BOOT 拉高 → RST 脉冲 → 调用 SDK make flash → 烧录完成后恢复芯片。

### 烧录后看日志

```
flashkey_log(port="同上端口", duration=5, grep="Hello World")
```

### 烧录后芯片不启动？

```
flashkey_rst_pulse(50)
flashkey_log(port="...", duration=5)
```

---

## 烧录模式说明

| chip | 默认 mode | 行为 |
|------|:--------:|------|
| `bl602` | `break` | 先启动 `make flash` → 等待复位提示 → FlashKey RST 脉冲 → 烧录 |
| `bl616` | `isp` | BOOT↑ → RST 脉冲 → `make flash` → 恢复 |
| `bl618` | `isp` | 同 BL616 |

BL602 的 break 模式关键词检测（不区分大小写）：`reset`, `rest`, `press`, `uart`, `复位`。30 秒未检测到复位提示则报错，建议尝试 `mode="isp"`。

---

## 故障排查

```
flashkey_flash() 失败
├─ flashkey_status() 先检查 authed / boot / rst / v5v / v3v3 状态
├─ authed: false → 拔出 FK-01 重新插入，等 5 秒
├─ "make: No rule" → sdk_path 不对，或用 tool 参数指定
├─ "Failed to connect" / "shake hand fail" / ROM bootloader 无响应
│   ├─ FK-01 电源已开但 BL602 仍无响应 → 硬件连接问题，检查：
│   │   1. CH340C TX → BL602 GPIO7(RX)、CH340C RX → BL602 GPIO16(TX) 接线
│   │   2. Ai-WB2 模块可能需要板载 USB 独立供电，仅靠 FK-01 3.3V 可能不够
│   │   3. 确认模块型号的电源要求（部分模组需 5V + 3.3V 同时供电）
│   └─ 手动验证：按住 BOOT 按键 + 按 RESET → 看串口是否有 bootloader 输出
├─ CH340C 被占用 → 关闭串口监视器
├─ PING keepalive 在烧录中丢失 → 已修复：烧录期间自动暂停 PING，烧录后恢复
└─ 烧录成功但不启动 → rst_pulse(50) + flashkey_log(port, duration=5)
```

---

## 芯片领域知识

| chip | 烧录命令模板 | 默认波特率 | SDK |
|------|------------|:--------:|-----|
| `bl602` | `make flash p={port} b={baud}` | 921600 | Ai-Thinker-WB2 |
| `bl616` | `make flash CHIP=bl616 COMX={port} BAUDRATE={baud}` | 2000000 | bouffalo_sdk |
| `bl618` | `make flash CHIP=bl618 COMX={port} BAUDRATE={baud}` | 2000000 | bouffalo_sdk |

### 启动日志特征

| 芯片 | 正常启动关键词 | 等待时间 |
|------|---------------|:------:|
| BL602 | `Booting BL602...` 或 `[OS] Starting` | 1s |
| BL616 | `Starting ...` 或 `Hello World!` | 2s |
| BL618 | 同 BL616 | 2s |

### 平台陷阱

- **Windows COM10+**：必须写 `\\.\COM10`
- **WSL**：FK-01 + CH340C 都需要 `usbipd` 映射
- **串口互斥**：`flashkey_log` 和 `flashkey_flash` 共用 CH340C，不能同时调
- **v5v 反直觉**：`v5v_set(True)` = PB1 LOW = 开启 5V（低电平有效）

### 引脚参考

| 功能 | 引脚 | 默认 | 控制 |
|------|------|------|------|
| BOOT | PB3 | HIGH | `boot_set()` |
| RST | PB4 | HIGH | `rst_set()`/`rst_pulse()` |
| 5V_EN | PB1 | HIGH=OFF | `v5v_set()` — 低有效 |
| 3.3V_EN | PB0 | LOW=OFF | `v3v3_set()` — 高有效 |
