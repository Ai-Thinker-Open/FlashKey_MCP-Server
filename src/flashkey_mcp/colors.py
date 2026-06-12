#!/usr/bin/env python3
"""Color output utilities for FlashKey FK-01 test scripts."""

import sys

# ANSI escape codes
_COLORS = {
    "GREEN": "\033[92m",
    "RED": "\033[91m",
    "YELLOW": "\033[93m",
    "CYAN": "\033[96m",
    "GRAY": "\033[90m",
    "BOLD": "\033[1m",
    "RESET": "\033[0m",
}

# Only enable colors when outputting to terminal
_USE_COLOR = sys.stdout.isatty()


def _c(name: str, text: str) -> str:
    """Wrap text in ANSI color code if terminal, return plain otherwise."""
    if not _USE_COLOR:
        return text
    return f"{_COLORS[name]}{text}{_COLORS['RESET']}"


def green(text: str) -> str:
    return _c("GREEN", text)


def red(text: str) -> str:
    return _c("RED", text)


def yellow(text: str) -> str:
    return _c("YELLOW", text)


def cyan(text: str) -> str:
    return _c("CYAN", text)


def gray(text: str) -> str:
    return _c("GRAY", text)


def bold(text: str) -> str:
    return _c("BOLD", text)


def bold_yellow(text: str) -> str:
    return _c("YELLOW", _c("BOLD", text.replace("\033[0m", "")))
