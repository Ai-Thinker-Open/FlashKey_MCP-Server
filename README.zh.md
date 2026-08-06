# 🔑 flashkey-mcp

> FlashKey FK-01 MCP 服务器 — 通用 USB 烧录调试器，支持任何 MCP 兼容的 AI 工具。

[![简体中文](https://img.shields.io/badge/简体中文-中文文档-brightgreen)](README.zh.md)
[![English](https://img.shields.io/badge/English-README-blue)](README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 项目简介

FlashKey FK-01 是安信可（Ai-Thinker）推出的双芯片 USB 烧录调试器。**flashkey-mcp** 是它的 MCP（Model Context Protocol）服务器插件，让 Claude Code、Claude Desktop、Cline、Hermes Agent、MiMo Code 等 AI 工具可以直接控制 FK-01 完成烧录、日志采集与调试：

- ⚡ 一键烧录 BL602 / BL616 / BL618 固件
- 📋 采集目标芯片串口日志
- 🔘 控制 BOOT / RST 引脚与 5V / 3.3V / VUSB 电源
- 🔄 升级 FK-01 自身 CH32V203 固件（OpenOCD + WCH-LinkE）

插入 FK-01 后自动握手，5 秒内完成；`flashkey_status()` 统一状态查询无需认证，开箱即用。

> 源码仓库：[Ai-Thinker-Open/FlashKey_MCP-Server](https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server)

---

## 安装 / 编译

### 一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/Ai-Thinker-Open/FlashKey_MCP-Server/master/setup.sh | bash
```

脚本自动完成：
1. 安装 flashkey-mcp
2. 检测系统上的 AI 工具，自动写入对应格式的 MCP 配置
3. 提示下一步操作

**支持自动配置的工具**：Claude Code、Claude Desktop、Cline、Hermes Agent、MiMo Code

### pip 安装（Git 源）

```bash
pip install git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git
```

需要 SSE 支持时：

```bash
pip install "flashkey-mcp[sse] @ git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git"
```

### 从源码编译 / 安装（开发者）

```bash
git clone git@github.com:Ai-Thinker-Open/FlashKey_MCP-Server.git
cd flashkey-mcp
pip install -e .
```

---

## 使用说明

flashkey-mcp 以 stdio MCP 服务器方式运行。**所有工具的 MCP 配置本质上相同：告诉工具用 `flashkey-mcp` 命令启动一个 stdio 子进程。**

### 手动配置

#### JSON 格式（Claude Code / Claude Desktop / Cline / VS Code）

在工具的 MCP 配置文件中添加：

```json
{
  "mcpServers": {
    "flashkey": {
      "command": "flashkey-mcp"
    }
  }
}
```

| 工具 | 配置文件路径 |
|------|-------------|
| Claude Code | `~/.claude/mcp.json` |
| Claude Desktop macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cline (VS Code) | `~/.cline/mcp.json` |

#### YAML 格式（Hermes Agent）

`~/.hermes/config.yaml`：

```yaml
mcp_servers:
  flashkey:
    command: flashkey-mcp
    args: []
    enabled: true
```

#### Cursor

Settings → MCP → Add new MCP server：
- 名称：`flashkey`
- 类型：`command`
- 命令：`flashkey-mcp`

#### MiMo Code

```bash
mimo mcp add flashkey-mcp --command flashkey-mcp
```

或项目根目录 `mimocode.json`：

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

### 验证安装

```bash
# 安装验证
flashkey-mcp --version

# 配置验证：重启 AI 工具后，在对话中调用
flashkey_status()
```

---

## 示例

### 示例 1：检查设备状态

在 AI 工具对话中调用：

```
flashkey_status()
```

返回 FK-01 是否在线、固件版本、可用串口等信息。

### 示例 2：一键烧录固件

```
flashkey_flash(firmware_path="/path/to/firmware.bin", chip="bl602", flash_port="ttyACM1")
```

> ⚠️ 端口选择：先调用 `flashkey_list_ports()` 查看端口列表，选择 `role=fk_log` 的端口；**不能**使用 `role=fk_control` 的端口（那是 FK-01 主控口，MCP 内部专用）。

### 示例 3：采集目标芯片日志

```
flashkey_log(port="ttyACM1", baud_rate=115200, duration=5, grep="ERROR")
```

---

## 工作原理

FlashKey FK-01 是双芯片 USB 烧录调试器。MCP 插件提供 27 个工具：

```
flashkey_status()          ← 统一状态，无需认证
flashkey_list_ports()      ← 列出所有串口

flashkey_flash()           ← 一键烧录 BL602/BL616/BL618
flashkey_log()             ← 采集目标芯片日志
flashkey_firmware_check()  ← 检查 FK-01 自身固件是否有更新
flashkey_firmware_flash()  ← 烧录 FK-01 自身 CH32V203 固件（OpenOCD + WCH-LinkE）

flashkey_boot_set/get()    ← BOOT 引脚控制
flashkey_rst_set/get/pulse()  ← RST 引脚控制
flashkey_v5v_set/get()     ← 5V 电源
flashkey_v3v3_set/get()    ← 3.3V 电源
flashkey_enter_bootloader()   ← 组合进入 ISP 模式
flashkey_ping() / flashkey_get_version() / flashkey_get_uid()
```

插入 FK-01 后自动握手，5 秒内完成。

---

## FK-01 自身固件升级（CH32V203）

`flashkey_firmware_check()` 比较设备当前固件版本、当前安装包内置固件版本与 GitHub 最新 Release 固件版本；`flashkey_firmware_flash()` 通过 WCH-LinkE（SDI）烧录 CH32V203（默认烧包内内置 hex，也可传 `hex_path`）。

⚠️ 烧录前置条件：把 FlashKey 自带的 WCH-LinkE 通过 USB 接入电脑，并将 SWDIO/SWCLK/GND/3V3 接到 CH32V203 的 SWD 接口且目标板上电；WSL 环境需先 `usbip attach`。烧录需要显式传 `confirm=True`；普通烧录失败且疑似读保护/写保护时，工具会自动用带 unlock 的全片擦除+烧录重试一次，仍失败会提示用 Windows 主机的 **WCH-LinkUtility** 手动解锁。

OpenOCD 二进制（WCH v1.6，Linux x64 / Windows x64）已随包内置在 `flashkey_mcp/openocd/`，无需单独安装；可用环境变量 `FLASHKEY_OPENOCD` 覆盖路径。随附组件的许可证见包内 `NOTICE-OPENOCD.md`。

---

## FAQ / 故障排查

**Q: 在 WSL 下无法识别 FK-01？**

A: 需要先把 USB 设备附加进 WSL（如 `usbip attach -r 127.0.0.1 -b <bus-id>`），然后再调用工具。

**Q: 烧录时选错端口？**

A: 先调用 `flashkey_list_ports()`，务必选择 `role=fk_log` 的端口（WCH-LinkE VCP）；`role=fk_control` 是 FK-01 主控口，不可用于烧录或日志。

**Q: 烧录失败并提示读保护/写保护？**

A: 工具会自动重试带 unlock 的全片擦除+烧录；仍失败时请用 Windows 主机的 WCH-LinkUtility 手动解锁。

**Q: 高波特率日志不稳定？**

A: WCH-LinkE VCP（fk_log）最高仅支持 921600，需要更高波特率时请改用外接 USB-UART。

**Q: 工具提示有新版？**

A: 执行 `pip install --upgrade git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git` 后重启 MCP 服务。

---

## 贡献指南

欢迎提交 Issue 与 Pull Request：

1. Fork 本仓库并创建特性分支；
2. 提交信息遵循 Conventional Commits 风格（如 `feat:`、`fix:`、`docs:`）；
3. 修改后请运行 `pytest` 保证测试通过；
4. 发起 PR 时说明改动目的与验证方式。

---

## License

[MIT](LICENSE) © 2026 Ai-Thinker Open
