"""L1 protocol integration tests for FlashKey MCP Server (no hardware needed).

Covers:
- Server startup and module imports
- JSON-RPC initialize handshake
- tools/list returns all 30 tools
- resources/list + resources/read
- prompts/list + prompts/get
- Uninitialized request rejection
- Auth middleware (no hardware → graceful error)
- Garbage input tolerance
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading

# ── Path setup ──────────────────────────────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_DIR, "src")
sys.path.insert(0, SRC_DIR)

LATEST_PROTOCOL_VERSION = "2025-11-25"

EXPECTED_TOOLS = [
    "status",
    "list_ports",
    "recover",
    "module_info",
    "ping",
    "auth_status",
    "boot_set",
    "boot_get",
    "rst_set",
    "rst_get",
    "rst_pulse",
    "v5v_set",
    "v5v_get",
    "v3v3_set",
    "v3v3_get",
    "vusb_set",
    "vusb_get",
    "flash_guide",
    "get_version",
    "get_uid",
    "get_events",
    "get_status",
    "enter_bootloader",
    "flash",
    "log_open",
    "log_close",
    "log_dump",
    "send",
    "firmware_check",
    "firmware_flash",
]

EXPECTED_RESOURCES = [
    "flashkey://docs/quickstart",
    "flashkey://docs/flash-guide",
    "flashkey://docs/error-codes",
    "flashkey://status",
    "flashkey://ports",
    "flashkey://log",
]

EXPECTED_PROMPTS = ["flash-firmware", "recover-device", "collect-logs"]

EXPECTED_TEMPLATES = [
    "flashkey://logs/{project}",
    "flashkey://logs/{project}/{file}",
]

_FAILURES: list[str] = []


def _fail(msg: str) -> None:
    _FAILURES.append(msg)


# ── Helpers ─────────────────────────────────────────────────────────────

def _build_env() -> dict[str, str]:
    """Return an env dict with PYTHONPATH set to include the src dir."""
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    return env


def start_server() -> subprocess.Popen:
    """Start the MCP server subprocess connected via stdin/stdout."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "flashkey_mcp.server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=PROJECT_DIR,
        env=_build_env(),
    )
    return proc


