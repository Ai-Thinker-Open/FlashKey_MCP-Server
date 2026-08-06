"""Tests for flashkey_mcp.firmware_tools — version compare, update check, flash."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402
from flashkey_mcp import firmware_tools  # noqa: E402


# ── Version helpers ───────────────────────────────────────────────────

def test_parse_version():
    assert firmware_tools.parse_version("0.1.9") == (0, 1, 9)
    assert firmware_tools.parse_version("v0.1.10") == (0, 1, 10)
    assert firmware_tools.parse_version("1") == (1,)
    assert firmware_tools.parse_version(None) is None
    assert firmware_tools.parse_version("abc") is None


def test_version_compare():
    assert firmware_tools.version_compare("0.1.9", "0.1.10") == -1
    assert firmware_tools.version_compare("0.1.1", "0.1.1") == 0
    assert firmware_tools.version_compare("0.1.2", "0.1.1") == 1
    assert firmware_tools.version_compare(None, "0.1.0") == -1
    assert firmware_tools.version_compare("0.1.0", None) == 1


# ── Bundled resources ─────────────────────────────────────────────────

def test_bundled_manifest_parses():
    manifest = firmware_tools.bundled_manifest()
    assert manifest and manifest.get("version") == "0.1.2"
    assert len(manifest.get("md5", "")) == 32
    assert len(manifest.get("sha256", "")) == 64


def test_bundled_hex_exists():
    assert firmware_tools.bundled_hex_path().is_file()


# ── OpenOCD resolution ────────────────────────────────────────────────

def test_resolve_openocd_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "openocd"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv(firmware_tools.OPENOCD_ENV, str(fake))
    assert firmware_tools.resolve_openocd() == fake


def test_resolve_openocd_missing_env_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv(firmware_tools.OPENOCD_ENV, str(tmp_path / "nope"))
    with mock.patch.object(firmware_tools, "_resource", return_value=Path("/nonexistent")):
        with mock.patch.object(firmware_tools.shutil, "which", return_value=None):
            assert firmware_tools.resolve_openocd() is None


# ── Update check ──────────────────────────────────────────────────────

def test_fetch_latest_release_falls_back_to_gitee(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url, timeout=firmware_tools.NETWORK_TIMEOUT_S):
        calls.append(url)
        if "api.github.com" in url:
            return None
        return {"tag_name": "v0.1.2", "html_url": "https://gitee.com/x/y/releases/v0.1.2"}

    monkeypatch.setattr(firmware_tools, "fetch_json", fake_fetch)
    data = firmware_tools.fetch_latest_release()
    assert data and data["tag_name"] == "v0.1.2"
    assert len(calls) == 2
    assert calls[0] == firmware_tools.RELEASES_LATEST_URL
    assert calls[1] == firmware_tools.GITEE_RELEASES_LATEST_URL


def test_fetch_latest_release_gitee_only(monkeypatch):
    monkeypatch.setenv(firmware_tools.UPDATE_SOURCE_ENV, "gitee")
    calls: list[str] = []

    def fake_fetch(url, timeout=firmware_tools.NETWORK_TIMEOUT_S):
        calls.append(url)
        return {"tag_name": "v0.1.2"} if "gitee.com" in url else None

    monkeypatch.setattr(firmware_tools, "fetch_json", fake_fetch)
    assert firmware_tools.fetch_latest_release()["tag_name"] == "v0.1.2"
    assert len(calls) == 1
    assert calls[0] == firmware_tools.GITEE_RELEASES_LATEST_URL


def test_fetch_manifest_at_tag_falls_back(monkeypatch):
    calls: list[str] = []

    def fake_fetch(url, timeout=firmware_tools.NETWORK_TIMEOUT_S):
        calls.append(url)
        if "raw.githubusercontent.com" in url:
            return None
        return {"version": "0.1.2"}

    monkeypatch.setattr(firmware_tools, "fetch_json", fake_fetch)
    data = firmware_tools.fetch_manifest_at_tag("v0.1.2")
    assert data and data["version"] == "0.1.2"
    assert "gitee.com" in calls[1]


def test_fetch_all_sources_fail(monkeypatch):
    monkeypatch.setattr(
        firmware_tools, "fetch_json", lambda url, timeout=firmware_tools.NETWORK_TIMEOUT_S: None
    )
    assert firmware_tools.fetch_latest_release() is None
    assert firmware_tools.fetch_manifest_at_tag("v0.1.2") is None


def test_check_firmware_update_offline_device(monkeypatch):
    monkeypatch.setattr(firmware_tools, "fetch_latest_release", lambda **kw: None)
    result = firmware_tools.check_firmware_update(device_version=None)
    assert result["device_version"] is None
    assert result["latest_mcp_version"] is None
    assert result["update_available"] is False
    assert result["package_update_available"] is False
    assert result["bundled_hex_version"] == "0.1.2"


def test_check_firmware_update_newer_release(monkeypatch):
    import flashkey_mcp

    monkeypatch.setattr(flashkey_mcp, "__version__", "0.0.1")
    monkeypatch.setattr(
        firmware_tools,
        "fetch_latest_release",
        lambda **kw: {
            "tag_name": "v0.2.0",
            "html_url": (
                "https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server/releases/tag/v0.2.0"
            ),
            "body": "Release notes...",
        },
    )
    monkeypatch.setattr(
        firmware_tools,
        "fetch_manifest_at_tag",
        lambda tag, **kw: {"version": "0.2.0", "sha256": "x" * 64},
    )
    result = firmware_tools.check_firmware_update(device_version="0.1.1")
    assert result["latest_mcp_version"] == "0.2.0"
    assert result["latest_hex_version"] == "0.2.0"
    assert result["package_update_available"] is True
    assert result["update_available"] is True
    assert result["changelog"] == "Release notes..."


def test_check_firmware_update_already_latest(monkeypatch):
    import flashkey_mcp

    monkeypatch.setattr(flashkey_mcp, "__version__", "0.2.0")
    monkeypatch.setattr(
        firmware_tools,
        "fetch_latest_release",
        lambda **kw: {"tag_name": "v0.2.0", "body": ""},
    )
    monkeypatch.setattr(
        firmware_tools,
        "fetch_manifest_at_tag",
        lambda tag, **kw: {"version": "0.2.0"},
    )
    result = firmware_tools.check_firmware_update(device_version="0.2.0")
    assert result["update_available"] is False
    assert result["package_update_available"] is False


# ── Flash validation / orchestration ─────────────────────────────────

def test_flash_requires_confirm(tmp_path):
    hex_file = tmp_path / "x.hex"
    hex_file.write_text("data")
    with pytest.raises(ToolError, match="confirm=True"):
        firmware_tools.flash_ch32v203(str(hex_file))


def test_flash_hex_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(firmware_tools, "bundled_hex_path", lambda: tmp_path / "missing.hex")
    with pytest.raises(ToolError, match="固件文件不存在"):
        firmware_tools.flash_ch32v203("", confirm=True)


def test_flash_sha_mismatch(tmp_path, monkeypatch):
    hex_file = tmp_path / "x.hex"
    hex_file.write_text("data")
    monkeypatch.setattr(firmware_tools, "bundled_hex_path", lambda: hex_file)
    monkeypatch.setattr(
        firmware_tools, "bundled_manifest", lambda: {"version": "0.1.1", "sha256": "0" * 64}
    )
    with pytest.raises(ToolError, match="sha256 不匹配"):
        firmware_tools.flash_ch32v203("", confirm=True)


def test_flash_md5_mismatch(tmp_path, monkeypatch):
    hex_file = tmp_path / "x.hex"
    hex_file.write_text("data")
    monkeypatch.setattr(firmware_tools, "bundled_hex_path", lambda: hex_file)
    monkeypatch.setattr(
        firmware_tools, "bundled_manifest", lambda: {"version": "0.1.1", "md5": "0" * 32}
    )
    with pytest.raises(ToolError, match="MD5 不匹配"):
        firmware_tools.flash_ch32v203("", confirm=True)


def test_flash_downgrade_guard(tmp_path, monkeypatch):
    hex_file = tmp_path / "x.hex"
    hex_file.write_text("data")
    monkeypatch.setattr(firmware_tools, "bundled_hex_path", lambda: hex_file)
    monkeypatch.setattr(firmware_tools, "bundled_manifest", lambda: {"version": "0.1.0"})
    with pytest.raises(ToolError, match="低于设备当前版本"):
        firmware_tools.flash_ch32v203("", confirm=True, get_version_fn=lambda: "0.1.2")


def test_flash_force_allows_downgrade_then_requires_openocd(tmp_path, monkeypatch):
    hex_file = tmp_path / "x.hex"
    hex_file.write_text("data")
    monkeypatch.setattr(firmware_tools, "bundled_hex_path", lambda: hex_file)
    monkeypatch.setattr(firmware_tools, "bundled_manifest", lambda: {"version": "0.1.0"})
    monkeypatch.setattr(firmware_tools, "resolve_openocd", lambda: None)
    with pytest.raises(ToolError, match="未找到 openocd"):
        firmware_tools.flash_ch32v203(
            "", confirm=True, force=True, get_version_fn=lambda: "0.1.2"
        )


def test_flash_success(monkeypatch):
    monkeypatch.setattr(firmware_tools, "resolve_openocd", lambda: Path("/usr/bin/openocd"))
    monkeypatch.setattr(firmware_tools, "detect_wch_linke", lambda **kw: (True, "ok"))
    calls = []

    def fake_run(binary, cfg, commands, timeout):
        calls.append((cfg.name, commands))
        return mock.Mock(
            returncode=0,
            stdout="Info: Programming Finished\nInfo: Verified OK\n",
            stderr="",
        )

    monkeypatch.setattr(firmware_tools, "_run_openocd", fake_run)
    result = firmware_tools.flash_ch32v203("", confirm=True, get_version_fn=lambda: "0.1.1")
    assert result["ok"] is True
    assert result["unlocked_retried"] is False
    assert result["after_version"] == "0.1.1"
    assert calls[0][0] == "fk203-debug.cfg"


def test_flash_unlock_retry(monkeypatch):
    monkeypatch.setattr(firmware_tools, "resolve_openocd", lambda: Path("/usr/bin/openocd"))
    monkeypatch.setattr(firmware_tools, "detect_wch_linke", lambda **kw: (True, "ok"))
    calls = []

    def fake_run(binary, cfg, commands, timeout):
        calls.append((cfg.name, commands))
        if cfg.name == "fk203-debug.cfg":
            return mock.Mock(returncode=1, stdout="Error: flash protect active\n", stderr="")
        return mock.Mock(
            returncode=0,
            stdout="Info: Programming Finished\nInfo: Verified OK\n",
            stderr="",
        )

    monkeypatch.setattr(firmware_tools, "_run_openocd", fake_run)
    result = firmware_tools.flash_ch32v203("", confirm=True, get_version_fn=lambda: "0.1.1")
    assert result["ok"] is True
    assert result["unlocked_retried"] is True
    assert calls[1][0] == "fk203-unlock.cfg"
    assert "erase_address unlock" in calls[1][1][0]


def test_flash_failure_returns_linkutility_hint(monkeypatch):
    monkeypatch.setattr(firmware_tools, "resolve_openocd", lambda: Path("/usr/bin/openocd"))
    monkeypatch.setattr(firmware_tools, "detect_wch_linke", lambda **kw: (True, "ok"))

    def fake_run(binary, cfg, commands, timeout):
        return mock.Mock(returncode=1, stdout="Error: something failed\n", stderr="")

    monkeypatch.setattr(firmware_tools, "_run_openocd", fake_run)
    result = firmware_tools.flash_ch32v203("", confirm=True, get_version_fn=lambda: "0.1.1")
    assert result["ok"] is False
    assert "WCH-LinkUtility" in result["error"]


def test_flash_dry_run(monkeypatch, tmp_path):
    hex_file = tmp_path / "x.hex"
    hex_file.write_text("data")
    monkeypatch.setattr(firmware_tools, "bundled_hex_path", lambda: hex_file)
    monkeypatch.setattr(firmware_tools, "bundled_manifest", lambda: {"version": "0.1.1"})
    monkeypatch.setattr(firmware_tools, "resolve_openocd", lambda: Path("/usr/bin/openocd"))
    monkeypatch.setattr(
        firmware_tools,
        "_run_openocd",
        lambda *a, **k: pytest.fail("dry_run must not execute openocd"),
    )
    result = firmware_tools.flash_ch32v203("", confirm=True, dry_run=True)
    assert result["dry_run"] is True
    assert len(result["commands"]) == 2


def test_detect_wch_linke_present(monkeypatch):
    monkeypatch.setattr(
        "flashkey_mcp.transport.list_all_ports",
        lambda: [
            {
                "port": "/dev/ttyACM1",
                "vid": "0x1A86",
                "pid": "0x8010",
                "role": "fk_log",
            }
        ],
    )
    ok, detail = firmware_tools.detect_wch_linke()
    assert ok is True
    assert "WCH-LinkE" in detail


def test_detect_wch_linke_missing(monkeypatch):
    monkeypatch.setattr("flashkey_mcp.transport.list_all_ports", lambda: [])
    ok, detail = firmware_tools.detect_wch_linke()
    assert ok is False
    assert "未检测到 WCH-LinkE" in detail
