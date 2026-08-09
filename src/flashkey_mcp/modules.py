"""FlashKey 扩展模块动态工具支持。

扩展模块通过 USART2（原始字节透传）+ I2C（身份与工具清单）接入 FlashKey。
固件把模块清单以 0x61 分片上报，把模块自主上报的数据以 0x63 事件转发。
本模块负责：

- 解析模块 JSON 清单（``module{}`` + ``tools[]``）；
- 按清单运行时注册/注销 ``mod_*`` 动态工具（FastMCP ``add_tool``/``remove_tool``）；
- 通用动态 handler：参数序列化 JSON → 0x62 转发 → 响应窗口收集 0x63 事件返回；
- 模块 IO 加锁保证单飞。

模块是工具的实际执行者；FlashKey 与 MCP 均不解析模块业务字节内容（全中继）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import deque
from typing import Any, Callable, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import Tool
from mcp.server.fastmcp.utilities.func_metadata import (
    ArgModelBase,
    FuncMetadata,
)
from pydantic import ConfigDict, Field, create_model

from flashkey_mcp.commands import MODULE_IO_MAX

logger = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────
DEFAULT_MODULE_TIMEOUT_MS = 1500
MODULE_TIMEOUT_ENV = "FLASHKEY_MODULE_TIMEOUT_MS"
MODULE_TOOL_PREFIX = "mod_"


def module_timeout_ms() -> int:
    """Response-window duration in ms (env-overridable, default 1500)."""
    try:
        return max(100, int(os.environ.get(MODULE_TIMEOUT_ENV, DEFAULT_MODULE_TIMEOUT_MS)))
    except ValueError:
        return DEFAULT_MODULE_TIMEOUT_MS


class ManifestError(ValueError):
    """Raised when a module manifest is malformed or invalid."""


def sanitize_tool_name(name: str) -> str:
    """Build a safe ``mod_*`` tool name from a module tool name."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(name))
    cleaned = cleaned.strip("_")
    if not cleaned:
        cleaned = "module"
    return f"{MODULE_TOOL_PREFIX}{cleaned}"


# ======================================================================
# Manifest parsing
# ======================================================================

def parse_manifest(raw: bytes) -> dict[str, Any]:
    """Parse and validate a module manifest (JSON, 0xFF-padded, <= 1024B).

    Expected shape::

        {
          "module": {"name": "...", "vendor": "...", "version": "...",
                     "description": "..."},
          "tools": [
            {"name": "...", "description": "...",
             "parameters": {"type": "object", "properties": {...},
                            "required": [...]}}
          ]
        }

    Args:
        raw: Manifest bytes as reported by the firmware (0x61 fragments).

    Returns:
        Normalized manifest dict: ``{"module": dict, "tools": [dict...]}``.

    Raises:
        ManifestError: If the manifest is empty, invalid JSON, or lacks the
            required ``module.name`` / ``tools`` fields.
    """
    data = (raw or b"").rstrip(b"\xff").strip()
    if not data:
        raise ManifestError("清单为空")
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise ManifestError(f"清单不是合法 JSON: {exc}") from exc

    if not isinstance(obj, dict):
        raise ManifestError("清单根节点必须是 JSON 对象")

    mod = obj.get("module")
    if not isinstance(mod, dict) or not str(mod.get("name", "")).strip():
        raise ManifestError("清单缺少 module.name")

    tools_raw = obj.get("tools")
    if not isinstance(tools_raw, list):
        raise ManifestError("清单缺少 tools 列表")

    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in tools_raw:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            raise ManifestError("tools 每项必须包含非空 name")
        tname = sanitize_tool_name(str(item["name"]))
        if tname in seen:
            raise ManifestError(f"工具名重复（净化后）: {tname}")
        seen.add(tname)

        params = item.get("parameters")
        if params is not None and not isinstance(params, dict):
            raise ManifestError(f"工具 {item['name']} 的 parameters 必须是对象")
        if params is None:
            params = {"type": "object", "properties": {}}
        else:
            params.setdefault("type", "object")
            params.setdefault("properties", {})
        if not isinstance(params.get("properties"), dict):
            raise ManifestError(f"工具 {item['name']} 的 properties 必须是对象")

        tools.append({
            "name": str(item["name"]),
            "tool_name": tname,
            "description": str(item.get("description", "")),
            "parameters": params,
        })

    return {
        "module": {k: v for k, v in mod.items() if isinstance(v, (str, int, float, bool))},
        "tools": tools,
    }


# ======================================================================
# JSON Schema → pydantic arg model（FastMCP 动态工具）
# ======================================================================

def _json_type_annotation(meta: dict[str, Any]) -> Any:
    """Map a JSON Schema property type to a Python annotation."""
    t = meta.get("type")
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "string":
        enum = [v for v in meta.get("enum", []) if v is not None]
        if enum:
            return Literal[tuple(enum)]  # type: ignore[valid-type]
        return str
    if t == "array":
        return list
    if t == "object":
        return dict
    return str


