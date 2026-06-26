---
name: flashkey-mcp
description: FlashKey FK-01 MCP 插件 — BL602/BL616/BL618 烧录调试。AI 工具启动时自动拉起，插上 FK-01 自动握手，说需求自动完成烧录。
---

# flashkey-mcp — AI 操作知识库

> **不重复工具列表、安装命令、参数说明。** AI 从 `tools/list`/`tools/call` 能自己拿到。
> 本文档只放 **MCP 协议层拿不到的领域知识**。

## 芯片支持

| chip | 烧录方式 | 默认波特率 | SDK |
|------|---------|:--------:|-----|
| `bl602` | `make flash p={port} b={baud}` | 921600 | Ai-Thinker-WB2 |
| `bl616` | `make flash CHIP=bl616 COMX={port} BAUDRATE={baud}` | 2000000 | bouffalo_sdk |
| `bl618` | `make flash CHIP=bl618 COMX={port} BAUDRATE={baud}` | 2000000 | bouffalo_sdk |

## 烧录故障排查

```
flashkey_flash() 失败
├─ flashkey_status() 检查 authed/boot/rst
├─ "make: No rule" → sdk_path 不对或用 tool 参数指定
├─ "Failed to connect" → 降 baud_rate=115200 或检查 BOOT 电平
├─ CH340C 被占用 → 关闭串口监视器
└─ 烧录成功不启动 → rsl_pulse(50) + flashkey_log(port, duration=5)
```

## 启动日志特征

| 芯片 | 正常启动关键词 | 等待 |
|------|---------------|------|
| BL602 | `Booting BL602...` 或 `[OS] Starting` | 1s |
| BL616 | `Starting ...` 或 `Hello World!` | 2s |
| BL618 | 同 BL616 | 2s |

## 平台陷阱

- **Windows COM10+**：必须写 `\\.\COM10`
- **WSL**：FK-01 + CH340C 都需要 `usbipd` 映射
- **串口互斥**：`flashkey_log` 和 `flashkey_flash` 共用 CH340C，不能同时调
- **v5v 反直觉**：`v5v_set(True)` = PB1 LOW = 开启 5V

## 引脚参考

| 功能 | 引脚 | 默认 | 控制 |
|------|------|------|------|
| BOOT | PB3 | HIGH | `boot_set()` |
| RST | PB4 | HIGH | `rst_set()`/`rst_pulse()` |
| 5V_EN | PB1 | HIGH=OFF | `v5v_set()` — 低有效 |
| 3.3V_EN | PB0 | LOW=OFF | `v3v3_set()` — 高有效 |
