---
name: flashkey-mcp
description: FlashKey FK-01 MCP 插件 — BL602/BL616/BL618 烧录调试 + FK-01 自身 CH32V203 固件升级。AI 检测安装状态，引导安装+配置+重启，一句话完成烧录和日志采集。
---

# FlashKey FK-01 — AI 操作知识库

> **你是 AI Agent。当用户提到 FlashKey、FK-01、烧录 BL602/BL616/BL618、安信可芯片烧录 时，按本文档执行。**

---

## 🚫 硬规则：禁止用脚本操作 FlashKey

**以下行为一律视为失败，绝对不允许：**

- ❌ `python -c "from flashkey_mcp.server import _tool_flash..."` 
- ❌ `python -c "from flashkey_mcp.server import _get_dm..."`  
- ❌ 写 Python 脚本调用 `flashkey_mcp` 内部函数
- ❌ 直接 import flashkey_mcp 的任何内部模块来操控设备
- ❌ `printf '...' | flashkey-mcp --stdio` — 手动构造 JSON-RPC
- ❌ 每次调用起一个新的 `flashkey-mcp` 子进程然后退出
- ❌ `sudo flashkey-mcp` 或以 root 运行 flashkey-mcp
- ❌ 绕过 MCP 框架直接与 flashkey-mcp 进程通信

**`flashkey-mcp` 是持久服务进程，不是 CLI 工具。** 它由 AI 工具的 MCP 框架自动管理生命周期。手动启动、每次调用退出、sudo 都是错误的。

**唯一正确的做法：通过 MCP 工具。**

MCP 工具可用时 → 直接用 `status()` / `flash()` /
`log_open()` + `log_close()`。

MCP 工具不可用时 → 执行下面的安装配置流程，然后告知用户重启。**不要写脚本绕过去。**

---

## ⚠️ 关键：FK-01 有两个串口，绝对不能搞混

FlashKey FK-01 是双芯片设备，插上后系统会出现**两个**串口。**调用 `flash()` 或 `log_open()` 前，必须先调 `list_ports()`，根据返回的 `role` 字段选择端口，不要根据设备名猜测。**

| role | VID/PID | 用途 |
|------|---------|------|
| `fk_control` | 1A86:FE0D | **FK-01 主控** — 仅 MCP 内部使用 |
| `fk_log` | 1A86:8010 | **WCH-LinkE VCP 日志/烧录口**（最高 921600）— `flash()` / `log_open()` / `send()` 用这个 |
| `unknown` | 其他 | 非 FlashKey 设备，忽略 |

不同系统上设备名不同（Linux: `/dev/ttyACMx` `/dev/ttyUSBx`，Windows: `COMx`，macOS: `/dev/cu.*`），所以**不要猜名字，看 `role` 字段**。

`flash` 和 `log_open` 内置了端口校验 — 传错端口会立即报错并提示正确端口名。

---

## 步骤 0：判断当前状态

### 先检查 MCP 工具是否可用

**直接用 AI 工具的原生 function call 调用 `status()`**。不要用 shell 命令、不要 ps 查进程、不要检查配置文件。

- 调用成功 → MCP 已连接。**直接跳到步骤 3。**
- 返回 `tool not found` / `unknown tool` → MCP 未连接，继续步骤 1 检查服务状态。

---

## 步骤 1：确认 Python 版本 + 安装

### 1a. 检查 Python 版本

```bash
python3 --version
```

- **>= 3.10** → 用 `pip install` / `python3 -m pip install`，继续 1b
- **< 3.10** → 先检查有没有其他 Python 版本：

```bash
python3.11 --version 2>/dev/null || python3.12 --version 2>/dev/null || python3.10 --version 2>/dev/null
```

如果有 3.10+，所有命令用该版本代替，例如 `python3.11 -m pip install ...`。

如果都没有，安装 Python 3.10+：

| 系统 | 命令 |
|------|------|
| Ubuntu/Debian | `sudo apt install python3.12 python3.12-venv` |
| macOS | `brew install python@3.12` |
| Windows | `winget install Python.Python.3.12` |

安装后用 `python3.12 -m pip install ...` 代替 `pip install ...`。

### 1b. 安装 flashkey-mcp

```bash
pip install git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git
```