def _make_arg_model(schema: dict[str, Any]) -> type[ArgModelBase]:
    """Build a pydantic arg model from a module tool's JSON Schema.

    Property names may not be valid Python identifiers (spaces/dashes), so
    fields use generated names with aliases to the original property names.
    ``ArgModelBase.model_dump_one_level()`` dumps by alias, which lets the
    generic ``**kwargs`` handler receive the original argument keys.
    """
    props: dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    fields: dict[str, tuple[Any, Any]] = {}

    for idx, (pname, pmeta) in enumerate(props.items()):
        ann = _json_type_annotation(pmeta if isinstance(pmeta, dict) else {})
        py_name = f"p{idx}"
        description = pmeta.get("description") if isinstance(pmeta, dict) else None
        if pname in required:
            fields[py_name] = (
                ann,
                Field(alias=pname, description=description),
            )
        else:
            default = pmeta.get("default") if isinstance(pmeta, dict) else None
            if default is None:
                fields[py_name] = (
                    Optional[ann],
                    Field(alias=pname, default=None, description=description),
                )
            else:
                fields[py_name] = (
                    ann,
                    Field(alias=pname, default=default, description=description),
                )

    return create_model(
        "ModuleToolArgs",
        __base__=ArgModelBase,
        __config__=ConfigDict(populate_by_name=True, extra="ignore"),
        **fields,
    )


def _install_tool(mcp: Any, tool: Tool) -> None:
    """Register a Tool instance on the FastMCP server.

    FastMCP 1.28's public ``add_tool`` derives JSON Schema from the function
    signature and has no way to pass an explicit schema, so we insert the
    fully-constructed Tool directly into the tool manager (``remove_tool``
    remains the public API).  Falls back to ``add_tool`` + parameter swap
    if the internal dict is unavailable.
    """
    manager = getattr(mcp, "_tool_manager", None)
    if manager is not None and hasattr(manager, "_tools"):
        manager._tools[tool.name] = tool
        return
    mcp.add_tool(tool.fn, name=tool.name, description=tool.description)
    existing = getattr(mcp, "_tool_manager", None)
    if existing is not None:
        existing._tools[tool.name].parameters = tool.parameters


# ======================================================================
# ModuleRegistry
# ======================================================================

