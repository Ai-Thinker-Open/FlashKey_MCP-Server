"""L1 protocol integration tests for FlashKey MCP Server (no hardware needed).

Covers:
- Server startup and module imports
- JSON-RPC initialize handshake
- tools/list returns all 16 tools
- Uninitialized request rejection
- Auth middleware (no hardware → graceful error)
- Garbage input tolerance
"""

from __future__ import annotations

import fcntl
import json
import os
import select
import subprocess
import sys
import time

# ── Path setup ──────────────────────────────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_DIR, "src")
sys.path.insert(0, SRC_DIR)

LATEST_PROTOCOL_VERSION = "2025-11-25"

EXPECTED_TOOLS = [
    "flashkey_ping",
    "flashkey_handshake",
    "flashkey_auth_status",
    "flashkey_boot_set",
    "flashkey_boot_get",
    "flashkey_rst_set",
    "flashkey_rst_get",
    "flashkey_rst_pulse",
    "flashkey_v5v_set",
    "flashkey_v5v_get",
    "flashkey_v3v3_set",
    "flashkey_v3v3_get",
    "flashkey_get_version",
    "flashkey_get_uid",
    "flashkey_get_status",
    "flashkey_enter_bootloader",
]

_FAILURES: list[str] = []


def _fail(msg: str) -> None:
    _FAILURES.append(msg)


# ── Helpers ─────────────────────────────────────────────────────────────

def _build_env() -> dict[str, str]:
    """Return an env dict with PYTHONPATH set to include the src dir."""
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC_DIR + ":" + env.get("PYTHONPATH", "")
    return env


def start_server() -> subprocess.Popen:
    """Start the MCP server subprocess connected via stdin/stdout."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "flashkey_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=PROJECT_DIR,
        env=_build_env(),
    )
    return proc


def _read_line(proc: subprocess.Popen, timeout: float = 5.0) -> str:
    """Read one line from the server's stdout with a timeout."""
    fd = proc.stdout.fileno()
    # Set stdout pipe to non-blocking so select works reliably
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    deadline = time.monotonic() + timeout
    buf = b""
    while time.monotonic() < deadline:
        # Check if process died
        if proc.poll() is not None:
            remaining = time.monotonic()
            stderr_text = ""
            try:
                stderr_text = proc.stderr.read(4096).decode(errors="replace")
            except Exception:
                pass
            raise RuntimeError(
                f"Server exited prematurely (code={proc.returncode}). "
                f"stderr={stderr_text!r}"
            )

        # Use select to wait for data with a short timeout
        rlist, _, _ = select.select([proc.stdout], [], [], 0.1)
        if not rlist:
            continue

        try:
            chunk = os.read(fd, 4096)
        except (BlockingIOError, OSError):
            time.sleep(0.01)
            continue

        if not chunk:
            time.sleep(0.01)
            continue

        buf += chunk
        if b"\n" in buf:
            line, rest = buf.split(b"\n", 1)
            # Put back any extra bytes after first line
            # (we assume single-message-per-response for our protocol)
            return line.decode("utf-8")

    # Timeout — grab whatever we have for diagnostics
    remaining = time.monotonic()
    stderr_text = ""
    try:
        stderr_text = proc.stderr.read(4096).decode(errors="replace")
    except Exception:
        pass
    raise TimeoutError(
        f"No response within {timeout}s. "
        f"buf={buf!r} stderr={stderr_text!r}"
    )


def send_request(
    proc: subprocess.Popen,
    method: str,
    params: dict | None = None,
    request_id: int = 1,
) -> dict:
    """Send a JSON-RPC 2.0 request and read the response."""
    req = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    line = json.dumps(req, ensure_ascii=False)
    proc.stdin.write(line.encode() + b"\n")
    proc.stdin.flush()
    raw = _read_line(proc)
    return json.loads(raw)


def send_notification(proc: subprocess.Popen, method: str, params: dict | None = None) -> None:
    """Send a JSON-RPC 2.0 notification (no response expected)."""
    req = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        req["params"] = params
    line = json.dumps(req, ensure_ascii=False)
    proc.stdin.write(line.encode() + b"\n")
    proc.stdin.flush()


