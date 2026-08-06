"""Single-instance process lock for flashkey-mcp.

Multiple flashkey-mcp server processes (one per MCP client / Inspector) would
otherwise race to open the same FK-01 USB serial port: Linux allows repeated
``open()`` of ``/dev/ttyACM*``, so every process happily opens the device and
their HELLO/CHALLENGE frames interleave, breaking the handshake and leaving
the device LED blinking forever.

This module provides a process-wide (``flock``-based) lock.  The first process
to acquire it owns the device; other processes wait and retry instead of
opening the port.  Within one process the lock is reference-counted so that
multiple ``DeviceManager`` instances (e.g. in tests) share it safely.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_LOCK_PATH = os.environ.get("FLASHKEY_LOCK_PATH", "/tmp/flashkey-mcp.lock")

_held = False
_count = 0
_fd = -1


def acquire() -> bool:
    """Try to take the single-instance lock (non-blocking).

    Returns:
        ``True`` if this process owns the device (or no lock is available on
        this platform), ``False`` if another flashkey-mcp instance holds it.
    """
    global _held, _count, _fd

    if _held:
        _count += 1
        return True

    try:
        import fcntl
    except ImportError:
        # 非 POSIX 平台（Windows）：无进程级锁，保持原行为
        _held = True
        _count = 1
        return True

    try:
        fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            os.close(fd)  # type: ignore[possibly-undefined]
        except OSError:
            pass
        logger.info("另一 flashkey-mcp 实例已持有设备锁，等待其释放...")
        return False

    # 记录持有者 PID，便于排查
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
    except OSError:
        pass

    _fd = fd
    _held = True
    _count = 1
    return True


def release() -> None:
    """Release one reference to the single-instance lock."""
    global _held, _count, _fd

    if not _held:
        return

    _count -= 1
    if _count > 0:
        return

    if _fd >= 0:
        try:
            import fcntl

            fcntl.flock(_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(_fd)
        except OSError:
            pass
        _fd = -1

    _held = False
