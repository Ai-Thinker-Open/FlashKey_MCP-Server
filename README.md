# 🔑 flashkey-mcp

> FlashKey FK-01 的 MCP 通信库 — 让 AI Agent 直接控制 USB 烧录调试器。

## 安装

```bash
pip install flashkey-mcp
# 依赖: Python ≥ 3.10, pyserial, mcp (modelcontextprotocol/python-sdk)
```

## 快速开始

### Python API

```python
from flashkey_mcp import FlashKey

# 自动发现 FlashKey 设备
fk = FlashKey()

# 握手认证
if fk.commands.handshake():
    print("认证成功 ✅")

# 查询状态
print(fk.commands.get_status())

# 控制目标芯片
fk.commands.boot_set(True)   # BOOT 拉高
fk.commands.rst_pulse(50)    # RST 脉冲 50ms

fk.close()
```

### MCP Server（AI Agent 集成）

```bash
# 启动 MCP StdioServer
flashkey-mcp
# 或: python -m flashkey_mcp.server
```

### Hermes Agent 配置

编辑 `~/.hermes/config.yaml`：

```yaml
mcp_servers:
  flashkey:
    command: "flashkey-mcp"
    # 或使用 pip install -e 本地开发模式时：
    # command: "python"
    # args: ["-m", "flashkey_mcp.server"]
```

重启 Hermes 后，AI 即可使用以下 tools：

| Tool | 功能 | 需要认证 |
|:-----|:-----|:--------:|
| `flashkey_ping` | 检测连通性 | ❌ |
| `flashkey_auth_status` | 查询认证状态 | ❌ |
| `flashkey_boot_set/get` | BOOT 引脚控制 | ✅ |
| `flashkey_rst_set/get/pulse` | RST 引脚控制 | ✅ |
| `flashkey_v5v_set/get` | 5V 电源控制 | ✅ |
| `flashkey_v3v3_set/get` | 3.3V 电源控制 | ✅ |
| `flashkey_get_version/uid/status` | 设备信息查询 | ✅ |
| `flashkey_enter_bootloader` | 一键进入烧录模式 | ✅ |

## 通信协议

帧格式：`SOF=0x7E | LEN(=data_len+2) | CMD | DATA[N] | CRC-8(0x31) | EOF=0x7F`

支持 15 条命令：PING、CHALLENGE、RESPONSE、AUTH_STATUS、BOOT_SET/GET、RST_SET/GET/PULSE、V5V_SET/GET、V3V3_SET/GET、GET_VERSION、GET_UID、GET_STATUS。

## 目录结构

```
flashkey-mcp/
├── pyproject.toml
├── README.md
├── src/flashkey_mcp/
│   ├── __init__.py      # 包入口 + FlashKey 类
│   ├── transport.py     # USB CDC 串口发现与通信
│   ├── protocol.py      # 帧协议 + CRC-8 + 状态机
│   ├── auth.py          # Challenge-Response 认证算法
│   ├── commands.py      # 15 条命令封装
│   └── server.py        # MCP StdioServer
└── tests/
    ├── test_auth.py
    ├── test_commands.py
    └── test_s1_handshake.py
```

## 开发

```bash
git clone --recurse-submodules git@github.com:Ai-Thinker-Open/FlashKey.git
cd FlashKey/flashkey-mcp
python3.11 -m venv .venv311
source .venv311/bin/activate
pip install -e .
```

## 许可证

MIT · Ai-Thinker 安信可
