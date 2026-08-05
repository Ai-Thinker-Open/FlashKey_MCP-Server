#!/usr/bin/env bash
# 把 FlashKey 仓库编译出的 CH32V203 固件打包进 flashkey-mcp 包。
#
# 用法:
#   scripts/bundle-firmware.sh [FlashKey仓库路径] [版本号]
#
# 默认 FlashKey 仓库路径: /home/seahi/workspace/FlashKey
# 版本号缺省时从 firmware/ch32v203/User/flashkey_cmd.c 的 s_fw_version 自动解析。
set -euo pipefail

FLASHKEY_REPO="${1:-/home/seahi/workspace/FlashKey}"
VERSION="${2:-}"
PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FW_DIR="$FLASHKEY_REPO/firmware/ch32v203"
HEX_SRC="$FW_DIR/flashkey_ch32v203.hex"
DST_DIR="$PKG_DIR/src/flashkey_mcp/firmware"

if [[ ! -d "$FLASHKEY_REPO" ]]; then
    echo "ERROR: FlashKey 仓库不存在: $FLASHKEY_REPO" >&2
    exit 1
fi

echo "==> 构建固件 (make -C $FW_DIR)"
make -C "$FW_DIR" >/dev/null

if [[ ! -f "$HEX_SRC" ]]; then
    echo "ERROR: 编译产物不存在: $HEX_SRC（请先检查工具链）" >&2
    exit 1
fi

if [[ -z "$VERSION" ]]; then
    RAW="$(sed -n 's/.*s_fw_version\[4\].*{ *0x\([0-9A-Fa-f]*\), *0x\([0-9A-Fa-f]*\), *0x\([0-9A-Fa-f]*\).*/\1 \2 \3/p' "$FW_DIR/User/flashkey_cmd.c" | head -1)"
    if [[ -z "$RAW" ]]; then
        echo "ERROR: 无法从 flashkey_cmd.c 解析固件版本，请显式传入版本号" >&2
        exit 1
    fi
    read -r B0 B1 B2 <<< "$RAW"
    VERSION="$(printf '%d.%d.%d' "0x$B0" "0x$B1" "0x$B2")"
fi

SHA256="$(sha256sum "$HEX_SRC" | awk '{print $1}')"
MD5="$(md5sum "$HEX_SRC" | awk '{print $1}')"
mkdir -p "$DST_DIR"
cp "$HEX_SRC" "$DST_DIR/flashkey_ch32v203.hex"

cat > "$DST_DIR/firmware.json" <<EOF
{
  "version": "$VERSION",
  "md5": "$MD5",
  "sha256": "$SHA256",
  "changelog": "v$VERSION: 随 flashkey-mcp 发布的 FK-01 固件（构建来源: $FLASHKEY_REPO）"
}
EOF

echo "[OK] $DST_DIR/flashkey_ch32v203.hex"
echo "[OK] $DST_DIR/firmware.json (version=$VERSION, md5=$MD5, sha256=$SHA256)"
