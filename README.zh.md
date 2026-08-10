<h1 align="center">FlashKey MCP Server</h1>

> FlashKey FK-01 MCP 服务器 — 通用 USB 烧录调试器，支持任何 MCP 兼容的 AI 工具。

[![English|README](https://img.shields.io/badge/English-README-blue)](README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 项目简介

FlashKey FK-01 是安信可（Ai-Thinker）推出的双芯片 USB 烧录调试器。**flashkey-mcp** 是它的 MCP（Model Context Protocol）服务器插件，让 Cline、Hermes Agent、OpenCode 等 AI 工具可以直接控制 FK-01 完成烧录、日志采集与调试：

- ⚡ 一键烧录 Ai-WB2 / Ai-M62 固件
- 📋 采集目标芯片串口日志
- 🔘 控制 BOOT / RST 引脚与 5V / 3.3V / VUSB 电源
- 🔄 升级 FK-01 自身 CH32V203 固件（OpenOCD + WCH-LinkE）

插入 FK-01 后自动握手，5 秒内完成；`status()` 统一状态查询无需认证，开箱即用。

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

**支持自动配置的工具**：Cline、Hermes Agent、OpenCode

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

#### OpenCode

写入全局配置 `~/.config/opencode/opencode.json`（或项目根目录 `opencode.json`）：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "flashkey": {
      "type": "remote",
      "url": "http://127.0.0.1:8100/sse",
      "enabled": true
    }
  }
}
```

> OpenCode 的 `type` 支持 `local` / `remote`：连接本地 SSE 必须用 `type: "remote"` 并填
> `/sse` 端点（OpenCode 会先尝试 Streamable HTTP，失败后自动回退到 SSE 传输）。**不要**
> 用 `type: "local"` + `command: ["flashkey-mcp", "--sse"]`——那会再拉起一个独立服务进程，
> OpenCode 按 stdio 等待握手会报 `Connection closed`。

配置后运行 `opencode mcp list` 验证，应显示 `flashkey connected`。

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
- 空闲期间 `status()` 返回 `idle: true`
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
status()
```

---

## 示例

### 示例 1：检查设备状态

在 AI 工具对话中调用：

```
status()
```

返回 FK-01 的认证状态、固件版本、引脚状态与扩展模块信息。

### 示例 2：一键烧录固件

```
flash(firmware_path="/path/to/firmware.bin", chip="ai-wb2", flash_port="ttyACM1")
```

> ⚠️ 端口选择：先调用 `list_ports()` 查看端口列表，选择 `role=fk_log` 的端口；**不能**使用 `role=fk_control` 的端口（那是 FK-01 主控口，MCP 内部专用）。

### 示例 3：采集目标芯片日志

```
log_open(port="ttyACM1", baud_rate=115200, project="my_app")  # 后台开始监控，立即返回
rst_pulse(50)                               # 烧录后验证必须复位：让模组重启，采集完整启动日志
log_close()                                 # 关闭并释放串口
# 读取资源 flashkey://log 获取本次日志
# 如需长期保存：log_dump(dest_path="~/logs/boot.txt") 转存到文件
# log_close 已自动归档：~/flashkey-logs/my_app/，每项目最多 10 份，超出覆盖最旧
```

---

## 工作原理

FlashKey FK-01 是双芯片 USB 烧录调试器。MCP 插件提供 31 个工具：

```
status()          ← 统一状态，无需认证
list_ports()      ← 列出所有串口
recover()         ← 一站式恢复（USB 重挂载 + 重新握手）

flash()           ← 一键烧录 Ai-WB2/Ai-M62
flash_guide()     ← 烧录前先调用，返回标准烧录流程（几乎所有 AI Agent 可见）
log_open() / log_close()  ← 后台日志采集 + 关闭
log_dump()        ← 把最近一次日志转存为本地文件
firmware_check()  ← 检查 FK-01 自身固件是否有更新
firmware_flash()  ← 烧录 FK-01 自身 CH32V203 固件（OpenOCD + WCH-LinkE）

boot_set/get()    ← BOOT 引脚控制
rst_set/get/pulse()  ← RST 引脚控制
v5v_set/get()     ← 5V 电源
v3v3_set/get()    ← 3.3V 电源
enter_bootloader()   ← 组合进入 ISP 模式
ping() / get_version() / get_uid()
```

插入 FK-01 后自动握手，5 秒内完成。

---

## MCP Resources & Prompts

MCP 服务通过 **resources** 提供权威参考数据，通过 **prompts** 提供正确的调用流程模板。
支持 resources/prompts 的客户端可直接读取；不支持的客户端仍会收到注入的浓缩指引和工具描述。

### Resources

| URI | 内容 |
| --- | --- |
| `flashkey://docs/quickstart` | 上手流程：查状态 → 选端口 → 认证 → 烧录/日志 |
| `flashkey://docs/flash-guide` | 烧录指南：端口角色、chip 默认模式/波特率、烧后验证 |
| `flashkey://docs/error-codes` | 错误码权威表（与 README 错误码表同源） |
| `flashkey://status` | 实时设备状态（动态 JSON，离线时含 `error` 字段） |
| `flashkey://ports` | 实时串口列表，含 `role` 字段（动态 JSON） |
| `flashkey://log` | 最近一次日志监控采集到的串口日志（文本，覆盖式） |

