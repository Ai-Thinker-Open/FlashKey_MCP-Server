"""Unit tests for the unified ISP flash mode (break removed, no mode param).

Verifies:
- _resolve_flash_tool: BL602 → make eflash; BL616/BL618 → make flash
- _tool_flash ISP flow: BOOT↑ + RST pulse → flash tool → recovery (RST + BOOT↓)
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Path setup ──────────────────────────────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_DIR, "src")
sys.path.insert(0, SRC_DIR)


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_mock_fk():
    """Build a mock FlashKey object whose commands track calls."""
    fk = MagicMock()
    fk.commands.rst_pulse = MagicMock()
    fk.commands.boot_set = MagicMock()
    fk.commands.boot_get = MagicMock(return_value=True)
    fk.commands.rst_get = MagicMock(return_value=True)
    return fk


# ── Test 1: BL602 resolves make eflash (default ISP) ────────────────────

def test_bl602_resolves_make_eflash():
    """BL602 must resolve to `make eflash` by default (ISP)."""
    from flashkey_mcp.server import _resolve_flash_tool

    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "Makefile").write_text(
            "flash:\n\techo flash\neflash:\n\techo eflash\n"
        )
        with patch(
            "flashkey_mcp.server.subprocess.run",
            return_value=MagicMock(returncode=0),
        ):
            cmd = _resolve_flash_tool(
                "bl602",
                "",
                tmpdir,
                "/dev/ttyUSB0",
                921600,
                Path("/tmp/fw.bin"),
            )

    assert cmd[:4] == ["make", "-C", tmpdir, "eflash"]
    assert "p=/dev/ttyUSB0" in cmd
    assert "b=921600" in cmd
    print("  test_bl602_resolves_make_eflash ✅")


# ── Test 2: BL616 resolves make flash ───────────────────────────────────

def test_bl616_resolves_make_flash():
    """BL616/BL618 must resolve to `make flash CHIP=...` (ISP)."""
    from flashkey_mcp.server import _resolve_flash_tool

    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "Makefile").write_text(
            "flash:\n\techo flash\neflash:\n\techo eflash\n"
        )
        with patch(
            "flashkey_mcp.server.subprocess.run",
            return_value=MagicMock(returncode=0),
        ):
            cmd = _resolve_flash_tool(
                "bl616",
                "",
                tmpdir,
                "COM9",
                921600,
                Path("/tmp/fw.bin"),
            )

    assert cmd[:4] == ["make", "-C", tmpdir, "flash"]
    assert "CHIP=bl616" in cmd
    assert "COMX=COM9" in cmd
    assert "BAUDRATE=921600" in cmd
    print("  test_bl616_resolves_make_flash ✅")


# ── Test 3: ISP flow — enter bootloader, run tool, recover ──────────────

def _write_flash_tool_script(content: str) -> str:
    """Write a cross-platform Python flash-tool double and return its path."""
    fd, path = tempfile.mkstemp(suffix=".py", prefix="flash_tool_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))
    return path


def test_tool_flash_isp_flow():
    """ISP flow: BOOT↑ + RST pulse → flash tool → recovery (RST + BOOT↓)."""
    from flashkey_mcp.server import _tool_flash

    fk = _make_mock_fk()
    dm = MagicMock()
    dm.fk = fk

    # Cross-platform flash-tool double: exits 0 immediately (Python, no bash)
    script = _write_flash_tool_script("""\
import sys
sys.exit(0)
""")
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".bin", delete=False) as fw:
            fw.write("fake firmware")
            fw_path = fw.name
        try:
            with patch("flashkey_mcp.server._require_fk", return_value=(dm, fk)), \
                 patch("flashkey_mcp.server._resolve_flash_tool", return_value=[sys.executable, script]), \
                 patch("flashkey_mcp.server._validate_flash_port"), \
                 patch("flashkey_mcp.server._validate_baud_for_port"):
                result = asyncio.run(_tool_flash(
                    firmware_path=fw_path,
                    flash_port="/dev/ttyUSB0",
                    chip="ai-wb2",
                ))
        finally:
            os.unlink(fw_path)
    finally:
        os.unlink(script)

    assert result["success"] is True, f"Expected success, got {result}"
    assert result["mode"] == "isp", f"Expected mode=isp, got {result}"
    assert result["chip"] == "bl602", f"Expected chip=bl602, got {result}"

    # Enter bootloader: BOOT↑ then RST pulse
    fk.commands.boot_set.assert_any_call(True)
    # Recovery: RST pulse again + BOOT↓
    fk.commands.boot_set.assert_any_call(False)
    assert fk.commands.rst_pulse.call_count == 2, \
        f"Expected 2 RST pulses (entry + recovery), got {fk.commands.rst_pulse.call_count}"
    dm.pause_keepalive.assert_called_once()
    dm.resume_keepalive.assert_called_once()
    print("  test_tool_flash_isp_flow ✅")


# ── Runner ──────────────────────────────────────────────────────────────

def run_all():
    tests = [
        ("BL602 resolves make eflash", test_bl602_resolves_make_eflash),
        ("BL616 resolves make flash", test_bl616_resolves_make_flash),
        ("ISP flow", test_tool_flash_isp_flow),
    ]

    failures = []
    print("=" * 64)
    print("FlashKey MCP — Unified ISP Flash Mode Tests")
    print("=" * 64)
    print()

    for name, fn in tests:
        print(f"[{name}]")
        try:
            fn()
        except Exception as exc:
            failures.append((name, str(exc)))
            print(f"  ❌ FAILED: {exc}")
        print()

    print("=" * 64)
    total = len(tests)
    passed = total - len(failures)
    print(f"Results: {passed}/{total} passed")
    if failures:
        print("FAILURES:")
        for name, msg in failures:
            print(f"  ❌ {name}: {msg}")
        print(f"\n❌ {len(failures)} test(s) FAILED")
        sys.exit(1)
    else:
        print("✅ All tests PASSED")
        sys.exit(0)


if __name__ == "__main__":
    run_all()
