[![English](https://img.shields.io/badge/English-Docs-green)](CODE_ENTRY.md)

# 代码入口

## 进程入口

安装后的命令由 `pyproject.toml` 声明：

```toml
[project.scripts]
flashkey-mcp = "flashkey_mcp.server:main"
```

因此进程入口是 `flashkey_mcp.server.main()`。它会安装运行时保护、解析服务管理、升级、版本、主机、端口、SSE 与 stdio 参数，初始化日志并取得共享的 `DeviceManager`。

## 传输入口

- 默认路径调用 `_run_sse(host, port)`，在 `/mcp` 提供 Streamable HTTP，在 `/sse` 及消息路由提供传统 SSE，并提供 `/release`、`/reconnect` 兼容端点。
- 旧版 stdio 模式调用 `mcp.run(transport="stdio")`。
- 模块级 `FastMCP` 对象会在任一传输启动前注册 MCP 工具、资源和提示词。

## 硬件操作路径

```text
MCP 客户端
  -> server.py 工具处理函数
  -> DeviceManager.require_authed()
  -> commands.py 操作
  -> protocol.py 帧与 CRC 处理
  -> transport.py 串口传输
  -> FK-01 硬件
```

后台管理器通常按 `DISCONNECTED -> CONNECTING -> AUTHED` 推进。达到空闲超时后会释放串口并进入 `IDLE`；下一次硬件工具调用会重新唤醒设备检测和认证。

## 维护起点

- MCP 请求处理或端点：`src/flashkey_mcp/server.py`。
- 设备发现和生命周期：`src/flashkey_mcp/device_manager.py`。
- 线协议命令、帧处理和串口 I/O：分别查看 `commands.py`、`protocol.py`、`transport.py`。
- 单元测试放在 `tests/`；只能连接硬件执行的脚本保留为显式人工检查。