### Resource Templates（历史日志）

| 模板 | 用途 |
| --- | --- |
| `flashkey://logs/{project}` | 列出某项目的全部历史日志（JSON，最多 10 份） |
| `flashkey://logs/{project}/{file}` | 读取某项目下指定历史日志的完整内容 |

`log_open(..., project="<项目名>")` 采集、`log_close()` 关闭后，日志会自动归档到
`~/flashkey-logs/<项目名>/flashkey-log-<时间>.txt`（目录可用 `FLASHKEY_LOG_HISTORY_DIR`
覆盖）。每个项目最多保留 **10 份**，超出自动删除最旧的一份；新一次 `log_open()` 仍会
覆盖临时日志 `flashkey://log`，历史归档不受影响。

### Prompts

> MCP 的 prompts 由客户端按需触发（OpenCode 中表现为斜杠命令），AI Agent 不会自动调用；
> Agent 自动遵循烧录流程靠的是注入的 server instructions 与工具描述（与
> `flashkey://docs/*` 资源同源），两者都已内嵌“先 list_ports 选 fk_log、禁止硬编码端口、
> 普通烧录用 flash”的约束。

> 跨客户端通用做法：烧录前先调用 **`flash_guide(chip)`** 工具获取标准流程（工具对
> 所有支持 MCP 的客户端可见、可调用），再把本仓库的 `AGENTS.md` 复制到你的烧录工程
> 根目录，Codex / Claude Code / Cursor / OpenCode / Cline 等都会把流程规则注入系统提示。

| Prompt | 用途 |
| --- | --- |
| `flash-firmware` | 生成烧录步骤：选 `fk_log` → 认证 → 按 chip 默认参数烧录 → 验证 |
| `recover-device` | 根据错误码输出恢复决策树 |
| `collect-logs` | 生成日志采集步骤（AI 读取后自行分析日志判定运行情况） |

### Ai-WB2 / Ai-M62 正确用法摘要

- 先 `list_ports()`，选择 `role=fk_log`（WCH-LinkE VCP）的端口，
  **绝不**使用 `role=fk_control`，也不要按端口名猜测或硬编码
  （`/dev/ttyACM0` / `COM3` 在不同系统/插拔顺序下会变）。
- 普通烧录只调用 `flash(firmware_path, chip, flash_port)`；自定义烧录命令
  （如 `make eflash`）直接用 `flash` 的 `tool` 参数（支持 `{port}`/`{baud}`/
  `{firmware}`/`{chip}` 占位符），无需额外的低层工具。
- `chip="ai-wb2"` 默认 **break** / `baud_rate=921600`（`make flash`）：串口打断烧录，
  只烧 App、不烧 boot2；固件不支持串口打断或执行过 `make erase_flash` 时，
  改用 `mode="isp"` + `make eflash`（全量含 boot2，BOOT↑ + RST 进入 ISP）。
- `chip="ai-m62"` 使用 **isp** 模式、默认 `baud_rate=921600`
  （FlashKey 自带串口最高仅支持 921600；`2000000` 仅在外接 USB-UART 时可用）。
- 烧录后必须验证：先 `log_open()` 打开日志监控，**再 `rst_pulse()` 复位模组**
  采集完整启动日志，然后 `log_close()`，读取 `flashkey://log` 并**自行分析判定启动是否正常**
  （有异常先排查，不要只转述日志原文），或 AT 模组发送 `AT+GMR`。

---

## FK-01 自身固件升级（CH32V203）

`firmware_check()` 检查设备当前固件版本、安装包内置固件版本与已安装插件版本，并与 GitHub 最新 Release 对比；`firmware_flash()` 通过 WCH-LinkE（SDI）烧录 CH32V203（默认烧包内内置 hex，也可传 `hex_path`）。

> 版本策略：内置 hex（FK-01 设备固件）**已固化**，与 flashkey-mcp 包版本相互独立——
> flashkey-mcp 可频繁发版，hex 只在 FK-01 固件有新构建时才更新，不会随包版本变动。

⚠️ 烧录前置条件：把 FlashKey 自带的 WCH-LinkE 通过 USB 接入电脑，并将 SWDIO/SWCLK/GND/3V3 接到 CH32V203 的 SWD 接口且目标板上电；WSL 环境需先 `usbip attach`。烧录需要显式传 `confirm=True`；普通烧录失败且疑似读保护/写保护时，工具会自动用带 unlock 的全片擦除+烧录重试一次，仍失败会提示用 Windows 主机的 **WCH-LinkUtility** 手动解锁。

OpenOCD 二进制（WCH v1.6，Linux x64 / Windows x64）已随包内置在 `flashkey_mcp/openocd/`，无需单独安装；可用环境变量 `FLASHKEY_OPENOCD` 覆盖路径。随附组件的许可证见包内 `NOTICE-OPENOCD.md`。

---

## FAQ / 故障排查

**Q: 在 WSL 下无法识别 FK-01？**

A: 需要先把 USB 设备附加进 WSL（如 `usbip attach -r 127.0.0.1 -b <bus-id>`），然后再调用工具。

**Q: 烧录时选错端口？**

A: 先调用 `list_ports()`，务必选择 `role=fk_log` 的端口（WCH-LinkE VCP）；`role=fk_control` 是 FK-01 主控口，不可用于烧录或日志。

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
