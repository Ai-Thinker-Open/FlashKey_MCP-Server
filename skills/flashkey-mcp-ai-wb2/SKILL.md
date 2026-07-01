---
name: flashkey-mcp-ai-wb2
description: FlashKey FK-01 — Ai-WB2 (BL602) 串口打断烧录。flashkey_flash_monitor、Ai-Thinker-WB2 SDK。
---

# FlashKey FK-01 — Ai-WB2 烧录指南

> **当用户提到 Ai-WB2、WB2、BL602 烧录时，加载本 skill。**

---

## 烧录原理：串口打断模式

BL602 使用**串口打断**方式进入 bootloader：烧录工具 `bflb_iot_tool` 先往 CH340C TX 发送 sync 信号，然后打印 `Please Press Reset Key!` 等待复位。FK-01 检测到提示后通过 RST 引脚复位芯片，BL602 boot ROM 在复位时检测到 sync 信号即进入 bootloader 握手。

不需要 BOOT 引脚参与。CH340C 的 DTR/RTS 未引出，复位由 FK-01 RST 引脚完成。

## 烧录方式一：串口打断（默认，make flash）

```
flashkey_flash(
    firmware_path="/path/to/helloworld.bin",
    flash_port="/dev/ttyUSB0",
    chip="bl602",
    baud_rate=921600,
    sdk_path="/path/to/sdk/app"
)
```

自动完成：启动 make flash → 检测 `Please Press Reset Key!` → RST 脉冲 → 握手 → 烧录 → RST 恢复。

## 烧录方式二：ISP 模式（make eflash）

**使用场景：**
- 芯片执行过擦除后，必须用 ISP 模式
- `make flash` 多次超时后，改用 ISP 模式

`make eflash` 不会重新编译，只执行烧录。需要模组先进入 ISP 模式（BOOT 拉高 + RST 脉冲）。

```
flashkey_flash(
    firmware_path="/path/to/helloworld.bin",
    flash_port="/dev/ttyUSB0",
    chip="bl602",
    baud_rate=921600,
    sdk_path="/path/to/sdk/app",
    mode="isp",
    tool="make -C {sdk_path} eflash p={port} b={baud}"
)
```

或用 `flashkey_flash_monitor` 手动控制：

```
flashkey_flash_monitor(
    command="make -C <sdk_path>/app eflash p=/dev/ttyUSB0 b=921600",
    sdk_path="<sdk_path>/app"
)
```

## 参数默认值

| 参数 | 默认值 |
|------|--------|
| chip | bl602 |
| baud_rate | 921600 |
| mode | break（串口打断）|
| SDK | Ai-Thinker-WB2 |

## 烧录后验证

```
flashkey_log(port="/dev/ttyUSB0", duration=5, grep="Booting")
```

正常启动日志包含 `Booting BL602...` 或 `[OS] Starting`。

## 硬件接线

Ai-WB2 模组与 FK-01 连接：

| FK-01 | Ai-WB2 |
|-------|--------|
| CH340C TX | GPIO7 (RX) |
| CH340C RX | GPIO16 (TX) |
| RST (PB4) | CHIP_EN |
| 3V3 | 3.3V |
| GND | GND |

## 故障排查

```
烧录失败
├─ "shake hand fail" → 检查 CH340C TX/RX 交叉接线
├─ 串口无输出 → 检查 Ai-WB2 供电（可能需要 5V + 3.3V）
├─ 波特率过高 → 降为 115200
├─ make flash 多次超时 → 尝试 ISP 模式 (make eflash, mode="isp")
├─ 芯片擦除后 → 必须用 ISP 模式 (make eflash)
└─ 手动验证：按住 BOOT 按键 + 按 RESET，看串口是否有 bootloader 输出
```