def initialize_server(proc: subprocess.Popen) -> dict:
    """Perform the MCP initialize handshake.

    Returns the InitializeResult dict.
    """
    result = send_request(
        proc,
        "initialize",
        {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
        request_id=1,
    )
    send_notification(proc, "notifications/initialized")
    return result


def stop_server(proc: subprocess.Popen) -> None:
    """Gracefully terminate the server subprocess."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ── Test 1: Server startup ──────────────────────────────────────────────

def test_server_import() -> None:
    """Importing flashkey_mcp.server module must not raise."""
    try:
        import flashkey_mcp.server  # noqa: F401
    except Exception as exc:
        _fail(f"test_server_import FAILED: {exc}")
        return
    print("  test_server_import ✅")


def test_server_has_main() -> None:
    """server.py must expose a callable main() function."""
    try:
        from flashkey_mcp.server import main  # noqa: F811
        assert callable(main), "main is not callable"
    except (ImportError, AssertionError) as exc:
        _fail(f"test_server_has_main FAILED: {exc}")
        return
    print("  test_server_has_main ✅")


# ── Test 2: JSON-RPC Initialize 握手 ────────────────────────────────────

def test_jsonrpc_initialize() -> None:
    """Send initialize request, verify serverInfo and capabilities."""
    proc = start_server()
    try:
        result = initialize_server(proc)

        # Must contain result key
        assert "result" in result, f"No 'result' in response: {result}"
        r = result["result"]

        # Must have serverInfo
        assert "serverInfo" in r, f"No 'serverInfo': {r}"
        si = r["serverInfo"]
        assert "name" in si, f"No 'name' in serverInfo: {si}"
        assert "flashkey" in si["name"].lower(), (
            f"serverInfo.name should contain 'flashkey', got {si['name']!r}"
        )
        assert "version" in si, f"No 'version' in serverInfo: {si}"

        # Must have capabilities
        assert "capabilities" in r, f"No 'capabilities': {r}"
        caps = r["capabilities"]
        assert isinstance(caps, dict), f"capabilities not a dict: {caps}"

        # protocolVersion in response
        assert "protocolVersion" in r, f"No 'protocolVersion': {r}"
        print(f"  test_jsonrpc_initialize ✅  name={si['name']!r} version={si['version']!r}")
    except Exception as exc:
        _fail(f"test_jsonrpc_initialize FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Test 3: tools/list ──────────────────────────────────────────────────

def test_tools_list() -> None:
    """After initialize, tools/list must return exactly 16 tools."""
    proc = start_server()
    try:
        initialize_server(proc)

        result = send_request(proc, "tools/list", request_id=2)

        assert "result" in result, f"No 'result': {result}"
        r = result["result"]

        assert "tools" in r, f"No 'tools' key: {r}"
        tools = r["tools"]

        assert isinstance(tools, list), f"tools is not a list: {type(tools)}"
        assert len(tools) == 16, f"Expected 16 tools, got {len(tools)}"

        # Verify each tool has name and description
        for t in tools:
            assert "name" in t, f"Tool missing 'name': {t}"
            assert "description" in t, f"Tool {t['name']!r} missing 'description'"
            assert t["name"] in EXPECTED_TOOLS, (
                f"Unexpected tool name: {t['name']!r}"
            )

        # Verify all expected tool names are present
        actual_names = [t["name"] for t in tools]
        expected_set = set(EXPECTED_TOOLS)
        actual_set = set(actual_names)
        missing = expected_set - actual_set
        extra = actual_set - expected_set
        assert not missing, f"Missing tools: {sorted(missing)}"
        assert not extra, f"Unexpected tools: {sorted(extra)}"

        print(f"  test_tools_list ✅  ({len(tools)} tools, all names correct)")
    except Exception as exc:
        _fail(f"test_tools_list FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Test 4: 未初始化拒绝测试 ─────────────────────────────────────────────

def test_uninitialized_rejected() -> None:
    """Sending tools/list without initialize must return a JSON-RPC error."""
    proc = start_server()
    try:
        # Send tools/list immediately without initialize
        result = send_request(proc, "tools/list", request_id=1)

        # Expect a JSON-RPC error response, not a result
        if "error" in result:
            err = result["error"]
            # Must have code and message
            assert "code" in err, f"Error missing 'code': {err}"
            assert "message" in err, f"Error missing 'message': {err}"
            print(f"  test_uninitialized_rejected ✅  code={err['code']} message={err['message']!r}")
        elif "result" in result:
            _fail(
                "test_uninitialized_rejected FAILED: "
                "Server accepted tools/list without initialize"
            )
        else:
            _fail(f"test_uninitialized_rejected FAILED: unexpected response: {result}")
    except Exception as exc:
        _fail(f"test_uninitialized_rejected FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Test 5: Auth 中间件测试（无硬件） ──────────────────────────────────────

def test_auth_middleware_no_hardware() -> None:
    """Calling any tool without FlashKey hardware returns error, not crash."""
    proc = start_server()
    try:
        initialize_server(proc)

        # Send tools/call for flashkey_boot_set (requires auth + hardware)
        result = send_request(
            proc,
            "tools/call",
            {"name": "flashkey_boot_set", "arguments": {"value": True}},
            request_id=3,
        )

        # Server should not crash. Should return a result with error content
        # since _wrap_tool catches RuntimeError.
        assert "result" in result, (
            f"Expected a result, got: {result}"
        )
        r = result["result"]

        # The _wrap_tool wrapper catches RuntimeError and returns
        # {"error": "..."} as the content of a successful tool call.
        assert "content" in r, f"Missing 'content' in tool result: {r}"
        content = r["content"]
        assert isinstance(content, list), f"content is not a list: {content}"
        assert len(content) > 0, "content is empty"

        # At least one text content should mention no device or not authed
        texts = [c["text"] for c in content if c.get("type") == "text"]
        combined = " ".join(texts)
        assert "No FlashKey device" in combined or "Not authenticated" in combined, (
            f"Expected 'No FlashKey device' or 'Not authenticated' in tool response, "
            f"got: {combined}"
        )
        print(f"  test_auth_middleware_no_hardware ✅  msg={combined!r}")
    except Exception as exc:
        _fail(f"test_auth_middleware_no_hardware FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Test 6: 垃圾输入容错 ─────────────────────────────────────────────────

def test_garbage_input() -> None:
    """Sending invalid JSON must not crash the server.

    The MCP FastMCP server logs the parse error internally and sends a
    log notification. The server must remain alive for further requests.
    """
    proc = start_server()
    try:
        # Send garbage
        garbage = b"this is not json at all!!!\n"
        proc.stdin.write(garbage)
        proc.stdin.flush()

        # Read whatever the server sends back (should be a log notification)
        raw = _read_line(proc, timeout=3.0)
        result = json.loads(raw)

        # The server should send a log notification (not crash)
        assert isinstance(result, dict), f"Expected dict, got: {result}"
        assert result.get("method") == "notifications/message", (
            f"Expected notifications/message, got: {result}"
        )
        params = result.get("params", {})
        assert params.get("level") == "error", f"Expected error level, got: {params}"
        print(f"  test_garbage_input ✅  server stayed alive, logged notification")

        # Verify server is still alive and functional
        # Send initialize to confirm
        init_result = send_request(
            proc,
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
            request_id=1,
        )
        assert "result" in init_result, f"Server dead after garbage: {init_result}"
        print(f"  test_garbage_input ✅  server still functional after garbage")
    except Exception as exc:
        _fail(f"test_garbage_input FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Test 7: Call non-existent tool ──────────────────────────────────────

def test_unknown_tool() -> None:
    """Calling a non-existent tool returns a tool-level error (isError: true)."""
    proc = start_server()
    try:
        initialize_server(proc)

        result = send_request(
            proc,
            "tools/call",
            {"name": "nonexistent_tool", "arguments": {}},
            request_id=4,
        )

        # The FastMCP server returns a result with isError: true for unknown tools
        assert "result" in result, f"Expected a result, got: {result}"
        r = result["result"]
        assert isinstance(r, dict), f"Result is not a dict: {r}"
        assert r.get("isError") is True, (
            f"Expected isError=true, got: {r}"
        )
        assert "content" in r, f"Missing 'content' in result: {r}"
        content = r["content"]
        assert isinstance(content, list) and len(content) > 0, (
            f"Unexpected content: {content}"
        )
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        combined = " ".join(texts)
        assert "unknown tool" in combined.lower(), (
            f"Expected 'unknown tool' in response, got: {combined!r}"
        )
        print(f"  test_unknown_tool ✅  isError=true msg={combined!r}")
    except Exception as exc:
        _fail(f"test_unknown_tool FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Runner ──────────────────────────────────────────────────────────────

def run_all() -> None:
    """Run all tests in order, print summary."""
    global _FAILURES
    _FAILURES = []

    tests = [
        ("Server Import", test_server_import),
        ("Server has main()", test_server_has_main),
        ("JSON-RPC Initialize", test_jsonrpc_initialize),
        ("tools/list (16 tools)", test_tools_list),
        ("Uninitialized Rejected", test_uninitialized_rejected),
        ("Auth Middleware (no HW)", test_auth_middleware_no_hardware),
        ("Garbage Input", test_garbage_input),
        ("Unknown Tool", test_unknown_tool),
    ]

    print("=" * 64)
    print("FlashKey MCP Server - L1 Protocol Integration Tests")
    print("=" * 64)
    print(f"Protocol version: {LATEST_PROTOCOL_VERSION}")
    print()

    for name, fn in tests:
        print(f"[{name}]")
        try:
            fn()
        except Exception as exc:
            _fail(f"{name} UNHANDLED EXCEPTION: {exc}")
        print()

    # Summary
    print("=" * 64)
    total = len(tests)
    passed = total - len(_FAILURES)
    print(f"Results: {passed}/{total} passed")
    if _FAILURES:
        print("FAILURES:")
        for f in _FAILURES:
            print(f"  ❌ {f}")
        print(f"\n❌ {len(_FAILURES)} test(s) FAILED")
        sys.exit(1)
    else:
        print("✅ All tests PASSED")
        sys.exit(0)


if __name__ == "__main__":
    run_all()
