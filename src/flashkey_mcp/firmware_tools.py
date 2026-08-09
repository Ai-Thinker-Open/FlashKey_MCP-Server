"""CH32V203 firmware update check and flashing (OpenOCD + WCH-LinkE).

Provides the logic behind two MCP tools:

- ``firmware_check`` — compare the device firmware version against
  the hex bundled with this flashkey-mcp release and the latest release
  published on GitHub.
- ``firmware_flash`` — write the bundled (or a user-provided) hex
  into the FK-01 CH32V203 through the WCH-LinkE SDI interface.

The WCH OpenOCD binary and target configs ship inside the wheel under
``flashkey_mcp/openocd``.  ``FLASHKEY_OPENOCD`` overrides the binary path;
otherwise the platform-appropriate bundled binary is used, then ``PATH``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcp.server.fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)

# ── Update source (GitHub) ───────────────────────────────────────────

GITHUB_REPO = "Ai-Thinker-Open/FlashKey_MCP-Server"
GITEE_REPO = "Ai-Thinker-Open/FlashKey_MCP-Server"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RAW_FIRMWARE_JSON_URL = (
    "https://raw.githubusercontent.com/{repo}/{tag}/src/flashkey_mcp/firmware/firmware.json"
)
GITEE_RELEASES_LATEST_URL = f"https://gitee.com/api/v5/repos/{GITEE_REPO}/releases/latest"
GITEE_RAW_FIRMWARE_JSON_URL = (
    "https://gitee.com/{repo}/raw/{tag}/src/flashkey_mcp/firmware/firmware.json"
)

# ── Update source selection ───────────────────────────────────────────
# ``FLASHKEY_UPDATE_SOURCE=auto`` (default) tries GitHub first, then Gitee;
# ``=github`` / ``=gitee`` pins a single source (useful behind firewalls).
UPDATE_SOURCE_ENV = "FLASHKEY_UPDATE_SOURCE"

# ── Tuning ────────────────────────────────────────────────────────────

OPENOCD_ENV = "FLASHKEY_OPENOCD"
DEFAULT_FLASH_TIMEOUT_S = 90
RE_ENUMERATE_WAIT_S = 30
NETWORK_TIMEOUT_S = 8

# ── Bundled resource locations (resolved via importlib.resources) ─────

FIRMWARE_REL = "firmware/flashkey_ch32v203.hex"
MANIFEST_REL = "firmware/firmware.json"
DEBUG_CFG_REL = "openocd/fk203-debug.cfg"
UNLOCK_CFG_REL = "openocd/fk203-unlock.cfg"
_LINUX_OCD_REL = "openocd/bin/linux-x64/openocd"
_WIN_OCD_REL = "openocd/bin/win-x64/openocd.exe"

# Substrings in OpenOCD output that suggest flash write protection /
# code-protect (which the unlock retry can clear).
_LOCK_HINTS = (
    "protect",
    "lock",
    "rdp",
    "denied",
    "unable to erase",
    "unable to write",
)

OPENOCD_NOT_FOUND_MSG = (
    "未找到 openocd 可执行文件。请安装 WCH OpenOCD v1.6，"
    "或设置环境变量 FLASHKEY_OPENOCD 指向 openocd 路径；"
    "Windows 用户也可直接用 WCH-LinkUtility 手动烧录/解锁。"
)

HW_PREP_MSG = (
    "未检测到 WCH-LinkE 调试器或目标未连接。请按以下步骤准备后重试：\n"
    "1) 把 FlashKey 自带的 WCH-LinkE 通过 USB 接入电脑"
    "（WSL 环境需先 usbip attach 到 WSL）；\n"
    "2) 将 WCH-LinkE 的 SWDIO/SWCLK/GND/3V3 接到 FK-01 CH32V203 的 SWD 接口；\n"
    "3) 确认目标板已上电。\n"
    "接线完成后重新执行本工具；仍失败可先用 list_ports() 确认设备在线。"
)

WCH_LINKUTILITY_HINT = (
    "如果芯片处于读保护/写保护状态且自动解锁重试失败，请在 Windows 主机上"
    "运行 WCH-LinkUtility，连接 WCH-LinkE 后选择 Unlock 解除保护，"
    "再重新执行本工具。"
)


def _resource(rel: str) -> Path:
    """Return a filesystem path for a resource bundled inside the package."""
    from importlib.resources import files

    return Path(str(files("flashkey_mcp").joinpath(rel)))


# ── Version helpers ───────────────────────────────────────────────────

def parse_version(text: str | None) -> tuple[int, ...] | None:
    """Parse ``"0.1.9"`` / ``"v0.1.10"`` into a comparable tuple."""
    if not text:
        return None
    text = str(text).strip().lstrip("vV")
    parts: list[int] = []
    for seg in text.split("."):
        if not seg.isdigit():
            return None
        parts.append(int(seg))
    return tuple(parts) if parts else None


def version_compare(a: str | None, b: str | None) -> int:
    """Compare two version strings; ``None`` sorts below any concrete value."""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None and pb is None:
        return 0
    if pa is None:
        return -1
    if pb is None:
        return 1
    return (pa > pb) - (pa < pb)


# ── Bundled firmware resources ────────────────────────────────────────

def bundled_manifest() -> dict | None:
    """Return the packaged ``firmware.json`` dict, or None if unavailable."""
    try:
        with open(_resource(MANIFEST_REL), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def bundled_hex_path() -> Path:
    return _resource(FIRMWARE_REL)


# ── OpenOCD resolution / execution ────────────────────────────────────

def resolve_openocd() -> Path | None:
    """Pick the OpenOCD binary: env override → bundled → PATH."""
    env = os.environ.get(OPENOCD_ENV)
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_file():
            return candidate
    rel = _WIN_OCD_REL if platform.system() == "Windows" else _LINUX_OCD_REL
    bundled = _resource(rel)
    if bundled.is_file():
        return bundled
    found = shutil.which("openocd")
    return Path(found) if found else None


def _ocd_quote(path: str | Path) -> str:
    """Quote a path for use inside an OpenOCD ``-c`` command string."""
    escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _run_openocd(
    binary: Path,
    cfg: Path,
    commands: list[str],
    timeout: int,
) -> subprocess.CompletedProcess:
    """Run OpenOCD with one ``-c`` per command (avoids path-quoting issues)."""
    cmd = [str(binary), "-f", str(cfg)]
    for one in commands:
        cmd += ["-c", one]
    logger.info("openocd: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def detect_wch_linke() -> tuple[bool, str]:
    """Check the WCH-LinkE is present on USB; returns ``(ok, detail)``.

    This is deliberately a **USB-enumeration-only** check — it must never
    attach OpenOCD to the debugger.  Attaching OpenOCD makes the WCH-LinkE
    and/or the CH32V203 re-enumerate, which drops the usbipd → WSL
    attachment and requires the user to replug.  The OpenOCD connection is
    only made by the actual flash step (which ends with ``reset`` so the
    chip is left running).
    """
    try:
        from flashkey_mcp.transport import list_all_ports

        ports = list_all_ports()
    except Exception as exc:  # serial enumeration can fail on some systems
        return False, f"无法枚举串口: {exc}"
    for port in ports:
        if port.get("role") == "fk_log" or str(port.get("pid", "")).lower() == "0x8010":
            return True, f"检测到 WCH-LinkE（{port.get('port')}，role=fk_log）"
    detail = "；".join(
        f"{p.get('port')}({p.get('vid')}:{p.get('pid')}, {p.get('role')})"
        for p in ports
    )
    return False, f"未检测到 WCH-LinkE（1A86:8010 / role=fk_log）。当前串口: {detail or '无'}"


# ── Flash orchestration ───────────────────────────────────────────────

def _flash_succeeded(output: str) -> bool:
    return "Programming Finished" in output and "Verified OK" in output


def _looks_locked(output: str) -> bool:
    low = output.lower()
    return any(hint in low for hint in _LOCK_HINTS)


def _summary(output: str, limit: int = 800) -> str:
    text = output.strip()
    return text[-limit:] if len(text) > limit else text


def _wait_for_device(
    get_version_fn: Callable[[], str | None] | None,
    wait_s: int = RE_ENUMERATE_WAIT_S,
) -> str | None:
    """Poll until the device re-enumerates; returns its version or None."""
    if get_version_fn is None:
        return None
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            version = get_version_fn()
        except Exception:
            version = None
        if version:
            return version
        time.sleep(1)
    return None


def flash_ch32v203(
    hex_path: str = "",
    *,
    confirm: bool = False,
    force: bool = False,
    dry_run: bool = False,
    timeout: int = DEFAULT_FLASH_TIMEOUT_S,
    get_version_fn: Callable[[], str | None] | None = None,
) -> dict:
    """Flash the FK-01 CH32V203 via OpenOCD/WCH-LinkE (SDI).

    Returns a result dict; raises :class:`ToolError` for validation and
    precondition failures so the MCP layer surfaces a clear message.
    """
    if not confirm:
        raise ToolError(
            "烧录 FK-01 自身固件属于高风险操作，请先确认 WCH-LinkE 已接到 "
            "CH32V203 SWD 接口，并传 confirm=True 后再执行。"
        )
    if timeout <= 0:
        raise ToolError("timeout 必须为正整数。")

    if hex_path:
        hex_file = Path(hex_path).expanduser().resolve()
    else:
        hex_file = bundled_hex_path().resolve()
    if not hex_file.is_file():
        raise ToolError(f"固件文件不存在: {hex_file}")

    manifest = bundled_manifest()
    bundled_default = not hex_path
    if bundled_default and manifest:
        md5 = str(manifest.get("md5") or "").lower()
        sha256 = str(manifest.get("sha256") or "").lower()
        if md5 and hashlib.md5(hex_file.read_bytes()).hexdigest() != md5:
            raise ToolError(
                "包内固件校验失败（MD5 不匹配），请重新安装 flashkey-mcp 或更换固件文件。"
            )
        if sha256 and hashlib.sha256(hex_file.read_bytes()).hexdigest() != sha256:
            raise ToolError(
                "包内固件校验失败（sha256 不匹配），请重新安装 flashkey-mcp 或更换固件文件。"
            )

    before = get_version_fn() if get_version_fn else None
    expected = (manifest or {}).get("version") if bundled_default else None
    if (
        expected
        and before
        and version_compare(str(expected), before) < 0
        and not force
    ):
        raise ToolError(
            f"目标固件版本 {expected} 低于设备当前版本 {before}，"
            "如确需回退请传 force=True。"
        )

    binary = resolve_openocd()
    if binary is None:
        raise ToolError(OPENOCD_NOT_FOUND_MSG)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "before_version": before,
            "after_version": None,
            "unlocked_retried": False,
            "commands": _plan_commands(binary, hex_file),
            "output_summary": "DRY_RUN：未执行实际烧录",
            "duration_s": 0.0,
        }

    ok_probe, probe_detail = detect_wch_linke()
    if not ok_probe:
        raise ToolError(f"{HW_PREP_MSG}\n检测结果: {probe_detail}")

    start = time.monotonic()
    unlocked_retried = False
    try:
        proc = _run_openocd(
            binary,
            _resource(DEBUG_CFG_REL),
            [f"program {_ocd_quote(hex_file)} verify reset exit"],
            timeout,
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode != 0 or not _flash_succeeded(output):
            if _looks_locked(output):
                unlocked_retried = True
                proc = _run_openocd(
                    binary,
                    _resource(UNLOCK_CFG_REL),
                    [
                        "flash erase_address unlock 0x00000000 0x10000",
                        f"flash write_image {_ocd_quote(hex_file)}",
                        f"flash verify_image {_ocd_quote(hex_file)}",
                        "reset",
                        "exit",
                    ],
                    timeout,
                )
                output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        return {
            "ok": False,
            "before_version": before,
            "after_version": None,
            "unlocked_retried": unlocked_retried,
            "output_summary": f"烧录超时（>{timeout}s）",
            "duration_s": round(duration, 1),
            "error": f"烧录超时。{WCH_LINKUTILITY_HINT}",
        }

    duration = time.monotonic() - start
    if proc.returncode != 0 or not _flash_succeeded(output):
        return {
            "ok": False,
            "before_version": before,
            "after_version": None,
            "unlocked_retried": unlocked_retried,
            "output_summary": _summary(output),
            "duration_s": round(duration, 1),
            "error": (
                "烧录失败（未检测到 Programming Finished / Verified OK）。"
                f"{WCH_LINKUTILITY_HINT}\n日志摘要: {_summary(output)}"
            ),
        }

    after = _wait_for_device(get_version_fn)
    return {
        "ok": True,
        "before_version": before,
        "after_version": after,
        "unlocked_retried": unlocked_retried,
        "output_summary": _summary(output),
        "duration_s": round(duration, 1),
    }


def _plan_commands(binary: Path, hex_file: Path) -> list[str]:
    return [
        " ".join(
            [
                str(binary),
                "-f",
                str(_resource(DEBUG_CFG_REL)),
                "-c",
                f"program {_ocd_quote(hex_file)} verify reset exit",
            ]
        ),
        " ".join(
            [
                str(binary),
                "-f",
                str(_resource(UNLOCK_CFG_REL)),
                "-c",
                "flash erase_address unlock 0x00000000 0x10000",
                "-c",
                f"flash write_image {_ocd_quote(hex_file)}",
                "-c",
                f"flash verify_image {_ocd_quote(hex_file)}",
                "-c",
                "reset",
                "-c",
                "exit",
            ]
        ),
    ]


# ── Update check ──────────────────────────────────────────────────────

def fetch_json(url: str, timeout: int = NETWORK_TIMEOUT_S) -> dict | None:
    """GET a JSON endpoint; return None on any network/parse failure."""
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "flashkey-mcp",
                "Accept": "application/vnd.github+json",
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        return data if isinstance(data, dict) else None
    except (HTTPError, URLError, OSError, ValueError, TimeoutError):
        return None


def fetch_latest_release(timeout: int = NETWORK_TIMEOUT_S) -> dict | None:
    """``releases/latest`` for the flashkey-mcp repo (GitHub → Gitee fallback)."""
    for url in _release_latest_urls():
        data = fetch_json(url, timeout=timeout)
        if data:
            return data
    return None


def fetch_manifest_at_tag(tag: str, timeout: int = NETWORK_TIMEOUT_S) -> dict | None:
    """Fetch the ``firmware.json`` bundled at a specific release tag."""
    for url in _manifest_urls(tag):
        data = fetch_json(url, timeout=timeout)
        if data:
            return data
    return None


def _update_sources() -> list[str]:
    """Return the ordered update sources from ``FLASHKEY_UPDATE_SOURCE``."""
    env = os.environ.get(UPDATE_SOURCE_ENV, "").strip().lower()
    if env == "github":
        return ["github"]
    if env == "gitee":
        return ["gitee"]
    return ["github", "gitee"]  # auto: GitHub first, Gitee fallback


def _release_latest_urls() -> list[str]:
    urls: list[str] = []
    for src in _update_sources():
        urls.append(RELEASES_LATEST_URL if src == "github" else GITEE_RELEASES_LATEST_URL)
    return urls


def _manifest_urls(tag: str) -> list[str]:
    urls: list[str] = []
    for src in _update_sources():
        if src == "github":
            urls.append(RAW_FIRMWARE_JSON_URL.format(repo=GITHUB_REPO, tag=tag))
        else:
            urls.append(GITEE_RAW_FIRMWARE_JSON_URL.format(repo=GITEE_REPO, tag=tag))
    return urls


def check_firmware_update(
    device_version: str | None = None,
    timeout: int = NETWORK_TIMEOUT_S,
) -> dict:
    """Compare device / installed-package / latest-release firmware versions."""
    from flashkey_mcp import __version__

    manifest = bundled_manifest()
    bundled_version = (manifest or {}).get("version")

    release = fetch_latest_release(timeout=timeout)
    latest_mcp_version: str | None = None
    latest_hex_version: str | None = None
    changelog: str | None = None
    release_url: str | None = None
    if release:
        tag = release.get("tag_name")
        if tag:
            latest_mcp_version = str(tag).lstrip("v")
        release_url = release.get("html_url")
        body = (release.get("body") or "").strip()
        changelog = body[:1000] or None
        if tag:
            latest_manifest = fetch_manifest_at_tag(str(tag), timeout=timeout)
            if latest_manifest:
                latest_hex_version = latest_manifest.get("version")

    installed = str(__version__)
    package_update_available = bool(
        latest_mcp_version
        and version_compare(latest_mcp_version, installed) > 0
    )
    update_available = bool(
        device_version
        and latest_hex_version
        and version_compare(str(latest_hex_version), device_version) > 0
    )
    return {
        "device_version": device_version,
        "installed_mcp_version": installed,
        "latest_mcp_version": latest_mcp_version,
        "bundled_hex_version": bundled_version,
        "latest_hex_version": latest_hex_version,
        "update_available": update_available,
        "package_update_available": package_update_available,
        "changelog": changelog,
        "release_url": release_url,
    }
