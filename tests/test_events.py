"""Tests for flashkey_mcp.events — button-event recording (no hardware)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flashkey_mcp.events import EventRecorder


def test_record_button_event(tmp_path):
    base = Path(tmp_path)
    rec = EventRecorder(local_log=base / "events.jsonl")
    rec.record_button_event(bytes([0x01, 0x01, 0x34, 0x12, 0x00, 0x00]))
    rec.record_button_event(bytes([0x02, 0x02, 0x35, 0x12, 0x00, 0x00]))

    events = rec.recent(10)
    assert len(events) == 2
    assert events[0]["button"] == "PB9"      # newest first
    assert events[0]["role"] == "RST"
    assert events[0]["action"] == "released"
    assert events[1]["button"] == "PB8"
    assert events[1]["action"] == "pressed"
    assert "timestamp" in events[0]
    assert events[0]["uptime_ticks"] == 0x1235
    assert events[1]["uptime_ticks"] == 0x1234

    lines = (base / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "BUTTON_EVENT"


def test_recent_limit(tmp_path):
    base = Path(tmp_path)
    rec = EventRecorder(local_log=base / "events.jsonl")
    for i in range(15):
        rec.record_button_event(bytes([0x01, 0x01, i, 0x00, 0x00, 0x00]))
    assert rec.count() == 15
    assert len(rec.recent(5)) == 5


if __name__ == "__main__":
    import tempfile

    print("Running events.py unit tests...\n")
    with tempfile.TemporaryDirectory() as _td:
        test_record_button_event(_td)
        test_recent_limit(_td)
    print("\nAll events tests PASSED ✅")
