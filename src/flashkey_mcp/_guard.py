"""Runtime guard — prevents direct import of flashkey_mcp outside MCP framework.

flashkey-mcp modules are designed to run inside the MCP server process.
Direct imports from scripts or interactive Python sessions are blocked
to prevent AI agents from bypassing the MCP tool interface.

Set ``FLASHKEY_MCP=1`` environment variable to allow imports (used by
the MCP server entry point and test scripts).
"""

from __future__ import annotations

import os
import sys

_GUARD_MSG = (
    "\n"
    "╔══════════════════════════════════════════════════════════════╗\n"
    "║  flashkey-mcp 是 MCP 服务进程，不能直接在脚本中 import。  ║\n"
    "║                                                              ║\n"
    "║  正确做法：通过 MCP 工具调用                                 ║\n"
    "║    flashkey_status() / flashkey_flash() / flashkey_log()     ║\n"
    "║                                                              ║\n"
    "║  如果需要运行测试脚本：                                      ║\n"
    "║    FLASHKEY_MCP=1 python tests/test_xxx.py                   ║\n"
    "╚══════════════════════════════════════════════════════════════╝\n"
)


def _require_mcp_runtime() -> None:
    """Raise ``RuntimeError`` if not running in MCP or test context."""
    if os.environ.get("FLASHKEY_MCP") == "1":
        return
    # Allow test scripts (run from tests/ directory or via pytest)
    if _is_test_context():
        return
    print(_GUARD_MSG, file=sys.stderr)
    raise RuntimeError(
        "flashkey-mcp modules cannot be imported directly outside the "
        "MCP server process. Use MCP tools (flashkey_status / "
        "flashkey_flash / flashkey_log). Set FLASHKEY_MCP=1 for testing."
    )


def _is_test_context() -> bool:
    """Detect if we're running in a test/development context."""
    # pytest or unittest runner
    if "pytest" in sys.modules:
        return True
    # Script invoked from a tests/ directory
    script = sys.argv[0] if sys.argv else ""
    if "/tests/" in script or "\\tests\\" in script:
        return True
    # Running under a test framework
    for frame in sys._current_frames().values():
        co_name = frame.f_code.co_name
        if co_name.startswith("test_") or co_name == "run_unittest":
            return True
    return False
