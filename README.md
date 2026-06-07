# flashkey-mcp

> FlashKey FK-01 的 AI 插件——让 AI Agent 通过 MCP 协议控制烧录/调试硬件。

`pip install flashkey-mcp` 一条命令，AI Agent 就能发现、连接、控制 FlashKey 硬件。

---

## 这是什么

FlashKey FK-01 是一款 USB 智能烧录调试一体机。这个 Python 包是它的 **AI 控制接口**——封装成 MCP Server，让 Hermes / Claude / Cursor 等 AI 工具能直接操作物理硬件。

### 用户看到的

```
用户说："帮我烧录这块 BL618"

Agent 自动：
  1. 发现 FlashKey 设备（USB 串口）
  2. 连接 + 握手认证
  3. 拉高 BOOT → 脉冲 RST → 进入烧录模式
  4. 通过串口烧录固件
  
全程用户只说了一句话。
```

---

## 快速安装

```bash
pip install flashkey-mcp
```

在 Hermes config.yaml 添加：

```yaml
mcp_servers:
  flashkey:
    command: "flashkey-mcp"
    args: []
```

重启 Hermes，即可使用。

---

## 核心工具

| 分组 | 工具 | 做什么 |
|------|------|--------|
| **发现** | `flashkey_list_devices` | 扫描 USB 找出 FlashKey 串口 |
| **连接** | `flashkey_connect` / `flashkey_disconnect` | 打开/关闭串口 |
| **认证** | `flashkey_ping` / `flashkey_handshake` | 验真 + Challenge-Response 握手 |
| **GPIO** | `boot_set` / `boot_get` / `rst_set` / `rst_get` / `rst_pulse` | 控制目标板 BOOT/RST 引脚 |
| **电源** | `v5v_set` / `v5v_get` / `v3v3_set` / `v3v3_get` | 控制目标板 5V/3.3V 供电 |
| **一键烧录** | `flashkey_enter_bootloader(target)` | 传入 bl618/esp32/stm32 自动执行烧录时序 |
| **串口透传** | `serial_open` / `serial_send` / `serial_read` / `serial_close` | 通过 CH340C 与目标板通信 |

完整清单见 [docs/TOOLS.md](docs/TOOLS.md)。

---

## 设计文档

- [DESIGN.md](docs/DESIGN.md) — 架构设计 + 流程图
- [TOOLS.md](docs/TOOLS.md) — 22 个工具完整签名
- [PROTOCOL.md](docs/PROTOCOL.md) — USB 通讯协议

---

## 项目状态

```
硬件设计 ───→ 待打样验证
固件开发 ───→ 未开始
MCP Server ─→ 代码已写，待联调
```

当前为 **设计阶段**，代码基于硬件需求文档编写，尚未在真实硬件上验证。
