---
name: flashkey-mcp-ai-m61-m62
description: FlashKey FK-01 — Ai-M61/M62 (BL616/BL618) ISP 烧录。BOOT+RST、bouffalo_sdk。
---

# FlashKey FK-01 — Ai-M61/M62 烧录指南

> **当用户提到 Ai-M61、Ai-M62、M61、M62、BL616、BL618 烧录时，加载本 skill。**

---

## 烧录原理：ISP 模式

BL616/BL618 使用 **ISP 模式**进入 bootloader：FK-01 将 BOOT 引脚拉高，然后脉冲 RST 引脚复位芯片。芯片在 BOOT=HIGH 状态下复位即进入 ISP bootloader。之后 `make flash` 通过 CH340C 串口与 bootloader 握手并烧录。

## 烧录命令

```
flashkey_flash(
    firmware_path="/path/to/firmware.bin",
    flash_port="/dev/ttyUSB0",   # role=fk_flash 的端口
    chip="bl616",                 # 或 bl618
    baud_rate=2000000,
    sdk_path="/path/to/bouffalo_sdk/app"
)
```

`flashkey_flash` 自动完成：BOOT↑ → RST 脉冲 → 启动 make flash → 烧录 → BOOT↓ + RST 恢复。

## 参数默认值

| 参数 | BL616 | BL618 |
|------|-------|-------|
| baud_rate | 2000000 | 2000000 |
| mode | isp | isp |
| SDK | bouffalo_sdk | bouffalo_sdk |
| make args | `CHIP=bl616 COMX={port} BAUDRATE={baud}` | `CHIP=bl618 COMX={port} BAUDRATE={baud}` |

## 烧录后验证

```
flashkey_log(port="/dev/ttyUSB0", duration=5, grep="Starting")
```

正常启动日志包含 `Starting ...` 或 `Hello World!`。

## 故障排查

```
烧录失败
├─ "Failed to connect" → 降 baud_rate=115200，检查 BOOT 电平
├─ CH340C 被占用 → 关闭串口监视器
├─ 烧录成功不启动 → flashkey_rst_pulse(50) + flashkey_log(port, duration=5)
└─ SDK 未找到 → 克隆 bouffalo_sdk 并设置 sdk_path
```
