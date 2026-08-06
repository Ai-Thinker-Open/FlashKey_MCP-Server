<h1 align="center">FlashKey MCP Server</h1>

> FlashKey FK-01 MCP 服务器 — 通用 USB 烧录调试器，支持任何 MCP 兼容的 AI 工具。

[![English|README](https://img.shields.io/badge/English-README-blue)](README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 项目简介

FlashKey FK-01 是安信可（Ai-Thinker）推出的双芯片 USB 烧录调试器。**flashkey-mcp** 是它的 MCP（Model Context Protocol）服务器插件，让 Cline、Hermes Agent、MiMo Code 等 AI 工具可以直接控制 FK-01 完成烧录、日志采集与调试：

- ⚡ 一键烧录 BL602 / BL616 / BL618 固件
- 📋 采集目标芯片串口日志
- 🔘 控制 BOOT / RST 引脚与 5V / 3.3V / VUSB 电源
- 🔄 升级 FK-01 自身 CH32V203 固件（OpenOCD + WCH-LinkE）

插入 FK-01 后自动握手，5 秒内完成；`flashkey_status()` 统一状态查询无需认证，开箱即用。

> 源码仓库：[Ai-Thinker-Open/FlashKey_MCP-Server](https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server)

---

## 安装 / 编译

### 一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/Ai-Thinker-Open/FlashKey_MCP-Server/master/setup.sh | bash
```

脚本自动完成：
1. 安装 flashkey-mcp
2. 检测系统上的 AI 工具，自动写入对应格式的 MCP 配置
3. 提示下一步操作

**支持自动配置的工具**：Cline、Hermes Agent、MiMo Code

### pip 安装（Git 源）

```bash
pip install git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git
```

> SSE 运行所需的 starlette / uvicorn 已包含在默认安装中；`flashkey-mcp[sse]` 写法仅为兼容旧版本保留。

### 从源码安装（开发者）

```bash
git clone git@github.com:Ai-Thinker-Open/FlashKey_MCP-Server.git
cd FlashKey_MCP-Server
pip install -e .
```

### 国内网络/镜像

GitHub 不可达时，安装源和固件更新源都可以通过环境变量切换：

```bash
# 用 Gitee 镜像安装（Gitee 会自动从 GitHub 源仓库同步）
FLASHKEY_INSTALL_URL="flashkey-mcp[sse] @ git+https://gitee.com/Ai-Thinker-Open/FlashKey_MCP-Server.git" bash setup.sh

# firmware_check 更新源：auto（默认，先 GitHub 后 Gitee）/ github / gitee
FLASHKEY_UPDATE_SOURCE=gitee flashkey-mcp
```

---

## 使用说明

flashkey-mcp 默认以 **SSE（HTTP）模式**运行：一个常驻服务进程服务所有 AI 会话，多个会话共享同一台 FK-01，不会出现多进程抢占串口、握手互相打断的问题。

### 第一步：启动常驻服务

**Linux（推荐，systemd 用户服务，开机自启 + 崩溃自动重启）**

```bash
flashkey-mcp --service install
```

**Windows / macOS（手工常驻）**

```bash
flashkey-mcp --sse --host 127.0.0.1 --port 8100
```

> `--sse` 可省略（默认即 SSE）。启动后端点固定为 `http://127.0.0.1:8100/sse`。
> 若端口已被另一个 flashkey-mcp 实例占用，程序会提示"另一个实例已在运行，直接使用端点即可"并以 0 退出，不会重复占用设备。

### 第二步：配置 AI 工具（连接 URL）

所有工具的 MCP 配置本质相同：让工具连接上述 SSE 端点，而不是启动一个子进程。

#### JSON 格式（Cline / VS Code）

在工具的 MCP 配置文件中添加：

```json
{
  "mcpServers": {
    "flashkey": {
      "type": "sse",
      "url": "http://127.0.0.1:8100/sse"
    }
  }
}
```

| 工具 | 配置文件路径 |
|------|-------------|
| Cline (VS Code) | `~/.cline/mcp.json` |

#### YAML 格式（Hermes Agent）

`~/.hermes/config.yaml`：

```yaml
mcp_servers:
  flashkey:
    type: sse
    url: http://127.0.0.1:8100/sse
    enabled: true
```

#### Cursor

Settings → MCP → Add new MCP server：
- 名称：`flashkey`
- 类型：`sse`
- URL：`http://127.0.0.1:8100/sse`

#### MiMo Code

```bash
mimo mcp add flashkey-mcp --transport sse --url http://127.0.0.1:8100/sse
```

或项目根目录 `mimocode.json`：

```json
{
  "mcp": {
    "flashkey-mcp": {
      "type": "sse",
      "url": "http://127.0.0.1:8100/sse"
    }
  }
}
```

#### Codex (OpenAI)

```bash
codex mcp add flashkey-mcp --url http://127.0.0.1:8100/mcp
```

### 多个 AI 会话 / 多个客户端共享

同一台机器上的任意数量会话（Cursor、Codex…）都使用同一个 URL 即可。设备只被唯一的常驻进程持有，会话之间互不干扰：

- Linux：服务崩溃会自动重启，无需干预。
- Windows / macOS：保持常驻进程运行；日志可 `tail -f /tmp/flashkey-mcp.log` 查看。

### 空闲自动释放串口（心跳说明）

常驻服务在最后一次工具调用后默认 **30 秒**无活动会关闭 FK-01 串口，固件心跳（PING）随之暂停；下一次工具调用会自动重连并重新握手（约 5 秒），随后心跳自动恢复。这样空闲时串口不被长期占用，其他程序（或 WSL 的 USB 重映射）可以正常使用设备：

- 空闲秒数可用环境变量调整：`FLASHKEY_IDLE_TIMEOUT=60 flashkey-mcp`
- 设为 `0` 禁用空闲释放（一直保持连接，旧行为）
- 空闲期间 `flashkey_status()` 返回 `idle: true`
- 烧录、日志采集等长操作期间不会释放串口

### 兼容旧方式：stdio（单会话）

单会话场景仍可用 `flashkey-mcp --stdio` 启动，工具配置为 `{"command": "flashkey-mcp"}`。注意 stdio 是"一个会话一个进程"，多会话同时使用会抢占同一台 FK-01，请优先使用 SSE。

### 验证安装

```bash
# 安装验证
flashkey-mcp --version

# 服务状态（Linux）
flashkey-mcp --service status

# 配置验证：重启 AI 工具后，在对话中调用
flashkey_status()
```

---

## 示例

### 示例 1：检查设备状态

在 AI 工具对话中调用：

```
flashkey_status()
```

返回 FK-01 的认证状态、固件版本、引脚状态与扩展模块信息。

### 示例 2：一键烧录固件

```
flashkey_flash(firmware_path="/path/to/firmware.bin", chip="bl602", flash_port="ttyACM1")
```

> ⚠️ 端口选择：先调用 `flashkey_list_ports()` 查看端口列表，选择 `role=fk_log` 的端口；**不能**使用 `role=fk_control` 的端口（那是 FK-01 主控口，MCP 内部专用）。

### 示例 3：采集目标芯片日志

```
flashkey_log(port="ttyACM1", baud_rate=115200, duration=5, grep="ERROR")
```

---

## 工作原理

FlashKey FK-01 是双芯片 USB 烧录调试器。MCP 插件提供 27 个工具：

```
flashkey_status()          ← 统一状态，无需认证
flashkey_list_ports()      ← 列出所有串口

flashkey_flash()           ← 一键烧录 BL602/BL616/BL618
flashkey_log()             ← 采集目标芯片日志
flashkey_firmware_check()  ← 检查 FK-01 自身固件是否有更新
flashkey_firmware_flash()  ← 烧录 FK-01 自身 CH32V203 固件（OpenOCD + WCH-LinkE）

flashkey_boot_set/get()    ← BOOT 引脚控制
flashkey_rst_set/get/pulse()  ← RST 引脚控制
flashkey_v5v_set/get()     ← 5V 电源
flashkey_v3v3_set/get()    ← 3.3V 电源
flashkey_enter_bootloader()   ← 组合进入 ISP 模式
flashkey_ping() / flashkey_get_version() / flashkey_get_uid()
```

插入 FK-01 后自动握手，5 秒内完成。

---

## FK-01 自身固件升级（CH32V203）

`flashkey_firmware_check()` 检查设备当前固件版本、安装包内置固件版本与已安装插件版本，并与 GitHub 最新 Release 对比；`flashkey_firmware_flash()` 通过 WCH-LinkE（SDI）烧录 CH32V203（默认烧包内内置 hex，也可传 `hex_path`）。

⚠️ 烧录前置条件：把 FlashKey 自带的 WCH-LinkE 通过 USB 接入电脑，并将 SWDIO/SWCLK/GND/3V3 接到 CH32V203 的 SWD 接口且目标板上电；WSL 环境需先 `usbip attach`。烧录需要显式传 `confirm=True`；普通烧录失败且疑似读保护/写保护时，工具会自动用带 unlock 的全片擦除+烧录重试一次，仍失败会提示用 Windows 主机的 **WCH-LinkUtility** 手动解锁。

OpenOCD 二进制（WCH v1.6，Linux x64 / Windows x64）已随包内置在 `flashkey_mcp/openocd/`，无需单独安装；可用环境变量 `FLASHKEY_OPENOCD` 覆盖路径。随附组件的许可证见包内 `NOTICE-OPENOCD.md`。

---

## FAQ / 故障排查

**Q: 在 WSL 下无法识别 FK-01？**

A: 需要先把 USB 设备附加进 WSL（如 `usbip attach -r 127.0.0.1 -b <bus-id>`），然后再调用工具。

**Q: 烧录时选错端口？**

A: 先调用 `flashkey_list_ports()`，务必选择 `role=fk_log` 的端口（WCH-LinkE VCP）；`role=fk_control` 是 FK-01 主控口，不可用于烧录或日志。

**Q: 烧录失败并提示读保护/写保护？**

A: 工具会自动重试带 unlock 的全片擦除+烧录；仍失败时请用 Windows 主机的 WCH-LinkUtility 手动解锁。

**Q: 高波特率日志不稳定？**

A: WCH-LinkE VCP（fk_log）最高仅支持 921600，需要更高波特率时请改用外接 USB-UART。

**Q: 串口空闲一段时间后被释放了？**

A: 这是设计行为：30 秒无工具调用后自动关闭串口并暂停心跳，下次调用自动重连（约 5 秒握手）。可用 `FLASHKEY_IDLE_TIMEOUT=0` 禁用，或调整空闲秒数。

**Q: 工具提示有新版？**

A: 执行 `pip install --upgrade git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git` 后重启 MCP 服务。Linux 常驻服务可执行 `systemctl --user restart flashkey-mcp`（或 `flashkey-mcp --service status` 确认状态）。

---

## 贡献指南

欢迎提交 Issue 与 Pull Request：

1. Fork 本仓库并创建特性分支；
2. 提交信息遵循 Conventional Commits 风格（如 `feat:`、`fix:`、`docs:`）；
3. 修改后请运行 `pytest` 保证测试通过；
4. 发起 PR 时说明改动目的与验证方式。

---

## License

[MIT](LICENSE) © 2026 Ai-Thinker Open
