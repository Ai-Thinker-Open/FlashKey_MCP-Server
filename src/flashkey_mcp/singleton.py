"""Single-instance process lock for flashkey-mcp.

Multiple flashkey-mcp server processes (one per MCP client / Inspector) would
otherwise race to open the same FK-01 USB serial port: Linux allows repeated
``open()`` of ``/dev/ttyACM*``, so every process happily opens the device and
their HELLO/CHALLENGE frames interleave, breaking the handshake and leaving
the device LED blinking forever.

This module provides a process-wide file lock (``flock`` on POSIX and
``msvcrt.locking`` on Windows).  The first process to acquire it owns the
device; other processes wait and retry instead of opening the port.  Within one
process the lock is reference-counted so that multiple ``DeviceManager``
instances (e.g. in tests) share it safely.
"""

from __future__ import annotations

import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_LOCK_PATH = os.environ.get(
    "FLASHKEY_LOCK_PATH",
    os.path.join(tempfile.gettempdir(), "flashkey-mcp.lock"),
)

_held = False
_count = 0
_fd = -1


def acquire() -> bool:
    """Try to take the single-instance lock (non-blocking).

    Returns:
        ``True`` if this process owns the device, ``False`` if another
        flashkey-mcp instance holds it.
    """
    global _held, _count, _fd

    if _held:
        _count += 1
        return True

    fd = -1
    try:
        open_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
        fd = os.open(_LOCK_PATH, open_flags, 0o644)
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        logger.info("另一 flashkey-mcp 实例已持有设备锁，等待其释放...")
        return False

    # 记录持有者 PID，便于排查
    try:
        if os.name == "nt":
            os.lseek(fd, 1, os.SEEK_SET)
            os.ftruncate(fd, 1)
        else:
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
            if os.name == "nt":
                import msvcrt

                os.lseek(_fd, 0, os.SEEK_SET)
                msvcrt.locking(_fd, msvcrt.LK_UNLCK, 1)
            else:
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
