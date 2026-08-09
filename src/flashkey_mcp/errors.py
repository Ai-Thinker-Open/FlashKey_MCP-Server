"""FlashKey MCP 结构化错误（P0：错误指引）。

所有工具失败时统一返回带「错误码 + 下一步指引 + 是否可重试」的信息，
让 AI Agent 能自动判断：发生了什么、该不该重试、下一步调用哪个工具。
"""

from __future__ import annotations

from mcp.server.fastmcp.exceptions import ToolError


class E:
    """稳定错误码（Agent 与文档以此为准）。"""

    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"            # 设备未插入/未挂载
    HANDSHAKE_FAILED = "HANDSHAKE_FAILED"            # 握手失败/重连超时/连接中
    PORT_BUSY = "PORT_BUSY"                          # 串口被占用/烧录进行中
    PORT_WRONG_ROLE = "PORT_WRONG_ROLE"              # 用错端口角色
    AUTH_REQUIRED = "AUTH_REQUIRED"                  # 需要认证
    AUTH_FAILED = "AUTH_FAILED"                      # 认证失败（密钥错误）
    FLASH_PROTECTED = "FLASH_PROTECTED"              # Flash 读保护
    FLASH_VERIFY_FAILED = "FLASH_VERIFY_FAILED"      # 烧录校验不一致
    MODULE_NO_RESPONSE = "MODULE_NO_RESPONSE"        # 模组无响应
    MODULE_MANIFEST_INVALID = "MODULE_MANIFEST_INVALID"
    TIMEOUT = "TIMEOUT"                              # 响应超时
    FRAME_CRC = "FRAME_CRC"                          # 帧校验错误
    INVALID_ARG = "INVALID_ARG"                      # 参数错误
    INTERNAL = "INTERNAL_ERROR"                      # 未分类错误


class FlashkeyError(ToolError):
    """带错误码与下一步指引的 MCP 工具错误。

    ``code`` 供 Agent 稳定识别；``hint`` 给出一句话的下一步动作；
    ``retryable`` 表示是否可以直接重试；``recovery_tool`` 指向恢复工具。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
        retryable: bool = False,
        recovery_tool: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.recovery_tool = recovery_tool
        super().__init__(self.to_text())

    def to_text(self) -> str:
        parts = [f"[{self.code}] {self.message}"]
        if self.hint:
            parts.append(f"下一步: {self.hint}")
        if self.recovery_tool:
            parts.append(f"恢复: {self.recovery_tool}")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        d: dict = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        if self.retryable:
            d["retryable"] = True
        if self.recovery_tool:
            d["recovery_tool"] = self.recovery_tool
        return d


def map_require_authed_error(exc: Exception) -> FlashkeyError:
    """把 DeviceManager.require_authed() 的 RuntimeError 映射成结构化错误。"""
    msg = str(exc)
    low = msg.lower()
    if "认证" in msg or "auth" in low:
        return FlashkeyError(
            E.AUTH_REQUIRED, msg,
            hint="先完成密钥认证（SET_KEY / flashkey_auth 流程）后重试",
        )
    if ("未检测到" in msg or "未找到" in msg or "未插入" in msg
            or "not found" in low):
        return FlashkeyError(
            E.DEVICE_NOT_FOUND, msg,
            hint="插入 FK-01 并等待握手；WSL 下先确认 usbip 已挂载",
            retryable=True,
        )
    if "超时" in msg or "连接中" in msg or "timeout" in low:
        return FlashkeyError(
            E.HANDSHAKE_FAILED, msg,
            hint="稍候重试；若持续失败请检查 USB 链路/usbip 挂载",
            retryable=True,
        )
    return FlashkeyError(
        E.AUTH_REQUIRED, msg,
        hint="先完成密钥认证（SET_KEY / flashkey_auth 流程）后重试",
    )