安装后验证：

```bash
which flashkey-mcp && flashkey-mcp --version
```

如果 `which flashkey-mcp` 找不到（pip 装到了非 PATH 目录），链接到 PATH：

```bash
# 找到 flashkey-mcp 的实际位置
find / -name flashkey-mcp -type f 2>/dev/null | head -3
# 链接到 ~/.local/bin
ln -sf <实际路径> ~/.local/bin/flashkey-mcp
```

如果失败，检查 Python 版本（必须 >= 3.10），或尝试：

```bash
pip install --user git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git
```

---

## 步骤 2：配置 AI 工具

### 方式 A：一键配置（推荐）

```bash
bash setup.sh
```

自动检测系统上的 AI 工具并写入对应配置。支持 Cline、Hermes、MiMo Code。

### 方式 B：手动配置

flashkey-mcp 是通用 MCP 服务器，任何 MCP 兼容工具都可以使用。

**所有工具的 stdio 配置本质上相同：告诉工具用 `flashkey-mcp` 命令启动子进程。**

```json
{"flashkey": {"command": "flashkey-mcp"}}
```

| 工具 | 配置文件 | 格式 |
|------|---------|------|
| Cline (VS Code) | `~/.cline/mcp.json` | JSON |
| Hermes Agent | `~/.hermes/config.yaml` | YAML |
| MiMo Code | 项目根目录 `mimocode.json`，顶层 key 为 `"mcp"`，`"type": "local"` | JSON |

### 方式 C：SSE 服务模式（服务独立运行，需工具支持 SSE）

```bash
pip install "flashkey-mcp[sse] @ git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git"
flashkey-mcp --service install    # 安装 systemd 用户服务
```

MCP config：
```json
{"flashkey": {"type": "sse", "url": "http://127.0.0.1:8100/sse"}}
```

### ⚠️ 模式不能混用

- SSE 服务 + stdio config → 工具不可用（config 不匹配）
- 同时配了 SSE 服务 AND stdio config → 两个 flashkey-mcp 进程抢串口
- 切换模式时：先停掉旧的，再启新的

### 诊断命令

```bash
flashkey-mcp --service status          # 检查 SSE 服务状态
journalctl --user -u flashkey-mcp -f   # 查看服务日志
tail -f /tmp/flashkey-mcp.log          # 查看文件日志
```

---

## 步骤 3：触发握手 + 烧录 + 日志

### 3a. 先确认设备状态

调用 `status()` 确认 `authed: true`。DeviceManager 在 MCP 连接建立时自动启动，FK-01 插入后 5 秒内自动握手。

### 3b. 烧录

```
flash(firmware_path="/path/to/firmware.bin", flash_port="fk_log端口", chip="bl616")
```

### 3c. 查看日志

```
log_open(port="同上端口", baud_rate=115200, project="<项目名>")  # 后台开始监控，立即返回
# 可继续执行其他工具，如复位、发指令等
log_close()                                 # 关闭并释放串口
# 读取资源 flashkey://log 获取本次日志
# 历史日志自动归档到 ~/flashkey-logs/<项目名>/（每项目 10 份，可用 flashkey://logs/<项目名> 列出）
# 如需长期保存：log_dump(dest_path="~/logs/boot.txt")
```

如果不启动：`rst_pulse(50)`，然后 `log_open()` → 操作 →
`log_close()` → 读取 `flashkey://log`。

### 3d. 发送串口数据

```
send(port="fk_log端口", data="AT\r\n", read_response=True)
```

- **encoding="text"** (默认): 字符串作为 UTF-8 发送，`\n` `\r` `\t` 转义可用
- **encoding="hex"**: 十六进制发送，如 `"48 65 6C 6C 6F"` 或 `"48656C6C6F"`
- **read_response=True**: 发送后读回目标响应（适合 AT 指令等一问一答协议）
- **read_response=False**: 仅发送，不等待响应

示例：
```
send(port="/dev/ttyUSB0", data="AT+UART_CUR=115200,8,1,0,0\r\n", read_response=True)
send(port="/dev/ttyUSB0", data="48656C6C6F0D0A", encoding="hex")
```

---

## 步骤 3：烧录 + 日志

当 `status()` 返回 `authed: true` 后：

