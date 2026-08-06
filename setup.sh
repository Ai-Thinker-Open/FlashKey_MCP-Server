#!/usr/bin/env bash
# FlashKey MCP — 通用安装配置脚本
# 自动检测 AI 工具并写入对应的 MCP 配置，不依赖任何特定工具/模型。
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
BOLD='\033[1m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*"; }
header(){ echo -e "\n${BOLD}═══ $* ═══${NC}"; }

# ─── 步骤 1: 安装 flashkey-mcp ────────────────────────────────────────
header "步骤 1/3: 安装 flashkey-mcp"

# 安装源可用环境变量覆盖（国内可换成 Gitee 镜像或 PyPI 包名）：
#   FLASHKEY_INSTALL_URL="flashkey-mcp[sse] @ git+https://gitee.com/Ai-Thinker-Open/FlashKey_MCP-Server.git"
INSTALL_URL="${FLASHKEY_INSTALL_URL:-flashkey-mcp[sse] @ git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git}"

# ── 预检: Python >= 3.10 ──────────────────────────────────────────────
PYTHON_BIN=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PYTHON_BIN="$cand"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    err "未找到 Python >= 3.10，请先安装后重试："
    err "  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
    err "  macOS:         brew install python@3.12"
    err "  Windows:       winget install Python.Python.3.12"
    exit 1
fi
info "Python: $($PYTHON_BIN --version 2>&1 | head -1)"

# ── PEP 668 预检（Ubuntu 23.04+ 等系统 Python 默认拒绝全局 pip）────────
if "$PYTHON_BIN" -c 'import sysconfig, os; sys.exit(0 if os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")) else 1)' 2>/dev/null; then
    warn "系统 Python 受 PEP 668 (externally-managed) 保护，pip 全局安装可能被拒绝；脚本会尝试 venv/uv 兜底"
fi

if command -v flashkey-mcp &>/dev/null; then
    info "flashkey-mcp 已安装: $(flashkey-mcp --version 2>&1 | head -1)"
else
    echo "正在安装 flashkey-mcp..."
    if "$PYTHON_BIN" -m pip install "$INSTALL_URL" 2>/dev/null; then
        info "安装成功"
    elif "$PYTHON_BIN" -m pip install --user "$INSTALL_URL" 2>/dev/null; then
        info "安装成功 (--user)"
    elif command -v uv >/dev/null 2>&1 && uv tool install --reinstall "$INSTALL_URL" 2>/dev/null; then
        info "安装成功 (uv tool)"
    else
        # 最后兜底: 专用 venv
        VENV_DIR="${FLASHKEY_VENV_DIR:-$HOME/.local/share/flashkey-mcp-venv}"
        if "$PYTHON_BIN" -m venv "$VENV_DIR" 2>/dev/null \
            && "$VENV_DIR/bin/python" -m pip install "$INSTALL_URL" 2>/dev/null \
            && [ -x "$VENV_DIR/bin/flashkey-mcp" ]; then
            mkdir -p "$HOME/.local/bin"
            ln -sf "$VENV_DIR/bin/flashkey-mcp" "$HOME/.local/bin/flashkey-mcp"
            info "安装成功 (venv: $VENV_DIR)"
        else
            err "安装失败。请手动执行: $PYTHON_BIN -m pip install \"$INSTALL_URL\""
            err "提示: 若系统 Python 受 PEP 668 保护，请改用 uv tool install \"$INSTALL_URL\"，或先创建 venv"
            exit 1
        fi
    fi

    # 确保 flashkey-mcp 在 PATH 中
    if ! command -v flashkey-mcp &>/dev/null; then
        BIN_PATH=$(find / -name flashkey-mcp -type f 2>/dev/null | head -1)
        if [ -n "$BIN_PATH" ]; then
            mkdir -p ~/.local/bin
            ln -sf "$BIN_PATH" ~/.local/bin/flashkey-mcp
            info "已链接到 ~/.local/bin/flashkey-mcp"
        fi
    fi
fi

# ── WSL 提示 ──────────────────────────────────────────────────────────
if [ -n "${WSL_DISTRO_NAME:-}" ] || uname -r 2>/dev/null | grep -qi microsoft; then
    warn "检测到 WSL：FK-01 需先附加进 WSL 才能使用（Windows 端 usbipd 附加 → usbip attach），"
    warn "之后调用 flashkey_list_ports() 确认端口。详见 README FAQ。"
fi

# ─── 步骤 2: 启动 SSE 常驻服务 ───────────────────────────────────────
header "步骤 2/3: 启动 SSE 常驻服务"

SERVICE_OK=0
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    if flashkey-mcp --service install >/dev/null 2>&1; then
        info "常驻服务已安装并启动: http://127.0.0.1:8100/sse"
        SERVICE_OK=1
    else
        warn "systemd 服务安装失败，尝试后台常驻方式..."
    fi
fi

