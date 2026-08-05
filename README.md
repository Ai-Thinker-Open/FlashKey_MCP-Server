# 🔑 flashkey-mcp

> FlashKey FK-01 MCP 服务器 — 通用 USB 烧录调试器，支持任何 MCP 兼容的 AI 工具。

---

## 一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/Ai-Thinker-Open/flashkey-mcp/main/setup.sh | bash
```

脚本自动完成：
1. 安装 flashkey-mcp
2. 检测系统上的 AI 工具，自动写入对应格式的 MCP 配置
3. 提示下一步操作

**支持自动配置的工具**：Claude Code、Claude Desktop、Cline、Hermes Agent、MiMo Code

---

## 手动配置

如果自动脚本无法覆盖你的 AI 工具，手动添加 MCP 配置。

### 通用规则

所有工具的 MCP 配置本质上相同：**告诉工具用 `flashkey-mcp` 命令启动一个 stdio 子进程。**

### JSON 格式（Claude Code / Claude Desktop / Cline / VS Code）

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

### YAML 格式（Hermes Agent）

`~/.hermes/config.yaml`：

```yaml
mcp_servers:
  flashkey:
    command: flashkey-mcp
    args: []
    enabled: true
```

### Cursor

Settings → MCP → Add new MCP server：
- 名称：`flashkey`
- 类型：`command`
- 命令：`flashkey-mcp`

### MiMo Code

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

---

## 验证

```bash
# 安装验证
flashkey-mcp --version

# 配置验证：重启 AI 工具后，在对话中调用
flashkey_status()
```

---

## 工作原理

FlashKey FK-01 是双芯片 USB 烧录调试器。MCP 插件提供 21 个工具：

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

`flashkey_firmware_check()` 比较设备当前固件版本、当前安装包内置固件版本与
GitHub 最新 Release 固件版本；`flashkey_firmware_flash()` 通过 WCH-LinkE（SDI）
烧录 CH32V203（默认烧包内内置 hex，也可传 `hex_path`）。

⚠️ 烧录前置条件：把 FlashKey 自带的 WCH-LinkE 通过 USB 接入电脑，并将
SWDIO/SWCLK/GND/3V3 接到 CH32V203 的 SWD 接口且目标板上电；WSL 环境需先
`usbip attach`。烧录需要显式传 `confirm=True`；普通烧录失败且疑似读保护/
写保护时，工具会自动用带 unlock 的全片擦除+烧录重试一次，仍失败会提示用
Windows 主机的 **WCH-LinkUtility** 手动解锁。

OpenOCD 二进制（WCH v1.6，Linux x64 / Windows x64）已随包内置在
`flashkey_mcp/openocd/`，无需单独安装；可用环境变量 `FLASHKEY_OPENOCD`
覆盖路径。随附组件的许可证见包内 `NOTICE-OPENOCD.md`。

---

## 从本地源码安装（开发者）

```bash
git clone git@github.com:Ai-Thinker-Open/flashkey-mcp.git
cd flashkey-mcp
pip install -e .
```
