"""Unit tests for log_open / log_close (no hardware)."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flashkey_mcp import server  # noqa: E402
from flashkey_mcp.errors import FlashkeyError  # noqa: E402


def _ensure_closed() -> None:
    """Make sure no log session / flash lock leaks between tests."""
    if server._log_session["open"]:
        try:
            server._tool_log_close()
        except Exception:
            pass
    if server._flash_lock.locked():
        try:
            server._flash_lock.release()
        except Exception:
            pass


def _mock_serial(lines: list[bytes] | None = None) -> MagicMock:
    ser = MagicMock()
    pending = list(lines or [])

    def readline() -> bytes:
        if pending:
            return pending.pop(0)
        time.sleep(0.01)
        return b""

    ser.readline.side_effect = readline
    return ser


def _set_history_dir() -> Path:
    """Point log history to a temp dir and reset max files."""
    tmp = Path(tempfile.mkdtemp())
    server._LOG_HISTORY_DIR = tmp
    server._LOG_HISTORY_MAX = 10
    return tmp


def test_log_open_close_lifecycle() -> None:
    """open starts background capture; close stops it and finalizes the file."""
    _ensure_closed()
    ser = _mock_serial([b"hello\n", b"world\n"])
    with patch("serial.Serial", return_value=ser):
        opened = server._tool_log_open("/dev/ttyFAKE", 115200)
        try:
            assert opened["ok"] is True
            assert opened["monitoring"] is True
            assert opened["log_resource"] == "flashkey://log"
            assert server._log_session["open"] is True
            time.sleep(0.05)
            closed = server._tool_log_close()
        finally:
            _ensure_closed()

    assert closed["monitoring"] is False
    assert closed["lines"] == 2
    assert closed["bytes"] > 0
    assert server._log_session["open"] is False
    assert server._flash_lock.locked() is False
    ser.close.assert_called()
    content = server._LOG_FILE.read_text(encoding="utf-8", errors="replace")
    assert "hello" in content
    assert "world" in content


def test_log_open_duplicate_raises_busy() -> None:
    """A second open while monitoring must return PORT_BUSY."""
    _ensure_closed()
    ser = _mock_serial()
    with patch("serial.Serial", return_value=ser):
        server._tool_log_open("/dev/ttyFAKE")
        try:
            try:
                server._tool_log_open("/dev/ttyFAKE")
                assert False, "duplicate open should raise"
            except FlashkeyError as exc:
                assert exc.code == "PORT_BUSY"
        finally:
            server._tool_log_close()
    _ensure_closed()


def test_log_close_noop_when_not_open() -> None:
    """close without an active session returns a successful no-op."""
    _ensure_closed()
    result = server._tool_log_close()
    assert result["ok"] is True
    assert result["monitoring"] is False
    assert result["message"] == "未在监控"


def test_log_resource_reads_file() -> None:
    """flashkey://log resource returns current file text or a no-log marker."""
    server._LOG_FILE.write_text("line-a\nline-b\n", encoding="utf-8")
    assert server._resource_log() == "line-a\nline-b\n"
    server._LOG_FILE.write_text("", encoding="utf-8")
    assert server._resource_log() == "(无日志)"


def test_log_dump_writes_file() -> None:
    """log_dump copies the captured log to a destination file."""
    _ensure_closed()
    content = "line-a\nline-b\nline-c\n"
    server._LOG_FILE.write_text(content, encoding="utf-8")
    dest = os.path.join(tempfile.mkdtemp(), "dump.txt")
    result = server._tool_log_dump(dest)
    assert result["success"] is True
    assert result["path"] == os.path.abspath(dest)
    assert result["lines"] == 3
    assert result["bytes"] == len(content.encode("utf-8"))
    assert Path(dest).read_text(encoding="utf-8") == content


def test_log_dump_no_log() -> None:
    """log_dump without any captured log returns success=false gracefully."""
    server._LOG_FILE.write_text("", encoding="utf-8")
    result = server._tool_log_dump()
    assert result["success"] is False
    assert result["bytes"] == 0


def test_sanitize_project() -> None:
    """Project names become safe directory names (Chinese allowed)."""
    assert server._sanitize_project("") == "default"
    assert server._sanitize_project("hello world") == "hello_world"
    assert server._sanitize_project("../etc/passwd") == "etc_passwd"
    assert server._sanitize_project("我的项目") == "我的项目"


def test_log_open_close_archives_history() -> None:
    """log_close archives the capture into <history>/<project>/."""
    _ensure_closed()
    hist = _set_history_dir()
    ser = _mock_serial([b"boot ok\n", b"ready\n"])
    with patch("serial.Serial", return_value=ser):
        opened = server._tool_log_open("/dev/ttyFAKE", 115200, project="demo")
        assert opened["project"] == "demo"
        try:
            time.sleep(0.05)
            closed = server._tool_log_close()
        finally:
            _ensure_closed()
    assert closed["project"] == "demo"
    assert closed["archive"]["archived"] is True
    files = list((hist / "demo").glob("flashkey-log-*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "boot ok\nready\n"


def test_log_history_prune_keeps_newest_10() -> None:
    """Pruning removes the oldest files beyond the per-project cap of 10."""
    hist = _set_history_dir()
    project_dir = hist / "prj"
    project_dir.mkdir(parents=True, exist_ok=True)
    for i in range(12):
        p = project_dir / f"flashkey-log-20260809-{i:02d}.txt"
        p.write_text(f"log-{i}\n", encoding="utf-8")
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    removed = server._prune_log_history("prj")
    assert removed == 2
    remaining = sorted(project_dir.glob("flashkey-log-*.txt"))
    assert len(remaining) == 10
    assert remaining[0].name == "flashkey-log-20260809-02.txt"


def test_log_history_resources_list_and_read() -> None:
    """History templates list files and read content; traversal is blocked."""
    hist = _set_history_dir()
    project_dir = hist / "app"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "flashkey-log-20260809-120000.txt").write_text(
        "hello\n", encoding="utf-8"
    )

    listing = server._resource_log_history("app")
    assert listing["files"]
    assert listing["files"][0]["name"].endswith(".txt")
    assert listing["max_files"] == 10

    text = server._resource_log_file("app", "flashkey-log-20260809-120000.txt")
    assert text == "hello\n"

    blocked = server._resource_log_file("app", "../evil.txt")
    assert "未找到历史日志" in blocked


def test_log_close_recovery_archives_orphan() -> None:
    """log_close without a session archives an orphaned temp log (dedup)."""
    _ensure_closed()
    hist = _set_history_dir()
    server._LOG_FILE.write_text("orphan-line\n", encoding="utf-8")

    first = server._tool_log_close()
    assert first["archive"]["archived"] is True
    files = list((hist / "default").glob("flashkey-log-*.txt"))
    assert len(files) == 1

    # 再次调用不重复归档
    second = server._tool_log_close()
    assert second.get("archive", {}).get("archived") is not True
    assert len(list((hist / "default").glob("flashkey-log-*.txt"))) == 1


def test_shutdown_archive_log() -> None:
    """Server shutdown archives an open log session."""
    _ensure_closed()
    hist = _set_history_dir()
    ser = _mock_serial([b"shutdown line\n"])
    with patch("serial.Serial", return_value=ser):
        server._tool_log_open("/dev/ttyFAKE", 115200, project="svc")
        time.sleep(0.05)
        server._shutdown_archive_log()
    files = list((hist / "svc").glob("flashkey-log-*.txt"))
    assert len(files) == 1
    assert "shutdown line" in files[0].read_text(encoding="utf-8")
    assert server._log_session["open"] is False
    assert server._flash_lock.locked() is False
