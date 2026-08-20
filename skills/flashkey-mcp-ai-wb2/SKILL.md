---
name: flashkey-mcp-ai-wb2
description: FlashKey FK-01 — Ai-WB2 (BL602) ISP 模式烧录（make eflash，全量含 boot2）。Ai-Thinker-WB2 SDK。
---

# FlashKey FK-01 — Ai-WB2 烧录指南

> **当用户提到 Ai-WB2、WB2、BL602 烧录时，加载本 skill。**

---

## 烧录原理：ISP 模式（唯一烧录方式）

Ai-WB2 (BL602) 统一使用 **ISP 芯片级烧录模式**：FK-01 拉高 BOOT 引脚后发送 RST 复位脉冲，
芯片复位进入 bootloader（ISP 模式），然后执行 `make eflash` 全量烧录（**含 boot2**）。

```
BOOT↑ → RST 脉冲 → 进入 ISP → make eflash 烧录 → RST 恢复 → BOOT↓
```

串口打断（break / `make flash`）模式已移除，不再支持；`flash()` 无 mode 参数，统一 ISP 烧录。

## 标准烧录（默认即可）

```
flash(
    firmware_path="/path/to/helloworld.bin",
    flash_port="/dev/ttyUSB0",   # list_ports() 中 role=fk_log 的端口
    chip="ai-wb2",
    baud_rate=921600,
    flash_dir="/path/to/sdk/app"
)
```

自动完成：BOOT↑ + RST 脉冲进入 ISP → 执行 `make eflash`（全量含 boot2）→ RST + BOOT↓ 恢复。

`make eflash` 不会重新编译，只执行烧录。芯片执行过 `make erase_flash` 擦除后同样使用此模式。

## 自定义烧录命令（tool 参数）

自定义烧录命令直接用 `flash` 的 `tool` 参数（支持 {port}/{baud}/{firmware}/{chip} 占位符）：

```
flash(
    firmware_path="/path/to/helloworld.bin",
    flash_port="<list_ports() 中 role=fk_log 的端口>",
    chip="ai-wb2",
    flash_dir="<flash_dir>/app",
    tool="make -C {flash_dir} eflash p={port} b={baud}"
)
```

## 参数默认值

| 参数 | 默认值 |
|------|--------|
| chip | ai-wb2 |
| baud_rate | 921600（FlashKey 串口上限） |
| SDK | Ai-Thinker-WB2 |

## 烧录后验证

```
log_open(port="/dev/ttyUSB0", baud_rate=115200)
rst_pulse(50)  # 必须复位让模组重启，才能采集到完整启动日志
log_close()
# 读取 flashkey://log 查看启动日志
```

正常启动日志包含 `Booting BL602...` 或 `[OS] Starting`。

## 硬件接线

Ai-WB2 模组与 FK-01 连接：

| FK-01 | Ai-WB2 |
|-------|--------|
| fk_log TX (WCH-LinkE VCP) | GPIO7 (RX) |
| fk_log RX (WCH-LinkE VCP) | GPIO16 (TX) |
| RST (PB4) | CHIP_EN |
| BOOT (PB3) | GPIO8 |
| 3V3 | 3.3V |
| GND | GND |

## 故障排查

```
烧录失败
├─ "shake hand fail" → 检查日志口 TX/RX 交叉接线
├─ 串口无输出 → 检查 Ai-WB2 供电（可能需要 5V + 3.3V）
├─ 波特率过高 → 降为 115200
├─ ISP 握手失败 → 确认 BOOT (GPIO8) 已接 FK-01 BOOT 引脚，RST 接 CHIP_EN
└─ 手动验证：按住 BOOT 按键 + 按 RESET，看串口是否有 bootloader 输出
```
