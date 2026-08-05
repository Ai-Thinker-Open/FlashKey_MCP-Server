# flashkey-mcp 发版流程

每次发布新版本都会**同时携带 FlashKey FK-01 的 CH32V203 固件（hex）**，
`flashkey_firmware_check` / `flashkey_firmware_flash` 依赖这套流程提供"最新固件"。

## 发版前

1. 打包固件：

   ```bash
   bash scripts/bundle-firmware.sh /path/to/FlashKey [版本号]
   ```

   默认路径 `/home/seahi/workspace/FlashKey`；版本号缺省时自动从
   `flashkey_cmd.c` 的 `s_fw_version` 解析（设备固件版本，如 `0.1.1`）。
2. 检查 `src/flashkey_mcp/firmware/firmware.json`：`version` / `sha256` / `changelog`。
3. 更新 `src/flashkey_mcp/__init__.py` 的 `__version__`（**包版本**，与固件版本解耦）。
4. 运行测试：`python -m pytest tests/ -q`。
5. 提交以上改动（含内置 hex 与 OpenOCD 二进制目录）。

## 发布

1. 打 tag：`git tag v<包版本>`（如 `v0.1.0`），然后 `git push origin master --tags`。
2. 在 GitHub 创建 Release：选择刚打的 tag，正文写变更日志（建议包含固件更新内容）。
3. 发布后 `flashkey_firmware_check` 即可读到新版本
   （`releases/latest` + `raw.githubusercontent.com/<tag>/flashkey_mcp/firmware/firmware.json`）。

## 注意

- 固件版本（`firmware.json.version`，设备侧）与包版本（tag，MCP 侧）是两套号，不要混用。
- OpenOCD 二进制随包内置（Linux x64 / Windows x64，来自 wch-openocd v1.6）；
  升级包时如 WCH 有新版 OpenOCD，一并更新 `src/flashkey_mcp/openocd/bin/`。
- 首次发布必须手动创建 Release；在没有 Release 或网络不可达时，
  `flashkey_firmware_check` 的 `latest_*` 字段返回 null，不会报错。
