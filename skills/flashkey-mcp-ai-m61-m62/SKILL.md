---
name: flashkey-mcp-ai-m61-m62
description: FlashKey FK-01 — Ai-M61/M62 (BL616/BL618) ISP 烧录。BOOT+RST、bouffalo_sdk。
---

# FlashKey FK-01 — Ai-M61/M62 烧录指南

> **当用户提到 Ai-M61、Ai-M62、M61、M62、BL616、BL618 烧录时，加载本 skill。**

---

## 烧录原理：ISP 模式

BL616/BL618 使用 **ISP 模式**进入 bootloader：FK-01 将 BOOT 引脚拉高，然后脉冲 RST 引脚复位芯片。芯片在 BOOT=HIGH 状态下复位即进入 ISP bootloader。之后 `make flash` 通过 fk_log 串口（WCH-LinkE VCP）与 bootloader 握手并烧录。

⚠️ WCH-LinkE VCP（fk_log）最高仅支持 921600；BL616/BL618 需要 2M 烧录波特率时，请外接 USB-UART 并在 `flash` 里指定该端口。

## 烧录命令

```
flash(
    firmware_path="/path/to/firmware.bin",
    flash_port="<list_ports() 中 role=fk_log 的端口>",
    chip="bl616",                 # 或 bl618
    baud_rate=921600,
    flash_dir="/path/to/bouffalo_sdk/app"
)
```

`flash` 自动完成：BOOT↑ → RST 脉冲 → 启动 make flash → 烧录 → BOOT↓ + RST 恢复。
⚠️ FlashKey 自带串口最高仅支持 921600，不要用 2000000；若确需 2M 烧录波特率，
请外接 USB-UART 并在 `flash` 里把 `flash_port` 指向外接串口。

## 参数默认值

| 参数 | BL616 | BL618 |
|------|-------|-------|
| baud_rate | 921600（FlashKey 上限） | 921600（FlashKey 上限） |
| mode | isp | isp |
| SDK | bouffalo_sdk | bouffalo_sdk |
| make args | `CHIP=bl616 COMX={port} BAUDRATE={baud}` | `CHIP=bl618 COMX={port} BAUDRATE={baud}` |

## 烧录后验证

```
log_open(port="/dev/ttyUSB0", baud_rate=115200)
rst_pulse(50)  # 必须复位让模组重启，才能采集到完整启动日志
log_close()
# 读取 flashkey://log 查看启动日志
```

正常启动日志包含 `Starting ...` 或 `Hello World!`。

## 故障排查

```
烧录失败
├─ "Failed to connect" → 降 baud_rate=115200，检查 BOOT 电平
├─ fk_log 被占用 → 关闭串口监视器
├─ 烧录成功不启动 → rst_pulse(50) + log_open/close + 读 flashkey://log
└─ SDK 未找到 → 克隆 bouffalo_sdk 并设置 flash_dir
```