def _read_line(proc: subprocess.Popen, timeout: float = 5.0) -> str:
    """Read one line from the server's stdout with a timeout."""
    output_queue = getattr(proc, "_flashkey_stdout_queue", None)
    if output_queue is None:
        output_queue = queue.Queue()
        proc._flashkey_stdout_queue = output_queue

        def _pump_stdout() -> None:
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    output_queue.put(line)
            finally:
                output_queue.put(None)

        threading.Thread(target=_pump_stdout, daemon=True).start()

    try:
        line = output_queue.get(timeout=timeout)
    except queue.Empty as exc:
        if proc.poll() is not None:
            stderr_text = proc.stderr.read(4096).decode(errors="replace")
            raise RuntimeError(
                f"Server exited prematurely (code={proc.returncode}). "
                f"stderr={stderr_text!r}"
            ) from exc
        raise TimeoutError(f"No response within {timeout}s") from exc

    if line is None:
        stderr_text = proc.stderr.read(4096).decode(errors="replace")
        raise RuntimeError(
            f"Server stdout closed (code={proc.poll()}). stderr={stderr_text!r}"
        )
    return line.rstrip(b"\r\n").decode("utf-8")


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
    """After initialize, tools/list must return all expected tools."""
    proc = start_server()
    try:
        initialize_server(proc)

        result = send_request(proc, "tools/list", request_id=2)

        assert "result" in result, f"No 'result': {result}"
        r = result["result"]

        assert "tools" in r, f"No 'tools' key: {r}"
        tools = r["tools"]

        assert isinstance(tools, list), f"tools is not a list: {type(tools)}"
        assert len(tools) == len(EXPECTED_TOOLS), (
            f"Expected {len(EXPECTED_TOOLS)} tools, got {len(tools)}"
        )

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

        # Send tools/call for boot_set (requires auth + hardware)
        result = send_request(
            proc,
            "tools/call",
            {"name": "boot_set", "arguments": {"value": True}},
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
        assert (
            "No FlashKey device" in combined
            or "Not authenticated" in combined
            or "未检测到" in combined
        ), (
            f"Expected a no-device / not-authenticated error in tool response, "
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


# ── Test 8: Resources ───────────────────────────────────────────────────

def test_resources_list_and_read() -> None:
    """resources/list returns 5 URIs; docs read with key facts."""
    proc = start_server()
    try:
        initialize_server(proc)

        result = send_request(proc, "resources/list", request_id=5)
        assert "result" in result, f"No 'result': {result}"
        resources = result["result"]["resources"]
        uris = [r["uri"] for r in resources]
        assert set(EXPECTED_RESOURCES) <= set(uris), (
            f"Missing resources: {set(EXPECTED_RESOURCES) - set(uris)}"
        )

        flash = send_request(
            proc,
            "resources/read",
            {"uri": "flashkey://docs/flash-guide"},
            request_id=6,
        )
        assert "result" in flash, f"No 'result': {flash}"
        flash_text = flash["result"]["contents"][0]["text"]
        assert "fk_log" in flash_text
        assert "Ai-WB2" in flash_text
        assert "Ai-M62" in flash_text
        assert "921600" in flash_text

        errors = send_request(
            proc,
            "resources/read",
            {"uri": "flashkey://docs/error-codes"},
            request_id=7,
        )
        assert "result" in errors, f"No 'result': {errors}"
        errors_text = errors["result"]["contents"][0]["text"]
        assert "AUTH_REQUIRED" in errors_text
        assert "DEVICE_NOT_FOUND" in errors_text

        print(
            f"  test_resources_list_and_read ✅  "
            f"({len(uris)} resources, docs read ok)"
        )
    except Exception as exc:
        _fail(f"test_resources_list_and_read FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Test 8b: Resource templates ────────────────────────────────────────

def test_resource_templates_list() -> None:
    """resources/templates/list exposes the historical-log templates."""
    proc = start_server()
    try:
        initialize_server(proc)

        result = send_request(proc, "resources/templates/list", request_id=14)
        assert "result" in result, f"No 'result': {result}"
        templates = result["result"]["resourceTemplates"]
        uris = [t["uriTemplate"] for t in templates]
        assert set(EXPECTED_TEMPLATES) <= set(uris), (
            f"Missing templates: {set(EXPECTED_TEMPLATES) - set(uris)}"
        )

        print(
            f"  test_resource_templates_list ✅  "
            f"({len(templates)} templates)"
        )
    except Exception as exc:
        _fail(f"test_resource_templates_list FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Test 9: Prompts ─────────────────────────────────────────────────────

def test_prompts_list_and_get() -> None:
    """prompts/list returns 3 names; flash-firmware renders correct sequence."""
    proc = start_server()
    try:
        initialize_server(proc)

        result = send_request(proc, "prompts/list", request_id=8)
        assert "result" in result, f"No 'result': {result}"
        prompt_defs = result["result"]["prompts"]
        prompts = [p["name"] for p in prompt_defs]
        assert set(EXPECTED_PROMPTS) <= set(prompts), (
            f"Missing prompts: {set(EXPECTED_PROMPTS) - set(prompts)}"
        )
        flash_firmware = next(
            p for p in prompt_defs if p["name"] == "flash-firmware"
        )
        assert len(flash_firmware.get("arguments", [])) == 3, (
            f"flash-firmware should expose only 3 arguments: {flash_firmware}"
        )

        rendered = send_request(
            proc,
            "prompts/get",
            {
                "name": "flash-firmware",
                "arguments": {"chip": "ai-wb2", "firmware_path": "/tmp/fw.bin"},
            },
            request_id=9,
        )
        assert "result" in rendered, f"No 'result': {rendered}"
        messages = rendered["result"]["messages"]
        assert len(messages) == 2
        texts = " ".join(m["content"]["text"] for m in messages)
        assert "list_ports" in texts
        assert "fk_log" in texts
        assert "921600" in texts
        assert 'chip="ai-wb2"' in texts
        assert "Ai-WB2" in texts
        assert "flash" in texts

        logs = send_request(
            proc,
            "prompts/get",
            {"name": "collect-logs"},
            request_id=12,
        )
        assert "result" in logs, f"No 'result': {logs}"
        log_texts = " ".join(
            m["content"]["text"] for m in logs["result"]["messages"]
        )
        assert "log_open" in log_texts
        assert "log_close" in log_texts
        assert "flashkey://log" in log_texts
        assert "分析" in log_texts
        assert "log_dump" in log_texts
        assert "project" in log_texts
        assert "flashkey://logs" in log_texts

        print(
            f"  test_prompts_list_and_get ✅  "
            f"({len(prompts)} prompts, flash-firmware rendered)"
        )
    except Exception as exc:
        _fail(f"test_prompts_list_and_get FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Test 10: Dynamic resources (offline-safe) ───────────────────────────

def test_dynamic_resources_offline_safe() -> None:
    """status/ports resources must return JSON instead of crashing offline."""
    proc = start_server()
    try:
        initialize_server(proc)

        status = send_request(
            proc,
            "resources/read",
            {"uri": "flashkey://status"},
            request_id=10,
        )
        assert "result" in status, f"No 'result': {status}"
        status_data = json.loads(status["result"]["contents"][0]["text"])
        assert isinstance(status_data, dict)
        assert status_data.get("authed") is True or "error" in status_data, (
            f"Offline status should include error: {status_data}"
        )

        ports = send_request(
            proc,
            "resources/read",
            {"uri": "flashkey://ports"},
            request_id=11,
        )
        assert "result" in ports, f"No 'result': {ports}"
        ports_data = json.loads(ports["result"]["contents"][0]["text"])
        assert isinstance(ports_data, dict)
        assert "ports" in ports_data
        assert isinstance(ports_data["ports"], list)

        log_resource = send_request(
            proc,
            "resources/read",
            {"uri": "flashkey://log"},
            request_id=13,
        )
        assert "result" in log_resource, f"No 'result': {log_resource}"
        assert isinstance(log_resource["result"]["contents"][0]["text"], str)

        print(
            "  test_dynamic_resources_offline_safe ✅  "
            "status/ports/log resources readable"
        )
    except Exception as exc:
        _fail(f"test_dynamic_resources_offline_safe FAILED: {exc}")
    finally:
        stop_server(proc)


# ── Test 11: flash_guide returns the standard procedure (no hardware) ──

def test_flash_guide_tool() -> None:
    """flash_guide returns the full flashing procedure for the chip."""
    from flashkey_mcp.server import _tool_flash_guide

    result = _tool_flash_guide(chip="ai-wb2")
    assert result["ok"] is True
    assert result["chip"] == "bl602"
    assert "list_ports" in result["guide"]
    assert "fk_log" in result["guide"]
    assert "921600" in result["guide"]
    assert "flash(" in result["guide"]
    print("  test_flash_guide_tool ✅")


# ── Runner ──────────────────────────────────────────────────────────────

def run_all() -> None:
    """Run all tests in order, print summary."""
    global _FAILURES
    _FAILURES = []

    tests = [
        ("Server Import", test_server_import),
        ("Server has main()", test_server_has_main),
        ("JSON-RPC Initialize", test_jsonrpc_initialize),
        ("tools/list (30 tools)", test_tools_list),
        ("Uninitialized Rejected", test_uninitialized_rejected),
        ("Auth Middleware (no HW)", test_auth_middleware_no_hardware),
        ("Garbage Input", test_garbage_input),
        ("Unknown Tool", test_unknown_tool),
        ("Resources list/read", test_resources_list_and_read),
        ("Resource templates", test_resource_templates_list),
        ("Prompts list/get", test_prompts_list_and_get),
        ("Dynamic Resources (offline)", test_dynamic_resources_offline_safe),
        ("flash_guide tool", test_flash_guide_tool),
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
