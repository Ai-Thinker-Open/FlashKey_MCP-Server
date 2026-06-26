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
    args: []
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
      "args": []
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
| 命令 | `flashkey-mcp` |
</details>

<details>
<summary><b>Cline (VS Code)</b> — <code>.cline/mcp.json</code></summary>

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
</details>

> 🔐 **自动握手**：插入 FK-01 后固件自动发 HELLO 帧完成 Challenge-Response 认证，无需手动调用 handshake。
>
> 🤖 **AI 自助引导**：本仓库 `skills/flashkey-mcp/SKILL.md` 提供了完整的安装/配置/使用引导，Hermes Agent 加载后可自动完成全部流程。

---

## 🤖 可用工具

配置完成后，AI 工具自动发现以下 19 个工具：

| 工具 | 功能 | 需要认证 |
|:-----|:-----|:--------:|
| `flashkey_status` | **新** 统一状态查询（认证、版本、引脚） | ❌ |
| `flashkey_list_ports` | **新** 列出系统所有可用串口 | ❌ |
| `flashkey_flash` | **新** 一键烧录固件 (BOOT→RST→esptool→恢复) | ✅ |
| `flashkey_log` | **新** 采集目标芯片串口日志 | ✅ |
| `flashkey_ping` | 检测设备连通性 | ✅ |
| `flashkey_auth_status` | 查询认证状态 ⚠️ 已弃用 | ✅ |
| `flashkey_boot_set/get` | BOOT 引脚 (PB3) 控制 | ✅ |
| `flashkey_rst_set/get/pulse` | RST 引脚 (PB4) 控制 + 脉冲 | ✅ |
| `flashkey_v5v_set/get` | 5V 电源 (PB1, 低有效) 控制 | ✅ |
| `flashkey_v3v3_set/get` | 3.3V 电源 (PB0, 高有效) 控制 | ✅ |
| `flashkey_get_version` | 读取固件版本 | ✅ |
| `flashkey_get_uid` | 读取设备唯一 ID | ✅ |
| `flashkey_get_status` | 读取引脚状态 ⚠️ 已弃用，用 flashkey_status | ✅ |
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
| **⭐ Stdio（默认）** | `flashkey-mcp` | AI 工具 `command` 自动启动，标准 MCP 插件方式 |
| **SSE（备选）** | `flashkey-mcp --sse` | 手动启动 HTTP 服务（端口 8100），需安装 `pip install flashkey-mcp[sse]` |

### 🔄 USB 映射释放（SSE 模式）

当 AI Agent（Hermes/Claude 等）在 **WSL** 中运行，而 FK-01 插在 Windows 宿主机时，flashkey-mcp 占用 COM 端口会阻止 `usbipd` 将设备映射到 WSL。

SSE 模式提供两个 HTTP 端点让用户手动控制释放与重连：

| 端点 | 说明 |
|:-----|:-----|
| `POST /release` | 关闭串口、清空认证、暂停自动重连 → 设备可被 usbipd 绑定到 WSL |
| `POST /reconnect` | 重新扫描 FK-01、打开串口、自动 HELLO 握手 → 恢复连接 |

**典型工作流：**

```
① flashkey-mcp 正常运行（SSE 模式，Windows 侧）
         │
         ▼
② curl -X POST http://localhost:8100/release
   → {"status": "released"}
         │
         ▼
③ usbipd bind --busid <ID> --wsl       ← COM 已释放，绑定成功
         │
         ▼
④ WSL 中使用 FK-01（烧录调试等）
         │
         ▼
⑤ usbipd detach --busid <ID>
   → COM 口重新出现在 Windows
         │
         ▼
⑥ curl -X POST http://localhost:8100/reconnect
   → {"status": "connected", "authed": true}
```

> 💡 释放后如果误调用了 MCP 工具，会返回明确的错误提示"已释放，请调用 /reconnect 恢复"，不会自动重连。

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
│   ├── __init__.py          # 包入口 + FlashKey 类
│   ├── transport.py         # USB CDC 串口发现与通信 + list_all_ports
│   ├── protocol.py          # 帧协议 + CRC-8 + 状态机
│   ├── auth.py              # Challenge-Response 认证算法
│   ├── commands.py          # 命令封装 (15 条)
│   ├── device_manager.py    # 设备生命周期管理 (热插拔+自动握手+保活)
│   └── server.py            # MCP Server (19 工具, stdio 默认 + SSE可选)
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
