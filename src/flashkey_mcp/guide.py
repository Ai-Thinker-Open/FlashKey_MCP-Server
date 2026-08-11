"""MCP Resources & Prompts 内容源（纯逻辑，不依赖 server 内部状态）。

这里集中维护：

- ``ERROR_GUIDE``：错误码的权威数据源（含义 / 下一步 / 是否可重试 / 恢复工具），
  同时用于 ``flashkey://docs/error-codes`` 资源与 README 错误码表；
- 静态文档：快速上手、烧录指南、错误码表；
- 三个 Prompt 的构建函数与注册入口。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp.prompts.base import (
    AssistantMessage,
    Prompt,
    PromptArgument,
    UserMessage,
)

from flashkey_mcp.errors import E


# ======================================================================
# 模组名称（用户可见）与内部芯片名（SDK 命令）映射
# ======================================================================

CHIP_ALIASES: dict[str, str] = {
    # Ai-WB2 → BL602
    "ai-wb2": "bl602",
    "wb2": "bl602",
    "bl602": "bl602",
    # Ai-M62 覆盖 BL616/BL618；默认按 bl616 的 SDK 参数，旧值 bl618 仍兼容
    "ai-m62": "bl616",
    "m62": "bl616",
    "ai-m61": "bl616",
    "m61": "bl616",
    "bl616": "bl616",
    "bl618": "bl618",
}

MODULE_NAMES: dict[str, str] = {
    "bl602": "Ai-WB2",
    "bl616": "Ai-M62",
    "bl618": "Ai-M62",
}


def normalize_chip(chip: str) -> str:
    """把模组名/旧芯片名归一化为内部 SDK 芯片名。"""
    key = (chip or "").strip().lower()
    return CHIP_ALIASES.get(key, key)


# ======================================================================
# 错误码权威数据源（与 README 错误码表同源）
# ======================================================================

ERROR_GUIDE: dict[str, dict[str, Any]] = {
    E.DEVICE_NOT_FOUND: {
        "meaning": "设备未插入/未挂载",
        "hint": "插入 FK-01 并等待握手；WSL 下先确认 usbip 已挂载",
        "retryable": True,
        "recovery_tool": "recover(reattach=True)",
    },
    E.HANDSHAKE_FAILED: {
        "meaning": "握手失败/重连超时/连接中",
        "hint": "稍候重试；若持续失败请检查 USB 链路/usbip 挂载",
        "retryable": True,
        "recovery_tool": "recover(reattach=True)",
    },
    E.PORT_BUSY: {
        "meaning": "串口被占用/烧录进行中",
        "hint": "等待当前烧录/日志操作结束，或关闭占用串口的程序后重试",
        "retryable": True,
        "recovery_tool": "status",
    },
    E.PORT_WRONG_ROLE: {
        "meaning": "用错端口角色",
        "hint": "先用 list_ports() 按 role 字段选择 fk_log 端口，再重试",
        "retryable": True,
        "recovery_tool": "list_ports",
    },
    E.AUTH_REQUIRED: {
        "meaning": "需要认证",
        "hint": "先完成密钥认证（SET_KEY / flashkey_auth 流程）后重试",
        "retryable": False,
        "recovery_tool": None,
    },
    E.AUTH_FAILED: {
        "meaning": "认证失败（密钥错误）",
        "hint": "重新 SET_KEY 覆盖烧录密钥后重试",
        "retryable": False,
        "recovery_tool": None,
    },
    E.FLASH_PROTECTED: {
        "meaning": "Flash 读保护",
        "hint": "服务端已自动解锁重试；仍失败请用 WCH-LinkUtility 手动解锁",
        "retryable": True,
        "recovery_tool": "firmware_flash",
    },
    E.FLASH_VERIFY_FAILED: {
        "meaning": "烧录校验不一致",
        "hint": "确认 chip 参数与固件匹配后重新烧录",
        "retryable": True,
        "recovery_tool": "flash",
    },
    E.MODULE_NO_RESPONSE: {
        "meaning": "模组无响应",
        "hint": "检查接线/波特率/是否进入 Boot 模式后重试",
        "retryable": True,
        "recovery_tool": "recover(reattach=True)",
    },
    E.MODULE_MANIFEST_INVALID: {
        "meaning": "模组清单无效",
        "hint": "检查 I2C 连接与模块清单后重试",
        "retryable": True,
        "recovery_tool": "module_info",
    },
    E.TIMEOUT: {
        "meaning": "响应超时",
        "hint": "重试一次；若持续超时请检查设备/模组连接与波特率",
        "retryable": True,
        "recovery_tool": "status",
    },
    E.FRAME_CRC: {
        "meaning": "帧校验错误",
        "hint": "直接重试",
        "retryable": True,
        "recovery_tool": None,
    },
    E.INVALID_ARG: {
        "meaning": "参数错误",
        "hint": "按错误信息修正参数后重试",
        "retryable": False,
        "recovery_tool": None,
    },
    E.INTERNAL: {
        "meaning": "未分类错误",
        "hint": "重试；若仍失败请查看 flashkey-mcp 服务日志",
        "retryable": True,
        "recovery_tool": "status",
    },
}


# ======================================================================
# 静态文档资源
# ======================================================================

QUICKSTART_DOC = """\
# FK-01 快速上手

