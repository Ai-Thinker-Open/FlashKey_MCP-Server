# AGENTS.md

本仓库是 FlashKey FK-01 的 MCP 服务器插件（flashkey-mcp）。当用户要求**烧录 Ai-WB2 / Ai-M62 固件、采集日志、排查设备**时，所有支持 MCP 的客户端（Codex / Claude Code / Cursor / MiMo / Cline 等）都应遵循以下流程。

## 烧录固件（Ai-WB2 / Ai-M62）

1. 先调用 `list_ports()`，选择 `role=fk_log`（WCH-LinkE VCP）的端口。
   **禁止**硬编码或猜测端口名（`/dev/ttyACM0` / `COM3` 会随系统与插拔顺序变化），
   **禁止**使用 `role=fk_control`（FK-01 主控口，MCP 内部专用）。
2. 调用 `status()` 确认 `authed=true`；未认证先完成密钥认证。
3. 烧录前先调用 `flash_guide(chip)` 获取标准流程，然后调用
   `flash(firmware_path=<固件绝对路径>, chip=<ai-wb2|ai-m62>, flash_port=<fk_log 端口>, baud_rate=921600, ...)`。
   - Ai-WB2：默认 **break**（串口打断，`make flash`，只烧 App）；固件不支持串口打断或执行过
     `make erase_flash` 时，用 `mode="isp"`（`make eflash`，全量含 boot2）。
   - Ai-M62：**isp** 模式；FlashKey 自带串口最高仅支持 **921600**（2000000 需外接 USB-UART）。
4. 烧录后必须验证：`log_open(port=<fk_log 端口>, baud_rate=115200, project=<项目名>)`
   → 继续其他操作 → `log_close()` → 读取 `flashkey://log`，**自行分析日志判定启动是否正常**
   （有异常先排查，不要只转述日志原文）；AT 模组可发送 `AT+GMR` 确认版本。

## 采集日志

1. `list_ports()` 选 `role=fk_log` → `status()` 确认认证 → `log_open(port, baud_rate, project)`。
2. `log_open` 立即返回，**不要持续监控串口**；继续执行其他工具（如 `rst_pulse`、`boot_set`）。
3. 操作完成后 `log_close()` 关闭并释放串口。
4. 读取 `flashkey://log` 并自行分析；历史日志用 `flashkey://logs/{project}` 列出、
   `flashkey://logs/{project}/{file}` 读取；需要转存到指定位置用 `log_dump(dest_path=...)`。

## 出错时

- 先读 `flashkey://docs/error-codes`，按 hint / recovery_tool 恢复。
- 设备掉线（`DEVICE_NOT_FOUND` / `HANDSHAKE_FAILED`）先调 `recover(reattach=True)`。
- 端口选错（`PORT_WRONG_ROLE`）用 `list_ports()` 重新按 `role=fk_log` 选择。
