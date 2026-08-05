"""Unit tests for modules.py — manifest parsing, registry diff, dynamic tools."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flashkey_mcp.modules import (
    ManifestError,
    ModuleRegistry,
    parse_manifest,
    sanitize_tool_name,
)


MANIFEST_A = (
    b'{"module":{"name":"sensor","vendor":"acme","version":"1.2.3"},'
    b'"tools":['
    b'{"name":"gpio","description":"set a pin","parameters":{"type":"object",'
    b'"properties":{"pin":{"type":"integer"},"mode":{"type":"string",'
    b'"enum":["in","out"],"default":"in"}},"required":["pin"]}},'
    b'{"name":"adc read","description":"read adc",'
    b'"parameters":{"type":"object","properties":{}}}'
    b']}'
)

MANIFEST_B = (
    b'{"module":{"name":"sensor","vendor":"acme","version":"1.2.3"},'
    b'"tools":['
    b'{"name":"gpio","description":"set a pin","parameters":{"type":"object",'
    b'"properties":{"pin":{"type":"integer"}},"required":["pin"]}}'
    b']}'
)


class FakeToolManager:
    def __init__(self) -> None:
        self._tools: dict = {}

    def remove_tool(self, name: str) -> None:
        if name not in self._tools:
            raise KeyError(name)
        del self._tools[name]

    def list_tools(self):
        return list(self._tools.values())


class FakeMCP:
    def __init__(self) -> None:
        self._tool_manager = FakeToolManager()
        self.added: list[str] = []
        self.removed: list[str] = []

    def add_tool(self, fn, name=None, description=None, **kwargs):
        self.added.append(name)

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)
        self._tool_manager.remove_tool(name)


def test_sanitize_tool_name():
    assert sanitize_tool_name("gpio") == "mod_gpio"
    assert sanitize_tool_name("adc read") == "mod_adc_read"
    assert sanitize_tool_name("led-pin#1") == "mod_led-pin_1"
    assert sanitize_tool_name("!!!") == "mod_module"
    print("  sanitize_tool_name ✅")


def test_parse_manifest_valid():
    manifest = parse_manifest(MANIFEST_A + b"\xff" * 32)  # padded like I2C read
    assert manifest["module"]["name"] == "sensor"
    assert [t["tool_name"] for t in manifest["tools"]] == ["mod_gpio", "mod_adc_read"]
    assert manifest["tools"][0]["parameters"]["properties"]["mode"]["enum"] == ["in", "out"]
    print("  parse_manifest valid ✅")


def test_parse_manifest_invalid():
    for bad in (b"", b"not json", b'{"foo":1}', b'{"module":{}}', b'{"module":{"name":"x"},"tools":[{"name":""}]}'):
        try:
            parse_manifest(bad)
            assert False, f"Expected ManifestError for {bad!r}"
        except ManifestError:
            pass
    # duplicate sanitized names
    dup = b'{"module":{"name":"x"},"tools":[{"name":"a b"},{"name":"a_b"}]}'
    try:
        parse_manifest(dup)
        assert False, "Expected ManifestError for duplicate names"
    except ManifestError:
        pass
    print("  parse_manifest invalid ✅")


def test_registry_add_diff_remove():
    fake = FakeMCP()
    reg = ModuleRegistry()
    reg.attach(fake)

    assert reg.update(MANIFEST_A) is True
    assert set(fake._tool_manager._tools) == {"mod_gpio", "mod_adc_read"}
    assert fake.removed == []
    assert reg.registered_tools == ["mod_adc_read", "mod_gpio"]

    # no change -> no sync
    assert reg.update(MANIFEST_A) is False

    # changed manifest -> remove stale, keep shared
    assert reg.update(MANIFEST_B) is True
    assert set(fake._tool_manager._tools) == {"mod_gpio"}
    assert "mod_adc_read" in fake.removed

    # module gone -> all removed
    assert reg.clear() is True
    assert fake._tool_manager._tools == {}
    assert "mod_gpio" in fake.removed
    assert reg.update(None) is False  # already clear
    print("  registry add/diff/remove ✅")


def test_registry_invalid_manifest_keeps_previous():
    fake = FakeMCP()
    reg = ModuleRegistry()
    reg.attach(fake)
    reg.update(MANIFEST_A)
    assert reg.update(b"garbage") is False
    # previous manifest and tools retained
    assert set(fake._tool_manager._tools) == {"mod_gpio", "mod_adc_read"}
    assert "解析失败" in reg.info()["last_error"]
    print("  invalid manifest tolerance ✅")


def test_invoke_payload_and_response():
    received: list[bytes] = []

    def handler(payload: bytes) -> dict:
        received.append(payload)
        return {"sent": len(payload), "chunks": [b"reply:", payload], "timed_out": False}

    fake = FakeMCP()
    reg = ModuleRegistry()
    reg.attach(fake)
    reg.set_io_handler(handler)
    reg.update(MANIFEST_A)

    result = reg._invoke("mod_gpio", {"pin": 3, "mode": "out"})
    assert received == [b'{"pin":3,"mode":"out"}'], received
    assert result["response_bytes"] == len(b"reply:") + len(received[0])
    assert result["response_hex"] == (b"reply:" + received[0]).hex()
    assert result["timed_out"] is False
    print("  invoke payload/response ✅")


def test_invoke_oversize_and_single_flight():
    fake = FakeMCP()
    reg = ModuleRegistry()
    reg.attach(fake)
    reg.set_io_handler(lambda p: {"sent": len(p), "chunks": [], "timed_out": False})
    reg.update(MANIFEST_A)

    big_args = {"pin": 1, "mode": "x" * 300}
    try:
        reg._invoke("mod_gpio", big_args)
        assert False, "Expected ToolError for oversize payload"
    except Exception as exc:
        assert "252" in str(exc)

    # single-flight: second call while first holds the lock -> ToolError
    held = threading.Event()
    release = threading.Event()

    def blocking_handler(payload: bytes) -> dict:
        held.set()
        release.wait(2)
        return {"sent": len(payload), "chunks": [], "timed_out": False}

    reg2 = ModuleRegistry()
    reg2.attach(fake)
    reg2.set_io_handler(blocking_handler)
    reg2.update(MANIFEST_A)

    errs: list[str] = []

    def call():
        try:
            reg2._invoke("mod_gpio", {"pin": 1})
        except Exception as exc:
            errs.append(str(exc))

    t1 = threading.Thread(target=call)
    t2 = threading.Thread(target=call)
    t1.start()
    assert held.wait(2), "first call did not start"
    t2.start()
    t2.join(2)
    assert errs, "expected single-flight rejection"
    assert "另一个模块工具调用正在进行" in errs[0]
    release.set()
    t1.join(2)
    print("  oversize + single-flight ✅")


def test_fastmcp_dynamic_tool_end_to_end():
    """Inject a manifest, verify tools/list exposes mod_*, call it, verify
    the payload goes out and 0x63 data comes back as the result."""
    from mcp.server.fastmcp import FastMCP

    sent: list[bytes] = []

    def handler(payload: bytes) -> dict:
        sent.append(payload)
        return {
            "sent": len(payload),
            "chunks": [b"RESP(" + payload + b")"],
            "timed_out": False,
        }

    mcp = FastMCP("test")
    reg = ModuleRegistry()
    reg.attach(mcp)
    reg.set_io_handler(handler)
    reg.update(MANIFEST_A)

    listed = [t.name for t in mcp._tool_manager.list_tools()]
    assert "mod_gpio" in listed and "mod_adc_read" in listed, listed

    # schema from the manifest (not pydantic-generated titles)
    tool = mcp._tool_manager.get_tool("mod_gpio")
    assert tool.parameters["properties"]["pin"]["type"] == "integer"
    assert tool.parameters["properties"]["mode"]["enum"] == ["in", "out"]

    async def call():
        return await mcp._tool_manager.call_tool("mod_gpio", {"pin": 3, "mode": "out"})

    result = asyncio.run(call())
    assert sent == [b'{"pin":3,"mode":"out"}'], sent
    text = "".join(r.text for r in result) if not isinstance(result, dict) else str(result)
    assert "RESP(" in text
    assert '{"pin":3,"mode":"out"}' in text
    print("  FastMCP dynamic tool end-to-end ✅")


def test_device_manager_poll_module_sync():
    """DeviceManager._poll_module drives manifest updates, keeps state on
    timeout, and removes tools when the module disappears."""
    from flashkey_mcp.device_manager import DeviceManager

    fake = FakeMCP()
    reg = ModuleRegistry()
    reg.attach(fake)
    dm = DeviceManager()
    dm._module_registry = reg

    class FakeCmd:
        raw = MANIFEST_A

        def module_get_info(self, read_timeout: float = 2.0):
            return self.raw

    class FakeCmdTimeout:
        def module_get_info(self, read_timeout: float = 2.0):
            raise TimeoutError("no module support")

    class FakeCmdNone:
        def module_get_info(self, read_timeout: float = 2.0):
            return None

    class FakeFK:
        commands = FakeCmd()

    dm._fk = FakeFK()
    dm._poll_module()
    assert set(fake._tool_manager._tools) == {"mod_gpio", "mod_adc_read"}

    # timeout → keep previous state (no false tool removal)
    fk2 = FakeFK()
    fk2.commands = FakeCmdTimeout()
    dm._fk = fk2
    dm._poll_module()
    assert set(fake._tool_manager._tools) == {"mod_gpio", "mod_adc_read"}

    # explicit empty response → module gone, tools removed
    fk3 = FakeFK()
    fk3.commands = FakeCmdNone()
    dm._fk = fk3
    dm._poll_module()
    assert fake._tool_manager._tools == {}
    print("  DeviceManager module poll/sync ✅")


if __name__ == "__main__":
    test_sanitize_tool_name()
    test_parse_manifest_valid()
    test_parse_manifest_invalid()
    test_registry_add_diff_remove()
    test_registry_invalid_manifest_keeps_previous()
    test_invoke_payload_and_response()
    test_invoke_oversize_and_single_flight()
    test_fastmcp_dynamic_tool_end_to_end()
    test_device_manager_poll_module_sync()
    print("\nAll modules tests passed ✅")
