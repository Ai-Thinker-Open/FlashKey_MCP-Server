"""FlashKey FK-01 event recording — manual button-operation notifications.

The DeviceManager receives device→host event frames (e.g. manual PB8/PB9
button operations on v0.1.1 hardware) and records them here with a
wall-clock timestamp.  Records are kept in memory for MCP queries, appended
to a JSONL file under ``~/.flashkey/events.jsonl`` for persistence, and
optionally POSTed to an external server if ``FLASHKEY_EVENT_SERVER_URL`` is
configured.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_KEPT = 100
SERVER_URL_ENV = "FLASHKEY_EVENT_SERVER_URL"
SERVER_TOKEN_ENV = "FLASHKEY_EVENT_TOKEN"
DEFAULT_LOCAL_LOG = Path.home() / ".flashkey" / "events.jsonl"

# 与固件 flashkey_cmd.h 的 FK_EVT_* 定义保持一致
_BTN_NAMES = {0x01: "PB8", 0x02: "PB9"}
_BTN_ROLES = {0x01: "BOOT", 0x02: "RST"}
_ACTIONS = {0x01: "pressed", 0x02: "released"}
_ACTIONS_CN = {0x01: "按下", 0x02: "松开"}


class EventRecorder:
    """Records FlashKey device events with wall-clock timestamps."""

    def __init__(
        self,
        local_log: Path | None = None,
        server_url: str | None = None,
        server_token: str | None = None,
    ) -> None:
        self._records: deque[dict] = deque(maxlen=MAX_KEPT)
        self._local_log = local_log or DEFAULT_LOCAL_LOG
        self._server_url = (
            server_url if server_url is not None else os.environ.get(SERVER_URL_ENV, "")
        )
        self._server_token = (
            server_token
            if server_token is not None
            else os.environ.get(SERVER_TOKEN_ENV, "")
        )

    def record_button_event(self, data: bytes) -> None:
        """Record one button-event frame ``[btn_id, action, tick_lo..tick_hi]``."""
        if len(data) < 2:
            logger.warning("Dropped malformed button event frame: %s", data.hex())
            return

        btn_id, action = data[0], data[1]
        uptime_ticks = int.from_bytes(data[2:6], "little") if len(data) >= 6 else None

        record: dict = {
            "event": "BUTTON_EVENT",
            "button": _BTN_NAMES.get(btn_id, f"0x{btn_id:02X}"),
            "role": _BTN_ROLES.get(btn_id, ""),
            "action": _ACTIONS.get(action, f"0x{action:02X}"),
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        if uptime_ticks is not None:
            record["uptime_ticks"] = uptime_ticks

        self._records.append(record)
        self._append_local(record)
        logger.info(
            "BUTTON_EVENT 用户操作了 %s 按键 (%s, %s), tick=%s, timestamp=%s",
            record["button"],
            record["role"],
            _ACTIONS_CN.get(action, record["action"]),
            uptime_ticks,
            record["timestamp"],
        )
        self._push_server(record)

    def recent(self, limit: int = 20) -> list[dict]:
        """Return the most recent recorded events, newest first."""
        items = list(self._records)
        items.reverse()
        return items[:limit]

    def count(self) -> int:
        """Number of events currently held in memory."""
        return len(self._records)

    # ── internals ────────────────────────────────────────────────────────

    def _append_local(self, record: dict) -> None:
        """Append one JSON line to the local event log (always)."""
        try:
            self._local_log.parent.mkdir(parents=True, exist_ok=True)
            with self._local_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Local event log write failed: %s", exc)

    def _push_server(self, record: dict) -> None:
        """POST the event to an optional external server."""
        if not self._server_url:
            return
        try:
            import urllib.request

            req = urllib.request.Request(
                self._server_url,
                data=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            if self._server_token:
                req.add_header("Authorization", f"Bearer {self._server_token}")
            with urllib.request.urlopen(req, timeout=3) as resp:
                logger.info(
                    "Event pushed to %s (HTTP %d)", self._server_url, resp.status
                )
        except Exception as exc:
            logger.warning("Push event to %s failed: %s", self._server_url, exc)
