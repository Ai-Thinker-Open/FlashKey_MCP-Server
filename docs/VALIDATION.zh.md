[![English](https://img.shields.io/badge/English-Docs-green)](VALIDATION.md)

# 验证记录

## 范围

本记录覆盖仓库基线 `12d5eb380df48c1470783ced0d091935b31f7320` 及本次仓库健康度改动。验证环境为 Windows（`win32`）、Python 3.13.14 和 pytest 9.1.1，依赖根据项目声明的约束由当前 pip 解析安装。

## 自动验证结果

| 检查 | 结果 |
| --- | --- |
| 完整测试，第 1 次 | 122 通过、0 失败、1 个依赖警告；22.06 秒 |
| 完整测试，第 2 次 | 122 通过、0 失败、1 个依赖警告；21.93 秒 |
| 字节码编译 | `python -m compileall -q src tests` 通过 |
| 已安装 CLI 元数据 | `flashkey-mcp --version` 输出 `0.1.4` |
| 隔离打包，第 1 次 | wheel 与 sdist 均成功；无项目自身的构建警告或错误 |
| 隔离打包，第 2 次 | wheel 与 sdist 均成功；无项目自身的构建警告或错误 |

剩余警告来自已安装的 `pydantic-settings`/`mcp` 依赖路径在解析 MCP lifespan 前向引用时的行为，并非本仓库源码发出；两次测试均未因此失败。

## 构建产物

| 次数 | 产物 | 字节数 | SHA-256 |
| --- | --- | ---: | --- |
| 1 | `flashkey_mcp-0.1.4-py3-none-any.whl` | 10,851,940 | `946ba92bf52b6f65283936c917b6953de3f0c3d292874b6437bce466a5bd8ec7` |
| 1 | `flashkey_mcp-0.1.4.tar.gz` | 10,830,509 | `5b43337c90ceafec9d05ba348ffcdf95dccf96a0199e74414bd69f7f7daa0702` |
| 2 | `flashkey_mcp-0.1.4-py3-none-any.whl` | 10,851,940 | `9b85ecd98d2f8584b1398ddbaf42d1c5cb2a6b93955bf61a0af23483b09e33d8` |
| 2 | `flashkey_mcp-0.1.4.tar.gz` | 10,830,558 | `433969f8684eba73c814303f3fce28b87d8bdfff2ebc44613eeff7bf2011a1b4` |

两次构建均完成，并包含预期的固件以及 Linux/Windows OpenOCD 资源。由于 ZIP/tar 的归档元数据受时间戳影响，两次哈希不同，因此当前构建尚未达到逐位可复现。

## 硬件验证边界

本次未连接 FK-01 或目标板。以下项目仍需人工连接真实硬件验证；其中相关脚本已明确排除在无人值守 pytest 收集范围之外：

- 串口发现、认证、重连与实时日志采集；
- BOOT/RST 和 5 V / 3.3 V / VUSB 控制；
- Ai-WB2/Ai-M62 固件烧录及烧录后行为；
- 使用 WCH-LinkE/OpenOCD 更新 FK-01 控制器；
- 固件更新、WSL USB 重映射与 systemd 服务行为。

自动验证成功只证明测试和打包所覆盖的软件行为，不能代替电气行为或真实固件烧录认证。
