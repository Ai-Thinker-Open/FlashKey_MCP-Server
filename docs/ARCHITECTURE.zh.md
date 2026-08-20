[![English](https://img.shields.io/badge/English-Docs-green)](ARCHITECTURE.md)

# 架构说明

## 运行时总览

```text
MCP 客户端
    |  stdio / SSE / Streamable HTTP
server.py  -------------------- guide.py
    |                           资源、提示词、错误指引
DeviceManager
    |---- auth.py               挑战响应认证
    |---- commands.py           FK-01 操作
    |       `-- protocol.py     帧编码、解析与 CRC
    |               `-- transport.py   串口发现与 I/O
    |---- modules.py            扩展清单与动态工具
    `---- events.py             事件记录与 Webhook

firmware_tools.py               内置固件、OpenOCD 与升级
singleton.py                    每台主机只运行一个服务进程
```

## 模块职责

| 领域 | 源文件 | 职责 |
| --- | --- | --- |
| MCP 边界 | `server.py` | CLI、传输、工具、资源、提示词与 HTTP 路由 |
| 设备生命周期 | `device_manager.py` | 发现、握手、状态转换、保活与空闲释放 |
| 硬件命令 | `commands.py` | FK-01 高层命令 |
| 线协议 | `protocol.py` | 帧、解析、CRC 与请求响应交换 |
| 串口层 | `transport.py` | 端口角色、串口传输与设备选择 |
| 认证 | `auth.py` | 挑战响应握手 |
| 扩展 | `modules.py` | 模块清单与动态暴露工具 |
| 使用指引 | `guide.py` | MCP 资源、提示词与可操作错误说明 |
| 事件 | `events.py` | 操作事件、记录与 Webhook 投递 |
| 固件支持 | `firmware_tools.py` | 打包固件、OpenOCD、更新与烧录辅助 |
| 进程安全 | `singleton.py` | POSIX 与 Windows 跨进程锁 |

运行时资源位于 `src/flashkey_mcp/firmware/`，服务模板和主机配置位于 `src/flashkey_mcp/configs/`，由 `pyproject.toml` 中的声明一同打包。

## 状态归属

`DeviceManager` 统一持有串口设备状态，并由服务工具处理函数共享。硬件操作必须通过它的认证访问路径，不能自行打开端口。长操作会更新活动状态，避免空闲释放机制在执行中关闭设备。

进程锁用于防止两个本地服务实例争用同一个 FK-01：POSIX 使用 `fcntl.flock`，Windows 使用 `msvcrt.locking`。

## 扩展边界

新增协议行为时应按层拆分：帧行为放在 `protocol.py`，设备命令放在 `commands.py`，生命周期放在 `device_manager.py`，只有面向 MCP 的编排放在 `server.py`。真实设备测试应与无人值守测试分开，确保 CI 和本地验证不会隐式烧录设备或切换供电。