if [ "$SERVICE_OK" -eq 0 ] && command -v flashkey-mcp >/dev/null 2>&1; then
    nohup flashkey-mcp --sse --host 127.0.0.1 --port 8100 >>/tmp/flashkey-mcp-sse.log 2>&1 &
    SSE_PID=$!
    sleep 1
    if kill -0 "$SSE_PID" 2>/dev/null; then
        info "SSE 常驻服务已启动 (PID $SSE_PID): http://127.0.0.1:8100/sse"
        SERVICE_OK=1
    else
        warn "SSE 服务启动失败，请手动运行: flashkey-mcp --sse"
    fi
fi

if [ "$SERVICE_OK" -eq 0 ]; then
    warn "未启动常驻服务。请先运行: flashkey-mcp --sse，再配置下方 AI 工具。"
fi

# ─── 步骤 3: 配置 AI 工具 ────────────────────────────────────────────
header "步骤 3/3: 配置 AI 工具"

CONFIGURED=0

# ── Cline (VS Code) ──────────────────────────────────────────────────
configure_cline() {
    local config_file="$HOME/.cline/mcp.json"

    if [ -f "$config_file" ] && grep -q '"flashkey"' "$config_file" 2>/dev/null; then
        info "Cline: 已配置"
        CONFIGURED=$((CONFIGURED + 1))
        return
    fi

    mkdir -p "$(dirname "$config_file")"
    if [ -f "$config_file" ]; then
        python3 -c "
import json, sys
with open('$config_file') as f:
    cfg = json.load(f)
cfg.setdefault('mcpServers', {})['flashkey'] = {'type': 'sse', 'url': 'http://127.0.0.1:8100/sse'}
with open('$config_file', 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
" 2>/dev/null && info "Cline: 已添加配置" && CONFIGURED=$((CONFIGURED + 1))
    else
        printf '{\n  "mcpServers": {\n    "flashkey": {\n      "type": "sse",\n      "url": "http://127.0.0.1:8100/sse"\n    }\n  }\n}\n' > "$config_file"
        info "Cline: 已创建配置 (~/.cline/mcp.json)"
        CONFIGURED=$((CONFIGURED + 1))
    fi
}

# ── Hermes Agent ─────────────────────────────────────────────────────
configure_hermes() {
    local config_file="$HOME/.hermes/config.yaml"

    if [ -f "$config_file" ] && grep -q 'flashkey' "$config_file" 2>/dev/null; then
        info "Hermes: 已配置"
        CONFIGURED=$((CONFIGURED + 1))
        return
    fi

    mkdir -p "$(dirname "$config_file")"
    if [ -f "$config_file" ]; then
        if ! grep -q 'mcp_servers:' "$config_file" 2>/dev/null; then
            echo "" >> "$config_file"
            echo "mcp_servers:" >> "$config_file"
        fi
        echo "  flashkey:" >> "$config_file"
        echo "    type: sse" >> "$config_file"
        echo "    url: http://127.0.0.1:8100/sse" >> "$config_file"
        echo "    enabled: true" >> "$config_file"
    else
        cat > "$config_file" << 'YAMLEOF'
mcp_servers:
  flashkey:
    type: sse
    url: http://127.0.0.1:8100/sse
    enabled: true
YAMLEOF
    fi
    info "Hermes: 已创建配置 (~/.hermes/config.yaml)"
    CONFIGURED=$((CONFIGURED + 1))
}

# ── MiMo Code ────────────────────────────────────────────────────────
configure_mimo() {
    # MiMo 使用项目级配置，不写全局，只在当前项目目录生效
    local config_file="$(pwd)/mimocode.json"
    if [ -f "$config_file" ] && grep -q 'flashkey-mcp' "$config_file" 2>/dev/null; then
        info "MiMo Code: 已配置 (项目级 mimocode.json)"
        CONFIGURED=$((CONFIGURED + 1))
        return
    fi
    # MiMo 的 mimocode.json 格式特殊，仅在有该工具时处理
    if command -v mimo &>/dev/null; then
        mimo mcp add flashkey-mcp --transport sse --url http://127.0.0.1:8100/sse 2>/dev/null && {
            info "MiMo Code: 已添加 MCP"
            CONFIGURED=$((CONFIGURED + 1))
        }
    fi
}

# ── 检测并配置 ────────────────────────────────────────────────────────
configure_cline
configure_hermes
configure_mimo

# ─── 结果 ──────────────────────────────────────────────────────────────

echo ""
if [ "$CONFIGURED" -gt 0 ]; then
    header "配置完成 ($CONFIGURED 个工具)"
    echo ""
    echo -e "  ${BOLD}下一步:${NC}"
    echo -e "  1. ${BOLD}重启${NC}你的 AI 工具使配置生效"
    echo -e "  2. 确认常驻服务已启动: ${BOLD}http://127.0.0.1:8100/sse${NC}（Linux 可执行 ${BOLD}flashkey-mcp --service status${NC}）"
    echo -e "  3. 插入 FlashKey FK-01"
    echo -e "  4. 在 AI 工具中调用 ${BOLD}flashkey_status()${NC} 确认连接"
    echo ""
    echo -e "  如需烧录知识：在 AI 对话中说 ${BOLD}\"加载 flashkey-mcp skill\"${NC}"
else
    warn "未检测到已知 AI 工具，无法自动配置。"
    echo ""
    echo "  请参考 README.md 手动配置 MCP："
    echo "  https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server#readme"
fi