**调用 `flash()` 一键烧录**，FK-01 自动处理时序和恢复。

**烧录后**：`log_open(port, baud_rate=115200)` → `log_close()` →
读取 `flashkey://log` 验证启动日志。

---

## 步骤 4：FK-01 自身固件升级（CH32V203）

FK-01 主控芯片是 CH32V203。升级其固件需要 **WCH-LinkE 调试器**，且
**无法全自动完成**——必须先由用户完成硬件接线，之后才能触发烧录。

### 4a. 先检查更新

调用 `firmware_check()`（无需认证）：

- `update_available: true` → 有比设备当前更新的固件，进入 4b
- `package_update_available: true` → 需要先升级 flashkey-mcp 包才能拿到新 hex
  （`pip install --upgrade git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git`，重启服务）
- `latest_*` 为 null → GitHub 暂无 Release 或网络不可达，按现状使用包内固件

### 4b. 硬件准备（必须用户手动完成）

1. 把 FlashKey 自带的 WCH-LinkE 通过 USB 接入电脑
   （WSL 环境：`usbip attach` 到 WSL）
2. 将 WCH-LinkE 的 **SWDIO / SWCLK / GND / 3V3** 接到 CH32V203 的 **SWD 接口**
3. 确认目标板上电

未接线时工具会返回硬件准备错误并说明以上步骤，不会执行烧录。

### 4c. 触发烧录

```python
firmware_flash(confirm=True)
```

- `hex_path`：默认烧包内内置 hex；可传自定义路径
- `force=True`：允许烧录比设备当前更低的版本
- `dry_run=True`：只打印将执行的命令，不实际烧录
- 普通烧录失败且疑似读保护/写保护时，工具会**自动用带 unlock 的全片擦除+烧录重试一次**

### 4d. 仍失败：WCH-LinkUtility 解锁（兜底）

自动解锁重试仍失败时，工具会返回指引：在 **Windows 主机**上运行
**WCH-LinkUtility** → 连接 WCH-LinkE → 选择 **Unlock** 解除保护 →
重新执行 `firmware_flash(confirm=True)`。

> OpenOCD（WCH v1.6，Linux x64 / Windows x64）已随 flashkey-mcp 包内置，
> 无需单独安装；可用环境变量 `FLASHKEY_OPENOCD` 覆盖。

---

## 芯片子 skill

不同芯片烧录方式和 SDK 不同。根据用户提到的芯片型号，加载对应子 skill：

| 模组 | 芯片 | 子 skill | 烧录方式 |
|------|------|---------|---------|
| Ai-WB2 | BL602 | `flashkey-mcp-ai-wb2` | 串口打断 |
| Ai-M61/M62 | BL616/BL618 | `flashkey-mcp-ai-m61-m62` | ISP 模式 |

---

## 通用故障排查

```
flash() 失败
├─ status() 先检查 authed / boot / rst 状态
├─ authed: false → 拔出 FK-01 重新插入，等 5 秒
├─ "make: No rule" → flash_dir 不对
├─ fk_log 被占用 → 关闭串口监视器
├─ 烧录成功但不启动 → rst_pulse(50) + log_open/close + 读 flashkey://log
└─ 芯片特定问题 → 加载对应芯片子 skill
```

## 平台陷阱

- **Windows COM10+**：必须写 `\\.\COM10`
- **WSL**：FK-01 + WCH-Link 需要 `usbipd` 映射
- **串口互斥**：`log_open`、`send` 和 `flash` 共用 fk_log
- **v5v 反直觉**：`v5v_set(True)` = PB1 LOW = 开启 5V
- **vusb 反直觉**：`vusb_set(True)` = PA0 LOW = 开启外置 USB-A 电源（拉低启动，拉高关闭）

## 引脚参考

| 功能 | 引脚 | 控制 |
|------|------|------|
| BOOT | PB3 | `boot_set()` |
| RST | PB4 | `rst_set()`/`rst_pulse()` |
| 5V_EN | PB1 | `v5v_set()` — 低有效 |
| 3V3_EN | PB0 | `v3v3_set()` — 高有效 |
| USB-A 电源 | PA0 | `vusb_set()` — 低有效（拉低=开，拉高=关） |
