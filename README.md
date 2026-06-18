# 🔑 flashkey-mcp — MCP Plugin for FlashKey FK-01

> **让 AI Agent 直接控制 USB 烧录调试器。** 标准 MCP 协议，任何支持 MCP 的 AI 工具（Hermes、Claude Desktop、Cursor、Cline 等）即装即用。

---

## 🚀 快速开始（给 AI 工具看的）

把本仓库给 AI 工具，按以下两步即可：

### ① 安装

```bash
pip install flashkey-mcp
# 依赖: Python ≥ 3.10, pyserial, mcp
```

### ② 配置 AI 工具连接

<details>
<summary><b>Hermes Agent</b> — <code>~/.hermes/config.yaml</code></summary>

```yaml
mcp_servers:
  flashkey:
    command: flashkey-mcp
    args: ["--stdio"]
    enabled: true
```
</details>

<details>
<summary><b>Claude Desktop</b> — <code>claude_desktop_config.json</code></summary>

```json
{
  "mcpServers": {
    "flashkey": {
      "command": "flashkey-mcp",
      "args": ["--stdio"]
    }
  }
}
```
</details>

<details>
<summary><b>Cursor</b> — Cursor Settings → MCP</summary>

| 配置项 | 值 |
|:-------|:---|
| 名称 | `flashkey` |
| 类型 | `command` |
| 命令 | `flashkey-mcp --stdio` |
</details>

<details>
<summary><b>Cline (VS Code)</b> — <code>.cline/mcp.json</code></summary>

```json
{
  "mcpServers": {
    "flashkey": {
      "command": "flashkey-mcp",
      "args": ["--stdio"]
    }
  }
}
```
</details>

> 🔐 **自动握手**：插入 FK-01 后固件自动发 HELLO 帧完成 Challenge-Response 认证，无需手动调用 handshake。

---

## 🤖 可用工具

配置完成后，AI 工具自动发现以下 15 个工具：

| 工具 | 功能 | 需要认证 |
|:-----|:-----|:--------:|
| `flashkey_ping` | 检测设备连通性 | ❌ |
| `flashkey_auth_status` | 查询认证状态 | ❌ |
| `flashkey_boot_set/get` | BOOT 引脚 (PB3) 控制 | ✅ |
| `flashkey_rst_set/get/pulse` | RST 引脚 (PB4) 控制 + 脉冲 | ✅ |
| `flashkey_v5v_set/get` | 5V 电源 (PB1, 低有效) 控制 | ✅ |
| `flashkey_v3v3_set/get` | 3.3V 电源 (PB0, 高有效) 控制 | ✅ |
| `flashkey_get_version` | 读取固件版本 | ✅ |
| `flashkey_get_uid` | 读取设备唯一 ID | ✅ |
| `flashkey_get_status` | 查询全部引脚 + 认证状态 | ✅ |
| `flashkey_enter_bootloader` | BOOT↑→RST↓ 进入烧录模式 | ✅ |

### 典型工作流

```
AI Agent 自动完成：
  ① flashkey_enter_bootloader()   ← BOOT↑→RST↓脉冲→进入烧录模式
  ② 调用烧录工具写固件            ← 通过 CH340C 串口
  ③ flashkey_rst_pulse()          ← 复位目标芯片
```

---

## 📦 安装方式

### 从 PyPI（推荐，发布后可用）

```bash
pip install flashkey-mcp
```

### 从本地源码（开发者）

```bash
git clone git@github.com:Ai-Thinker-Open/flashkey-mcp.git
cd flashkey-mcp
pip install -e .
```

### 运行模式

| 模式 | 命令 | 说明 |
|:----|:-----|:-----|
| **⭐ Stdio（推荐）** | `flashkey-mcp --stdio` | AI 工具 `command` 自动启动，标准 MCP 插件方式 |
| SSE（备选） | `flashkey-mcp` | 手动启动 HTTP 服务（端口 8100），`url` 连接 |

---

## 🐍 Python API（直接调用）

```python
from flashkey_mcp import FlashKey

fk = FlashKey()

# 自动认证（固件已自动握手）
print(fk.commands.get_status())

# 控制目标芯片
fk.commands.boot_set(True)   # BOOT 拉高
fk.commands.rst_pulse(50)    # RST 脉冲 50ms
fk.commands.v5v_set(False)   # 5V 开启

fk.close()
```

---

## 📐 通信协议

帧格式：
```
SOF=0x7E | LEN(=data_len+2) | CMD | DATA[N] | CRC-8(0x31/MAXIM) | EOF=0x7F
```

支持 15 条命令：PING、CHALLENGE、RESPONSE、AUTH_STATUS、BOOT_SET/GET、RST_SET/GET/PULSE、V5V_SET/GET、V3V3_SET/GET、GET_VERSION、GET_UID、GET_STATUS

---

## 📁 目录结构

```
flashkey-mcp/
├── pyproject.toml
├── README.md
├── src/flashkey_mcp/
│   ├── __init__.py      # 包入口 + FlashKey 类
│   ├── transport.py     # USB CDC 串口发现与通信
│   ├── protocol.py      # 帧协议 + CRC-8 + 状态机
│   ├── auth.py          # Challenge-Response 认证算法
│   ├── commands.py      # 命令封装
│   └── server.py        # MCP Server (stdio + SSE)
└── tests/
    └── ...
```

---

## 🏠 硬件来源

FlashKey FK-01 硬件固件源码位于主仓库：
[`Ai-Thinker-Open/FlashKey`](https://github.com/Ai-Thinker-Open/FlashKey)
（firmware/ch32v203/ — CH32V203C8T6 固件）

---

## 📜 许可证

MIT · Ai-Thinker 安信可
