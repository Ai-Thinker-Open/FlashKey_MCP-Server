"""Tests for flashkey_mcp.singleton — single-instance device lock."""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import flashkey_mcp.singleton as singleton


_CHILD_CODE = (
    "import flashkey_mcp.singleton as s\n"
    "print('OK' if s.acquire() else 'BUSY')\n"
)


def _lock_path(tmp_path) -> str:
    return str(tmp_path / "flashkey-mcp-test.lock")


def _child_acquire(lock_path: str) -> str:
    """Run a fresh Python process that tries to take the lock."""
    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    env = dict(os.environ)
    env["FLASHKEY_LOCK_PATH"] = lock_path
    env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-c", _CHILD_CODE],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_same_process_refcounted(tmp_path):
    singleton._LOCK_PATH = _lock_path(tmp_path)
    try:
        assert singleton.acquire() is True
        assert singleton.acquire() is True   # 同进程引用计数共享
        singleton.release()
        assert singleton.acquire() is True
        singleton.release()
        singleton.release()                   # 全部释放
        assert singleton.acquire() is True    # 释放后可再次获取
        singleton.release()
    finally:
        while singleton._count > 0:
            singleton.release()


def test_cross_process_exclusive(tmp_path):
    lock_path = _lock_path(tmp_path)
    singleton._LOCK_PATH = lock_path
    try:
        # 本进程持有锁 → 独立的新进程必须拿不到
        assert singleton.acquire() is True
        try:
            assert _child_acquire(lock_path) == "BUSY"
        finally:
            singleton.release()

        # 本进程释放后 → 独立的新进程能拿到
        assert _child_acquire(lock_path) == "OK"
    finally:
        while singleton._count > 0:
            singleton.release()
