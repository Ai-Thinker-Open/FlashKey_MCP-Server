"""Unit tests for DeviceManager idle-release logic (no hardware needed).

Covers:
- mark_active() resets the idle timer
- idle timeout releases the port and moves to IDLE
- release is suppressed while keepalive is paused (flash/log in progress)
- release is disabled when FLASHKEY_IDLE_TIMEOUT=0
- require_authed() wakes the monitor from IDLE and waits for re-handshake
- status reports idle=True while the port is released
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flashkey_mcp.device_manager as dm_mod
from flashkey_mcp.device_manager import DeviceManager, DeviceState

_FAILURES: list[str] = []


def _fail(msg: str) -> None:
    _FAILURES.append(msg)


class FakeFK:
    """Minimal stand-in for FlashKey with a close() we can observe."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _authed_dm() -> tuple[DeviceManager, FakeFK]:
    dm = DeviceManager()
    fk = FakeFK()
    with dm._lock:
        dm._fk = fk
        dm._state = DeviceState.AUTHED
        dm._last_activity = time.monotonic() - 999
    return dm, fk


def test_mark_active_resets_timer():
    dm = DeviceManager()
    dm._last_activity = time.monotonic() - 999
    dm.mark_active()
    if time.monotonic() - dm._last_activity > 1:
        _fail("mark_active did not reset _last_activity")


def test_idle_release_transitions_to_idle_and_closes():
    dm, fk = _authed_dm()
    if not dm._check_idle_release():
        _fail("expected idle release to trigger")
    if dm._state is not DeviceState.IDLE:
        _fail(f"expected IDLE, got {dm._state.name}")
    if not fk.closed:
        _fail("FK-01 was not closed")
    if dm.fk is not None:
        _fail("_fk should be None after idle release")


def test_idle_release_suppressed_while_paused():
    dm, fk = _authed_dm()
    dm._pause_keepalive = True
    try:
        if dm._check_idle_release():
            _fail("must not release while keepalive paused (flash in progress)")
        if dm._state is not DeviceState.AUTHED:
            _fail("state changed while paused")
        if fk.closed:
            _fail("port closed while paused")
    finally:
        dm._pause_keepalive = False


def test_idle_release_disabled_when_timeout_zero():
    old = dm_mod._IDLE_TIMEOUT_S
    dm_mod._IDLE_TIMEOUT_S = 0
    try:
        dm, fk = _authed_dm()
        if dm._check_idle_release():
            _fail("idle release must be disabled when timeout is 0")
        if dm._state is not DeviceState.AUTHED:
            _fail("state changed with timeout disabled")
        if fk.closed:
            _fail("port closed with timeout disabled")
    finally:
        dm_mod._IDLE_TIMEOUT_S = old


def test_require_authed_wakes_and_waits():
    dm = DeviceManager()
    woke: list[bool] = []
    dm._wake_monitor = lambda: woke.append(True)  # type: ignore[method-assign]
    with dm._lock:
        dm._state = DeviceState.IDLE
        dm._last_error = ""

    old_timeout = dm_mod._WAKE_TIMEOUT_S
    dm_mod._WAKE_TIMEOUT_S = 0.2
    try:
        try:
            dm.require_authed()
            _fail("expected RuntimeError after wake timeout")
        except RuntimeError as exc:
            if "重新连接超时" not in str(exc):
                _fail(f"unexpected error: {exc}")
    finally:
        dm_mod._WAKE_TIMEOUT_S = old_timeout

    if not woke:
        _fail("_wake_monitor was not called")
    if dm._state is not DeviceState.DISCONNECTED:
        _fail("IDLE should transition to DISCONNECTED on wake")


def test_require_authed_success_marks_active():
    dm = DeviceManager()
    with dm._lock:
        dm._state = DeviceState.AUTHED
        dm._last_activity = time.monotonic() - 999
    dm.require_authed()
    if time.monotonic() - dm._last_activity > 1:
        _fail("require_authed on AUTHED should mark active")


def test_status_reports_idle():
    dm = DeviceManager()
    with dm._lock:
        dm._state = DeviceState.IDLE
    status = dm.get_status()
    if status.get("idle") is not True:
        _fail(f"expected idle=True in status, got {status}")


def run_all() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        print(f"[{fn.__name__}]")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            _fail(f"{fn.__name__} UNHANDLED EXCEPTION: {exc}")
    print("=" * 64)
    total = len(tests)
    passed = total - len(_FAILURES)
    print(f"Results: {passed}/{total} passed")
    if _FAILURES:
        print("FAILURES:")
        for f in _FAILURES:
            print(f"  ❌ {f}")
        sys.exit(1)
    print("✅ All idle-release tests PASSED")


if __name__ == "__main__":
    run_all()