class ModuleRegistry:
    """Holds the current module manifest and syncs ``mod_*`` tools.

    The registry itself is device-agnostic: the server wires an IO handler
    (``set_io_handler``) that sends 0x62 and collects the 0x63 response
    window, and the DeviceManager feeds manifest updates (``update``) and
    unsolicited module data (``on_module_data``).
    """

    def __init__(self, mcp: Any | None = None) -> None:
        self._mcp = mcp
        self._manifest: dict[str, Any] | None = None
        self._manifest_raw: bytes | None = None
        self._registered: set[str] = set()
        self._last_error: str = ""
        self._io_handler: Callable[[bytes], dict[str, Any]] | None = None
        self._io_lock = threading.Lock()
        self._sync_lock = threading.Lock()
        # 窗口外到达的模块自主数据（仅观测用，不持久化）
        self._recent_data: deque[tuple[str, int, str]] = deque(maxlen=8)
        self._data_total_frames = 0
        self._data_total_bytes = 0

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def attach(self, mcp: Any) -> None:
        """Attach the FastMCP server and (re)sync tools for the current manifest."""
        self._mcp = mcp
        self._sync_tools()

    def set_io_handler(self, handler: Callable[[bytes], dict[str, Any]]) -> None:
        """Set the 0x62-forward + response-window collector used by mod_* tools."""
        self._io_handler = handler

    # ------------------------------------------------------------------
    # Manifest / tool sync
    # ------------------------------------------------------------------

    @property
    def manifest(self) -> dict[str, Any] | None:
        return self._manifest

    @property
    def registered_tools(self) -> list[str]:
        with self._sync_lock:
            return sorted(self._registered)

    def update(self, raw: bytes | None) -> bool:
        """Parse a new manifest and sync tools; return True if anything changed."""
        if raw is None or not raw.strip():
            return self.clear()
        try:
            manifest = parse_manifest(raw)
        except ManifestError as exc:
            self._last_error = f"清单解析失败: {exc}"
            logger.warning("Module manifest rejected: %s", self._last_error)
            return False  # 非法清单：保留上一份可用清单与工具

        if manifest == self._manifest:
            self._manifest_raw = raw
            return False

        self._manifest = manifest
        self._manifest_raw = raw
        self._last_error = ""
        self._sync_tools()
        logger.info(
            "Module manifest updated: module=%s, tools=%s",
            manifest["module"].get("name"),
            [t["tool_name"] for t in manifest["tools"]],
        )
        return True

    def clear(self) -> bool:
        """Drop the manifest and remove all registered mod_* tools."""
        with self._sync_lock:
            changed = self._manifest is not None or bool(self._registered)
            removed = len(self._registered)
            self._manifest = None
            self._manifest_raw = None
            if changed:
                self._remove_all_tools()
                self._registered = set()
                self._last_error = ""
                logger.info("Module disappeared — removed %d dynamic tools", removed)
            return changed

    def _sync_tools(self) -> None:
        with self._sync_lock:
            desired: set[str] = set()
            if self._manifest:
                desired = {t["tool_name"] for t in self._manifest["tools"]}

            if self._mcp is None:
                self._registered = desired
                return

            # 移除已消失的工具
            for name in list(self._registered):
                if name not in desired:
                    self._remove_tool(name)

            # 注册新工具
            if self._manifest:
                for spec in self._manifest["tools"]:
                    if spec["tool_name"] not in self._registered:
                        self._add_tool(spec)
            self._registered = desired

    def _add_tool(self, spec: dict[str, Any]) -> None:
        name = spec["tool_name"]
        description = spec["description"] or f"调用扩展模块工具 {name}"
        parameters = spec["parameters"]
        arg_model = _make_arg_model(parameters)
        handler = self._make_handler(name)
        tool = Tool(
            fn=handler,
            name=name,
            description=description,
            parameters=parameters,
            fn_metadata=FuncMetadata(arg_model=arg_model),
            is_async=False,
        )
        _install_tool(self._mcp, tool)
        logger.info("Registered dynamic module tool: %s", name)

    def _remove_tool(self, name: str) -> None:
        try:
            self._mcp.remove_tool(name)
        except Exception:
            # 工具可能已被外部移除；以 registered 集合为准
            pass
        logger.info("Removed dynamic module tool: %s", name)

    def _remove_all_tools(self) -> None:
        for name in list(self._registered):
            self._remove_tool(name)

    # ------------------------------------------------------------------
    # Tool invocation
    # ------------------------------------------------------------------

    def _make_handler(self, tool_name: str):
        def handler(**kwargs: Any) -> dict[str, Any]:
            return self._invoke(tool_name, kwargs)
        return handler

    def _invoke(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Serialize args to JSON and forward to the module via 0x62."""
        handler = self._io_handler
        if handler is None:
            raise ToolError("模块 IO 桥未就绪（MCP 尚未连接设备）")

        payload = json.dumps(
            {k: v for k, v in args.items() if v is not None},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MODULE_IO_MAX:
            raise ToolError(
                f"工具参数序列化后 {len(payload)}B 超过单帧上限 {MODULE_IO_MAX}B"
            )

        if not self._io_lock.acquire(blocking=False):
            raise ToolError("另一个模块工具调用正在进行，请稍候重试")
        try:
            result = handler(payload)
        except TimeoutError as exc:
            raise ToolError(f"模块无响应: {exc}") from exc
        finally:
            self._io_lock.release()

        chunks: list[bytes] = result.get("chunks", [])
        data = b"".join(chunks)
        return {
            "tool": tool_name,
            "sent_bytes": result.get("sent", len(payload)),
            "response_bytes": len(data),
            "response_hex": data.hex() if data else "",
            "response_text": _decode_module_data(data),
            "response_frames": len(chunks),
            "timed_out": bool(result.get("timed_out", False)),
        }

    # ------------------------------------------------------------------
    # Unsolicited module data (0x63 outside a response window)
    # ------------------------------------------------------------------

    def on_module_data(self, data: bytes) -> None:
        """Record module autonomous data that arrived outside an IO window."""
        import time as _time

        self._data_total_frames += 1
        self._data_total_bytes += len(data)
        self._recent_data.appendleft((
            _time.strftime("%H:%M:%S"),
            len(data),
            _decode_module_data(data),
        ))

    @property
    def data_stats(self) -> dict[str, Any]:
        return {
            "total_frames": self._data_total_frames,
            "total_bytes": self._data_total_bytes,
            "recent": list(self._recent_data),
        }

    # ------------------------------------------------------------------
    # Info for module_info / status
    # ------------------------------------------------------------------

    def info(self) -> dict[str, Any]:
        """Return module presence, identity, tools and data stats."""
        base = {
            "present": self._manifest is not None,
            "tools": self.registered_tools,
            "last_error": self._last_error,
            "data": self.data_stats,
        }
        if self._manifest is None:
            base["module"] = None
            return base
        base["module"] = self._manifest["module"]
        return base


def _decode_module_data(data: bytes) -> str:
    """Decode module bytes as UTF-8 with a compact summary."""
    if not data:
        return ""
    text = data.decode("utf-8", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= 200:
        return text
    return f"{text[:200]}... ({len(data)}B)"
