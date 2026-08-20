"""Pytest collection policy.

The S1 handshake scripts require a physical FK-01 on a user-selected serial
port.  They are operator tools, not unattended unit tests, and must never open a
hard-coded device merely because pytest imports a module.
"""

collect_ignore = [
    "test_s1_handshake.py",
    "test_s1_handshake_debug.py",
]
