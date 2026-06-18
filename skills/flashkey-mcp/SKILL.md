---
name: flashkey-mcp
description: FlashKey FK-01 MCP 插件 — 安装、配置、使用引导。让 AI 工具自主控制 USB 烧录调试器（BOOT/RST/电源/烧录）。
---

# flashkey-mcp — AI 安装使用引导

> 本 skill 告诉 AI 如何安装、配置和使用 flashkey-mcp 插件来控制 FlashKey FK-01 硬件。

## 安装

```bash
pip install flashkey-mcp
# 依赖：Python ≥ 3.10, pyserial, mcp (modelcontextprotocol/python-sdk)
```

## 配置（Hermes Agent）

在 `~/.hermes/config.yaml` 中添加：

```yaml
mcp_servers:
  flashkey:
    command: flashkey-mcp
    args: ["--stdio"]
    enabled: true
```

重启 Hermes 后，AI 自动获得 15 个 `flashkey_*` 工具。

## 硬件前提

- FlashKey FK-01 硬件通过 USB 连接 PC
- 插入后自动完成 Challenge-Response 握手（固件自动发 HELLO）
- PB11 LED：常亮 = 认证成功，慢闪 = 未认证/心跳超时

## 可用工具

| 工具 | 功能 | 需要认证 |
|:-----|:-----|:--------:|
| `flashkey_ping` | 检测设备连通性 | ❌ |
| `flashkey_auth_status` | 查询认证状态 | ❌ |
| `flashkey_boot_set/get` | BOOT 引脚 (PB3) 控制 | ✅ |
| `flashkey_rst_set/get/pulse` | RST 引脚 (PB4) 控制 + 脉冲 | ✅ |
| `flashkey_v5v_set/get` | 5V 电源 (PB1, 低有效) | ✅ |
| `flashkey_v3v3_set/get` | 3.3V 电源 (PB0, 高有效) | ✅ |
| `flashkey_get_version` | 读取固件版本 | ✅ |
| `flashkey_get_uid` | 读取设备唯一 ID | ✅ |
| `flashkey_get_status` | 查询全部引脚 + 认证状态 | ✅ |
| `flashkey_enter_bootloader` | BOOT↑→RST↓ 进入烧录模式 | ✅ |

## 典型工作流

```
1. flashkey_ping()                    → 验证设备连接
2. flashkey_auth_status()             → 确认已认证
3. flashkey_enter_bootloader()        → BOOT↑→RST↓ 脉冲→目标芯片进烧录模式
4. 通过 CH340C 串口烧录固件
5. flashkey_rst_pulse(ms=50)          → 复位目标芯片
```

## 引脚映射

| 功能 | 引脚 | 电平逻辑 |
|:-----|:-----|:---------|
| BOOT | PB3 | val=True→HIGH, val=False→LOW |
| RST | PB4 | val=True→HIGH, val=False→LOW；pulse(ms) 拉低 Nms 后恢复 |
| 5V_EN | PB1 | 低有效：False=开, True=关 |
| 3.3V_EN | PB0 | 高有效：True=开, False=关 |

## Python API（直接调用）

```python
from flashkey_mcp import FlashKey

fk = FlashKey()
# 自动握手已完成，直接使用
print(fk.commands.get_status())
fk.commands.boot_set(True)
fk.commands.rst_pulse(50)
fk.close()
```

> 注意：flashkey-mcp 是一个**标准 MCP 协议插件**，支持 Hermes / Claude Desktop / Cursor / Cline 等所有兼容 MCP 的 AI 工具。
