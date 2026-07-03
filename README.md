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

FlashKey FK-01 是双芯片 USB 烧录调试器。MCP 插件提供 19 个工具：

```
flashkey_status()          ← 统一状态，无需认证
flashkey_list_ports()      ← 列出所有串口

flashkey_flash()           ← 一键烧录 BL602/BL616/BL618
flashkey_log()             ← 采集目标芯片日志

flashkey_boot_set/get()    ← BOOT 引脚控制
flashkey_rst_set/get/pulse()  ← RST 引脚控制
flashkey_v5v_set/get()     ← 5V 电源
flashkey_v3v3_set/get()    ← 3.3V 电源
flashkey_enter_bootloader()   ← 组合进入 ISP 模式
flashkey_ping() / flashkey_get_version() / flashkey_get_uid()
```

插入 FK-01 后自动握手，5 秒内完成。

---

## 从本地源码安装（开发者）

```bash
git clone git@github.com:Ai-Thinker-Open/flashkey-mcp.git
cd flashkey-mcp
pip install -e .
```
