"""Unit tests for flashkey_mcp.guide — resources/prompts content (no hardware)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flashkey_mcp import guide  # noqa: E402
from flashkey_mcp.errors import E  # noqa: E402


def _error_codes() -> set[str]:
    """Collect stable error-code constants from errors.E."""
    return {
        value
        for key, value in vars(E).items()
        if key.isupper() and isinstance(value, str)
    }


def test_error_guide_covers_all_error_codes() -> None:
    """ERROR_GUIDE must cover every errors.E constant with a non-empty hint."""
    assert set(guide.ERROR_GUIDE) == _error_codes()
    for code, info in guide.ERROR_GUIDE.items():
        assert info.get("meaning"), f"{code}: missing meaning"
        assert info.get("hint"), f"{code}: missing hint"
        assert isinstance(info.get("retryable"), bool), f"{code}: bad retryable"
        assert "recovery_tool" in info, f"{code}: missing recovery_tool"


def test_flash_firmware_prompt_bl602() -> None:
    """flash-firmware for Ai-WB2 (default break) must include serial-break flow."""
    messages = guide._prompt_flash_firmware(
        chip="ai-wb2",
        firmware_path="/tmp/fw.bin",
    )
    assert len(messages) == 2
    assistant = messages[1].content.text
    assert "list_ports" in assistant
    assert "fk_log" in assistant
    assert "921600" in assistant
    assert 'chip="ai-wb2"' in assistant
    assert "Ai-WB2" in assistant
    assert "flash" in assistant
    assert "log_open" in assistant
    assert "log_close" in assistant
    assert "flashkey://log" in assistant
    assert "RST 复位脉冲" in assistant
    assert "不依赖解析提示文本" in assistant
    assert "boot2" in assistant
    assert "rst_pulse" in assistant


def test_flash_firmware_prompt_bl602_isp() -> None:
    """Ai-WB2 mode=isp must recommend make eflash and boot2/erase guidance."""
    messages = guide._prompt_flash_firmware(
        chip="ai-wb2",
        firmware_path="/tmp/fw.bin",
        mode="isp",
        flash_dir="/opt/wb2/app",
    )
    assistant = messages[1].content.text
    assert 'chip="ai-wb2"' in assistant
    assert "mode=\"isp\"" in assistant
    assert "make eflash" in assistant
    assert "boot2" in assistant
    assert "erase_flash" in assistant
    assert "BOOT" in assistant
    assert "RST" in assistant
    assert "921600" in assistant


def test_flash_firmware_prompt_ai_m62_baud_cap() -> None:
    """Ai-M62 via FlashKey must cap baud at 921600 (FlashKey serial limit)."""
    messages = guide._prompt_flash_firmware(
        chip="ai-m62",
        firmware_path="/tmp/fw.bin",
    )
    assistant = messages[1].content.text
    assert 'chip="ai-m62"' in assistant
    assert "mode=\"isp\"" in assistant
    assert "921600" in assistant
    assert "上限" in assistant or "921600" in assistant
    assert "2000000" in assistant  # 提示 2000000 需外接 USB-UART


def test_normalize_chip_module_names() -> None:
    """Module names map to internal SDK chip names."""
    assert guide.normalize_chip("ai-wb2") == "bl602"
    assert guide.normalize_chip("Ai-M62") == "bl616"
    assert guide.normalize_chip("bl618") == "bl618"


def test_collect_logs_prompt_uses_open_close_resource() -> None:
    """collect-logs must guide open → other work → close → read resource."""
    messages = guide._prompt_collect_logs(port="/dev/ttyUSB0", baud_rate=115200)
    text = "\n".join(m.content.text for m in messages)
    assert "log_open" in text
    assert "log_close" in text
    assert "flashkey://log" in text
    assert "rst_pulse" in text
    assert "分析" in text
    assert "运行" in text
    assert "log_dump" in text
    assert "project" in text
    assert "flashkey://logs" in text


def test_recover_device_prompt_decision_tree() -> None:
    """recover-device must map DEVICE_NOT_FOUND / PORT_WRONG_ROLE correctly."""
    messages = guide._prompt_recover_device(error_code=E.DEVICE_NOT_FOUND)
    text = "\n".join(m.content.text for m in messages)
    assert "recover(reattach=True)" in text

    messages = guide._prompt_recover_device(error_code=E.PORT_WRONG_ROLE)
    text = "\n".join(m.content.text for m in messages)
    assert "list_ports" in text
    assert "fk_log" in text


def test_docs_have_no_unreplaced_placeholders() -> None:
    """Static docs must not contain leftover ``{placeholder}`` braces."""
    for doc in (
        guide.QUICKSTART_DOC,
        guide.FLASH_GUIDE_DOC,
        guide.ERROR_CODES_DOC,
    ):
        assert "{" not in doc and "}" not in doc


def test_instructions_length() -> None:
    """Injected instructions stay within the token budget (~500 chars)."""
    assert len(guide._INSTRUCTIONS) <= 500


def test_instructions_enforce_flash_workflow() -> None:
    """Instructions must force list_ports/fk_log and forbid hardcoded ports."""
    instr = guide._INSTRUCTIONS
    assert "list_ports" in instr
    assert "fk_log" in instr
    assert "硬编码" in instr
    assert "flash" in instr
    assert "flash_guide" in instr
