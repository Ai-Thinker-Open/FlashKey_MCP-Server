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

尝试调用 `flashkey_status()` —— 如果工具存在且返回了结果（包括无设备时的 `authed: false`），说明 MCP 已配置且 FK-01 server 在运行。**直接跳到步骤 3。**

如果工具不存在（`tool not found` 或类似错误），说明 MCP 还未配置，继续步骤 1。

---

## 步骤 1：安装 flashkey-mcp

```bash
pip install git+https://github.com/Ai-Thinker-Open/flashkey-mcp.git
```

如果失败（网络/权限），尝试：

```bash
pip install --user git+https://github.com/Ai-Thinker-Open/flashkey-mcp.git
```

验证：

```bash
flashkey-mcp --help
```

应该看到 `FlashKey FK-01 MCP Server` 的帮助信息。

---

## 步骤 2：配置 MCP 自动启动

在 AI 工具的 MCP 配置文件中添加 flashkey 服务器。不同工具配置路径和格式如下：

### Claude Code / Claude Desktop

配置文件：
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "flashkey": {
      "command": "flashkey-mcp",
      "args": []
    }
  }
}
```

如果文件已有其他 MCP server，在 `mcpServers` 对象内追加 `flashkey` 条目，不要覆盖已有的。

### Cursor

Cursor Settings → MCP → Add new MCP server：
- 名称：`flashkey`
- 类型：`command`
- 命令：`flashkey-mcp`

### Cline (VS Code)

配置文件 `~/.cline/mcp.json`：

```json
{
  "mcpServers": {
    "flashkey": {
      "command": "flashkey-mcp",
      "args": []
    }
  }
}
```

### Hermes Agent

配置文件路径（按优先级尝试）：
1. `~/.hermes/config.yaml`
2. `~/.hermes/profiles/product/config.yaml`

```yaml
mcp_servers:
  flashkey:
    command: flashkey-mcp
    args: []
    enabled: true
```

配置完成后告诉用户：

> "flashkey-mcp 已安装并配置完成。请**重启**当前 AI 工具使 MCP 生效。重启后插入 FlashKey FK-01，直接告诉我要烧录的固件和芯片型号即可。"

---

## 步骤 3：烧录 + 日志（MCP 工具已可用）

当 `flashkey_status()` 返回 `authed: true` 时，一句话即可完成：

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
