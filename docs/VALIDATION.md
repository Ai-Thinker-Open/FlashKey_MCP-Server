[![中文](https://img.shields.io/badge/中文-文档-blue)](VALIDATION.zh.md)

# Validation record

## Scope

This record covers repository baseline `12d5eb380df48c1470783ced0d091935b31f7320` plus the repository-health changes. Validation ran on Windows (`win32`) with Python 3.13.14 and pytest 9.1.1. Dependencies were installed from the project's declared constraints with current pip resolution.

## Automated results

| Check | Result |
| --- | --- |
| Full test suite, run 1 | 122 passed, 0 failed, 1 dependency warning; 22.06 s |
| Full test suite, run 2 | 122 passed, 0 failed, 1 dependency warning; 21.93 s |
| Byte-code compilation | `python -m compileall -q src tests` passed |
| Installed CLI metadata | `flashkey-mcp --version` reported `0.1.4` |
| Isolated package build, run 1 | wheel and sdist built; no project build warnings or errors |
| Isolated package build, run 2 | wheel and sdist built; no project build warnings or errors |

The remaining warning comes from the installed `pydantic-settings`/`mcp` dependency path while resolving an MCP lifespan forward reference. It is not emitted by this repository's source, and neither test run failed.

## Build artifacts

| Run | Artifact | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| 1 | `flashkey_mcp-0.1.4-py3-none-any.whl` | 10,851,940 | `946ba92bf52b6f65283936c917b6953de3f0c3d292874b6437bce466a5bd8ec7` |
| 1 | `flashkey_mcp-0.1.4.tar.gz` | 10,830,509 | `5b43337c90ceafec9d05ba348ffcdf95dccf96a0199e74414bd69f7f7daa0702` |
| 2 | `flashkey_mcp-0.1.4-py3-none-any.whl` | 10,851,940 | `9b85ecd98d2f8584b1398ddbaf42d1c5cb2a6b93955bf61a0af23483b09e33d8` |
| 2 | `flashkey_mcp-0.1.4.tar.gz` | 10,830,558 | `433969f8684eba73c814303f3fce28b87d8bdfff2ebc44613eeff7bf2011a1b4` |

Both builds completed and contained the expected packaged firmware and Linux/Windows OpenOCD assets. Their hashes differ because ZIP/tar archive metadata is timestamp-dependent; the current build is therefore not bit-for-bit reproducible.

## Hardware boundary

No FK-01 or target board was attached. The following remain manual hardware checks and are intentionally excluded from unattended pytest collection where applicable:

- serial discovery, authentication, reconnection and live log capture;
- BOOT/RST and 5 V / 3.3 V / VUSB control;
- Ai-WB2/Ai-M62 firmware flashing and post-flash behavior;
- WCH-LinkE/OpenOCD update of the FK-01 controller;
- firmware update, WSL USB remapping and systemd service behavior.

A successful automated run proves the software-level behavior covered by tests and packaging; it does not certify electrical behavior or a real firmware flash.