## 目标

让 AI 能正确完成「查状态 → 选端口 → 认证 → 烧录/日志/调试」，不猜参数、不漏步骤。

## 推荐顺序

1. 调用 `status()` 确认 FK-01 已连接，并查看 `authed` 字段。
2. 调用 `list_ports()` 获取串口列表，按 `role` 字段选择端口：
   - `role=fk_log` → WCH-LinkE VCP，用于烧录/日志/发送，最高 921600
   - `role=fk_control` → FK-01 主控口，MCP 内部专用，绝不能用于烧录/日志
3. 若 `authed=false`，先完成密钥认证（SET_KEY / flashkey_auth 流程）。
4. 烧录使用 `flash-firmware` prompt 或 `flash()`；采集日志使用
   `log_open()` 开启后台监控，`log_close()` 关闭后读取 `flashkey://log`。
5. 出错时先读 `flashkey://docs/error-codes`，按 hint / recovery_tool 恢复
   （设备掉线通常先调 `recover(reattach=True)`）。

## 关键约束

- 永远不要按端口名猜测角色，不同系统上可能是 COMx / ttyACMx / ttyUSBx / cu.*。
- 烧录与日志互斥：返回 PORT_BUSY 时等待当前操作结束再重试。
"""


FLASH_GUIDE_DOC = """\
# 烧录指南（Ai-WB2 / Ai-M62）

## 端口选择（最重要）

- 必须选择 `list_ports()` 中 `role=fk_log`（WCH-LinkE VCP）的端口。
- 绝不能使用 `role=fk_control`（FK-01 主控口，MCP 内部专用）。
- 不要按端口名猜：不同系统上端口名不同（COMx / ttyACMx / ttyUSBx / cu.*）。

## 芯片 → 模式 / 默认波特率

| chip | mode | 默认 baud_rate | 烧录命令 |
| --- | --- | --- | --- |
| Ai-WB2 | break（默认） | 921600 | `make flash p=<port> b=<baud>`：串口打断，只烧 App，不含 boot2 |
| Ai-WB2 | isp | 921600 | `make eflash p=<port> b=<baud>`：需 BOOT↑ + RST 进入 ISP，全量含 boot2 |
| Ai-M62 | isp | 921600（FlashKey 串口上限） | `make flash`（SDK 内按 Ai-M62 对应芯片传 CHIP）：BOOT↑ + RST 进入 ISP；2000000 仅在外接 USB-UART 时可用 |

### Ai-WB2 两种模式

1. **串口打断烧录（break，默认）**：`make flash` 启动后会等待模组复位，
   工具自动触发一次 FK-01 RST 复位脉冲（检测到复位提示或短延时后触发，
   不依赖解析提示文本），模组重启后即可触发烧录。该模式只烧录 App
   应用程序，**不烧录 boot2**；如果无法触发烧录，说明固件不支持串口打断，
   应改用 ISP 模式。
2. **ISP 烧录（isp）**：芯片级烧录模式，需要芯片先进入 boot 烧录模式。
   FlashKey 拉高 BOOT 后发送复位脉冲即可进入 ISP 模式，之后执行 `make eflash`。
   该模式**全量烧录（含 boot2）**；执行过 `make erase_flash` 擦除芯片后必须使用 ISP 模式。

## 正确顺序

