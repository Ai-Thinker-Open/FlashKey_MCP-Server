# 工具清单

## 连接 & 认证

### `flashkey_list_devices()`
扫描 USB 串口，找出 FlashKey 控制接口。

```
返回: 端口 | 描述 | VID:PID 表格
```

### `flashkey_connect(port)`
打开 FlashKey 控制串口。

```
参数: port — 串口名 (COM3, /dev/ttyACM0, /dev/cu.usbmodemXXXX)
返回: 连接结果
前置: 无
后继: 必须调 flashkey_ping() 和 flashkey_handshake()
```

### `flashkey_disconnect()`
断开串口连接。

### `flashkey_ping()`
验证连通性，检查设备是否返回 MAGIC "FK01"。

```
返回: "PONG! MAGIC: FK01" 或失败信息
前置: 已 connect
```

### `flashkey_handshake()`
Challenge-Response 认证。确认设备是正品 Ai-Thinker FlashKey。

```
返回: 成功/失败
前置: 已 ping 通过
后继: 认证后所有控制命令可用
```

---

## 状态查询

### `flashkey_get_version()`
读取固件版本号 MAJOR.MINOR.PATCH。

### `flashkey_get_uid()`
读取 CH32V203 芯片唯一 ID（12 字节 hex）。

### `flashkey_get_all_status()`
一次性读取：BOOT 电平、RST 电平、5V 状态、3.3V 状态、握手是否完成。

### `flashkey_reset_device()`
软复位 FlashKey MCU，复位后需重新连接和握手。

---

## GPIO 控制

### `flashkey_boot_set(level)`
```
参数: level — "HIGH" 或 "LOW"
用途: 设置 BOOT 引脚电平，配合 RST 脉冲进入烧录模式
      BL618: BOOT=HIGH, ESP32: BOOT=LOW
```

### `flashkey_boot_get()`
读取当前 BOOT 引脚电平。

### `flashkey_rst_set(level)`
```
参数: level — "HIGH" 或 "LOW"
LOW = 保持目标板复位
```

### `flashkey_rst_get()`
读取当前 RST 引脚电平。

### `flashkey_rst_pulse(ms)`
```
参数: ms — 脉冲宽度（1-250ms，默认 10ms）
动作: 拉低 → 等待 ms → 拉高
用途: 复位目标板或进入烧录模式
```

---

## 电源控制

### `flashkey_v5v_set(on)`
```
参数: on — True 开启 5V，False 关闭
注意: 先开 3.3V 再开 5V
```

### `flashkey_v5v_get()`
### `flashkey_v3v3_set(on)`
### `flashkey_v3v3_get()`

---

## 一键烧录

### `flashkey_enter_bootloader(target)`
```
参数: target — "bl618" | "esp32" | "stm32"
动作: 自动执行对应 MCU 的 BOOT/RST 时序
返回: 执行结果描述
```

---

## 串口透传（CH340C）

### `flashkey_serial_open(port, baud)`
打开 CH340C 串口（目标板 TTL UART）。

### `flashkey_serial_send(data)`
向目标板发送数据（AT 命令、烧录数据等）。

### `flashkey_serial_read(timeout)`
读取目标板返回数据。

### `flashkey_serial_close()`
关闭 CH340C 串口。