1. `list_ports()` → 记录 `role=fk_log` 的端口。
2. `status()` → 未认证先完成密钥认证（SET_KEY / flashkey_auth）。
3. `flash(firmware_path=<固件路径>, chip=<Ai-WB2/Ai-M62>, flash_port=<fk_log 端口>, baud_rate=<默认值>, ...)`。
4. 烧录后验证：`log_open(port=<fk_log 端口>, baud_rate=<目标日志波特率>, project=<项目名>)`
   → **`rst_pulse()` 复位模组（采集完整启动日志的关键，不能跳过）** → `log_close()`
   → 读取 `flashkey://log` 观察启动日志，
   并**自行分析日志判定启动是否正常**（有异常先排查，不要只转述日志原文）；
   日志会自动归档到 `~/flashkey-logs/<project>/`（每项目最多 10 份，超出覆盖最旧），
   可用 `flashkey://logs/<project>` 列历史；
   AT 模组可发送 `AT+GMR` 确认版本。

## 错误速查

- 错误码表：`flashkey://docs/error-codes`
- 设备掉线：`recover(reattach=True)`
- 端口选错：重新 `list_ports()` 选 `fk_log`
"""


def _error_codes_doc() -> str:
    """由 ERROR_GUIDE 生成 Markdown 错误码表。"""
    lines = [
        "# 错误码与恢复指引",
        "",
        "工具失败时统一返回 `[错误码] 信息 + 下一步: ...`（MCP `isError`）。",
        "下表与 README 错误码表同源，由 ERROR_GUIDE 自动生成。",
        "",
        "| code | 含义 | 下一步 | 可重试 | 恢复工具 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for code, info in ERROR_GUIDE.items():
        retry = "是" if info["retryable"] else "否"
        recovery = info.get("recovery_tool") or "—"
        lines.append(
            f"| {code} | {info['meaning']} | {info['hint']} | {retry} | {recovery} |"
        )
    return "\n".join(lines) + "\n"


ERROR_CODES_DOC = _error_codes_doc()


# ======================================================================
# Server instructions（≤300 字浓缩指引）
# ======================================================================

_INSTRUCTIONS = (
    "FlashKey MCP：先读资源 quickstart、flash-guide；烧录前先调 flash_guide(chip) 学标准流程。"
    "烧录/日志先调 list_ports() 按 role=fk_log 选端口，"
    "禁硬编码/猜端口名（/dev/ttyACM0、COM3）与 fk_control。"
    "status 确认已认证，未认证先密钥认证。"
    "烧录只用 flash（firmware_path、chip、flash_port），"
    "Ai-WB2 默认 break/921600，可 isp（make eflash 全量含 boot2）；"
    "Ai-M62 默认 isp/921600（FlashKey 串口上限）。"
    "烧后验证：log_open 后先 rst_pulse 复位采完整启动日志，再 log_close 并自行分析；或 AT+GMR。"
    "日志采集用 log_open(project=...)，close 自动归档 flashkey://logs/{project}（10 份/项目）。"
    "错误见 error-codes；设备掉线先 recover(reattach=True)。"
)


# ======================================================================
# Prompts
# ======================================================================


def _prompt_flash_firmware(
    chip: str,
    firmware_path: str,
    flash_port: str = "",
    mode: str = "",
    baud_rate: int = 0,
    flash_dir: str = "",
    tool: str = "",
) -> list[Any]:
    """按正确顺序输出烧录步骤（端口 → 认证 → 烧录 → 验证）。"""
    chip_key = normalize_chip(chip)
    module_name = MODULE_NAMES.get(chip_key, chip_key or "未知")
    if chip_key == "bl602":
        default_mode = "break"
        default_baud = 921600
        selected_mode = (mode or default_mode).lower()
        if selected_mode == "isp":
            chip_note = (
                f"{module_name} ISP 模式（make eflash）：全量烧录（含 boot2）。"
                "工具会自动 BOOT↑ + RST 脉冲让模组进入 ISP 模式后再执行 eflash。"
                "固件不支持串口打断或执行过 make erase_flash 后必须用此模式。"
            )
        else:
            selected_mode = "break"
            chip_note = (
                f"{module_name} 串口打断模式（默认，make flash）：只烧录 App，不烧 boot2。"
                "工具启动后自动触发一次 FK-01 RST 复位脉冲（检测到复位提示或短延时后，"
                "不依赖解析提示文本）；"
                "若无法触发，说明固件不支持串口打断，请改用 mode=\"isp\"（make eflash）。"
            )
    elif chip_key in ("bl616", "bl618"):
        default_mode = "isp"
        default_baud = 921600
        selected_mode = mode or default_mode
        chip_note = (
            f"{module_name} 使用 isp 模式，默认 921600（FlashKey 串口最高仅支持 921600；"
            f"2000000 需外接 USB-UART）；flash_dir 指向烧录工程目录。"
        )
    else:
        default_mode = "isp"
        default_baud = 921600
        selected_mode = mode or default_mode
        chip_note = "仅支持 Ai-WB2 / Ai-M62，请确认 chip 参数。"

    selected_baud = baud_rate or default_baud
    port_display = flash_port or "<由 list_ports() 选择 role=fk_log 的端口>"
    chip_arg = (
        "ai-wb2"
        if chip_key == "bl602"
        else "ai-m62"
        if chip_key in ("bl616", "bl618")
        else chip_key
    )

    extra = ""
    if flash_dir:
        extra += f"\n  - flash_dir: {flash_dir}"
    if tool:
        extra += f"\n  - tool（自定义命令）: {tool}"
    elif chip_key == "bl602" and selected_mode == "isp":
        sdk_display = flash_dir or "<flash_dir>"
        extra += (
            f"\n  - tool（ISP 烧录命令，make eflash）: "
            f"make -C {sdk_display} eflash p={{port}} b={{baud}}"
        )

    steps = (
        f"请按以下顺序执行烧录：\n"
        f"1. 调用 list_ports()，找到 role=fk_log（WCH-LinkE VCP）的端口；"
        f"绝不要使用 role=fk_control，也不要按端口名猜测。\n"
        f"2. 调用 status() 确认设备已连接且 authed=true；"
        f"未认证先完成密钥认证（SET_KEY / flashkey_auth）。\n"
        f"3. 调用 flash(firmware_path=\"{firmware_path}\", chip=\"{chip_arg}\", "
        f"flash_port=\"{port_display}\", baud_rate={selected_baud}, mode=\"{selected_mode}\")"
        f"{extra}\n"
        f"   - {chip_note}\n"
        f"4. 烧录完成后验证：先 log_open(port=\"{port_display}\", baud_rate=115200) "
        f"打开日志监控，然后**必须**调用 rst_pulse() 发送复位脉冲让模组重启，"
        f"才能采集到完整启动日志；再 log_close()，读取 flashkey://log 观察启动日志，"
        f"并自行分析日志判定启动是否正常（有异常先排查，不要只转述日志原文）；"
        f"AT 模组可发送 AT+GMR 确认版本。\n"
        f"5. 若返回错误码，先读 flashkey://docs/error-codes；"
        f"DEVICE_NOT_FOUND / HANDSHAKE_FAILED 时先 recover(reattach=True)。"
    )
    return [
        UserMessage(f"请为 {module_name} 烧录固件：{firmware_path}"),
        AssistantMessage(steps),
    ]


def _prompt_recover_device(error_code: str = "", context: str = "") -> list[Any]:
    """根据错误码输出恢复决策树。"""
    code = (error_code or "").strip().upper()
    info = ERROR_GUIDE.get(code)
    if info:
        recovery = info.get("recovery_tool") or "无需恢复工具，按 hint 处理"
        branch = (
            f"错误码 {code}（{info['meaning']}）\n"
            f"- 含义：{info['meaning']}\n"
            f"- 下一步：{info['hint']}\n"
            f"- 是否可直接重试：{'是' if info['retryable'] else '否'}\n"
            f"- 恢复工具：{recovery}\n"
        )
        if code == E.DEVICE_NOT_FOUND or code == E.HANDSHAKE_FAILED:
            branch += (
                "决策：先调用 recover(reattach=True) 重新挂载/握手；"
                "若仍失败，检查 WSL usbip attach 或 USB 物理连接。"
            )
        elif code == E.PORT_WRONG_ROLE:
            branch += (
                "决策：重新调用 list_ports()，按 role 字段选择 fk_log"
                "（WCH-LinkE VCP），不要用 fk_control。"
            )
        elif code == E.PORT_BUSY:
            branch += "决策：等待当前烧录/日志操作结束，或关闭占用串口的程序后重试。"
        elif code in (E.AUTH_REQUIRED, E.AUTH_FAILED):
            branch += "决策：先完成密钥认证（SET_KEY / flashkey_auth），再重试原操作。"
    else:
        branch = (
            "未识别到具体错误码，按通用流程处理：\n"
            "1. 调用 status() 检查设备连接与认证状态。\n"
            "2. 若设备掉线，调用 recover(reattach=True)。\n"
            "3. 若工具返回错误，读取 flashkey://docs/error-codes 并按 hint 处理。\n"
        )
    if context:
        branch += f"\n用户补充上下文：{context}"
    return [
        UserMessage(f"请帮我恢复 FlashKey 设备（错误码：{code or '未知'}）"),
        AssistantMessage(f"恢复步骤：\n{branch}"),
    ]


def _prompt_collect_logs(
    port: str = "",
    baud_rate: int = 115200,
) -> list[Any]:
    """输出后台日志采集步骤，并要求 AI 自行分析日志判定运行情况。"""
    port_display = port or "<由 list_ports() 选择 role=fk_log 的端口>"
    steps = (
        "请按以下顺序采集日志：\n"
        f"1. 调用 list_ports()，选择 role=fk_log（WCH-LinkE VCP）的端口；"
        f"不要使用 role=fk_control。\n"
        f"2. 调用 status() 确认设备已连接且 authed=true。\n"
        f"3. 调用 log_open(port=\"{port_display}\", baud_rate={baud_rate}, "
        f"project=\"<用户当前项目名，未知用 default>\")，立即返回，不需要持续监控串口。\n"
        f"4. 烧录后验证场景：**必须** rst_pulse() 发送复位脉冲让模组重启，"
        f"才能采集到完整启动日志（不要跳过复位）；其他场景可继续执行 boot_set() 等操作。\n"
        f"5. 操作完成后调用 log_close() 关闭监控并释放串口。\n"
        f"6. 读取资源 flashkey://log 获取本次日志；下次 log_open() 会覆盖旧日志。\n"
        f"7. 自行分析日志内容并判定运行情况：检查启动流程是否正常（boot 版本、"
        f"外设初始化、网络连接等关键打印），查找异常/报错/panic/AT 错误码；"
        f"给出结论（运行正常 / 启动失败及原因与建议），不要只把日志原文转述给用户。\n"
        f"8. 历史日志已自动归档：log_close() 会保存到 ~/flashkey-logs/<project>/，"
        f"每项目最多 10 份、超出覆盖最旧；用 flashkey://logs/{{project}} 列出历史、"
        f"flashkey://logs/{{project}}/{{file}} 读取某份历史日志；"
        f"如需转存到指定位置，再调用 log_dump(dest_path=...)。\n"
        f"注意：监控期间 flash / send 不可用（PORT_BUSY）；"
        f"若设备掉线，先 recover(reattach=True)。"
    )
    return [
        UserMessage("请采集目标芯片串口日志"),
        AssistantMessage(steps),
    ]


def register_prompts(mcp: Any) -> None:
    """向 FastMCP 注册三个 Prompt。"""
    mcp.add_prompt(
        Prompt(
            name="flash-firmware",
            title="烧录固件到 Ai-WB2 / Ai-M62",
            description=(
                "按正确顺序生成烧录步骤：先选 fk_log 端口，再确认认证状态，"
                "按芯片默认模式/波特率调用 flash，最后验证。"
            ),
            arguments=[
                PromptArgument(name="chip", description="模组名称：Ai-WB2 / Ai-M62", required=True),
                PromptArgument(name="firmware_path", description="固件文件绝对路径", required=True),
                PromptArgument(
                    name="mode",
                    description="可选；Ai-WB2：break（默认）/isp；Ai-M62：isp",
                ),
            ],
            fn=_prompt_flash_firmware,
        )
    )
    mcp.add_prompt(
        Prompt(
            name="recover-device",
            title="恢复 FlashKey 设备",
            description="根据错误码输出恢复决策树（设备掉线 / 端口选错 / 认证失败等）。",
            arguments=[
                PromptArgument(name="error_code", description="可选；工具返回的错误码，如 DEVICE_NOT_FOUND"),
                PromptArgument(name="context", description="可选；用户补充的故障上下文"),
            ],
            fn=_prompt_recover_device,
        )
    )
    mcp.add_prompt(
        Prompt(
            name="collect-logs",
            title="采集目标芯片日志",
            description=(
                "输出采集日志的正确步骤：选 fk_log 端口、确认认证、"
                "open 后继续其他操作、close 后读取 flashkey://log，"
                "并要求 AI 自行分析日志判定运行情况。"
            ),
            arguments=[
                PromptArgument(name="port", description="可选；role=fk_log 的串口"),
                PromptArgument(name="baud_rate", description="可选；日志波特率，默认 115200"),
            ],
            fn=_prompt_collect_logs,
        )
    )
