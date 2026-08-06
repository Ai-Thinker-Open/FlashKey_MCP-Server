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
header "步骤 1/2: 安装 flashkey-mcp"

if command -v flashkey-mcp &>/dev/null; then
    info "flashkey-mcp 已安装: $(flashkey-mcp --version 2>&1 | head -1)"
else
    echo "正在安装 flashkey-mcp..."
    if pip install git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git 2>/dev/null; then
        info "安装成功"
    elif pip install --user git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git 2>/dev/null; then
        info "安装成功 (--user)"
    elif python3 -m pip install git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git 2>/dev/null; then
        info "安装成功 (python3 -m pip)"
    else
        err "安装失败，请手动执行: pip install git+https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server.git"
        exit 1
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

# ─── 步骤 2: 配置 AI 工具 ────────────────────────────────────────────
header "步骤 2/2: 配置 AI 工具"

CONFIGURED=0

# ── Claude Code ──────────────────────────────────────────────────────
configure_claude_code() {
    local config_file="$HOME/.claude/mcp.json"
    local server_name="flashkey"

    # 检查是否已配置
    if [ -f "$config_file" ] && grep -q "\"$server_name\"" "$config_file" 2>/dev/null; then
        info "Claude Code: 已配置 ($config_file)"
        CONFIGURED=$((CONFIGURED + 1))
        return
    fi

    # 确保目录存在
    mkdir -p "$(dirname "$config_file")"

    # 读取已有配置 (如果存在)
    local new_config
    if [ -f "$config_file" ]; then
        new_config=$(python3 -c "
import json, sys
with open('$config_file') as f:
    cfg = json.load(f)
cfg.setdefault('mcpServers', {})['$server_name'] = {'command': 'flashkey-mcp'}
with open('$config_file', 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
print('ok')
" 2>/dev/null)
        if [ "$new_config" = "ok" ]; then
            info "Claude Code: 已添加配置 (~/.claude/mcp.json)"
            CONFIGURED=$((CONFIGURED + 1))
        else
            warn "Claude Code: 配置写入失败，请手动编辑 ~/.claude/mcp.json"
        fi
    else
        cat > "$config_file" << 'MCPEOF'
{
  "mcpServers": {
    "flashkey": {
      "command": "flashkey-mcp"
    }
  }
}
MCPEOF
        info "Claude Code: 已创建配置 (~/.claude/mcp.json)"
        CONFIGURED=$((CONFIGURED + 1))
    fi

    # 同时添加 MCP 工具免授权权限
    local perm_file="$HOME/.claude/settings.json"
    if [ -f "$perm_file" ]; then
        python3 -c "
import json
with open('$perm_file') as f:
    cfg = json.load(f)
perms = cfg.setdefault('permissions', {}).setdefault('allow', [])
if 'mcp__flashkey__*' not in perms:
    perms.append('mcp__flashkey__*')
with open('$perm_file', 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
" 2>/dev/null && info "Claude Code: MCP 工具已免授权"
    fi
}

# ── Claude Desktop ───────────────────────────────────────────────────
configure_claude_desktop() {
    local config_file
    case "$(uname -s)" in
        Darwin) config_file="$HOME/Library/Application Support/Claude/claude_desktop_config.json" ;;
        Linux)  return ;;  # Claude Desktop 不常见于 Linux
        *)      return ;;
    esac

    if [ ! -d "$(dirname "$config_file")" ]; then
        return  # Claude Desktop 未安装
    fi

    if [ -f "$config_file" ] && grep -q '"flashkey"' "$config_file" 2>/dev/null; then
        info "Claude Desktop: 已配置"
        CONFIGURED=$((CONFIGURED + 1))
        return
    fi

    mkdir -p "$(dirname "$config_file")"
    if [ -f "$config_file" ]; then
        python3 -c "
import json, sys
with open('$config_file') as f:
    cfg = json.load(f)
cfg.setdefault('mcpServers', {})['flashkey'] = {'command': 'flashkey-mcp'}
with open('$config_file', 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
" 2>/dev/null && info "Claude Desktop: 已添加配置" && CONFIGURED=$((CONFIGURED + 1))
    else
        printf '{\n  "mcpServers": {\n    "flashkey": {\n      "command": "flashkey-mcp"\n    }\n  }\n}\n' > "$config_file"
        info "Claude Desktop: 已创建配置"
        CONFIGURED=$((CONFIGURED + 1))
    fi
}

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
cfg.setdefault('mcpServers', {})['flashkey'] = {'command': 'flashkey-mcp'}
with open('$config_file', 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
" 2>/dev/null && info "Cline: 已添加配置" && CONFIGURED=$((CONFIGURED + 1))
    else
        printf '{\n  "mcpServers": {\n    "flashkey": {\n      "command": "flashkey-mcp"\n    }\n  }\n}\n' > "$config_file"
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
        echo "    command: flashkey-mcp" >> "$config_file"
        echo "    args: []" >> "$config_file"
        echo "    enabled: true" >> "$config_file"
    else
        cat > "$config_file" << 'YAMLEOF'
mcp_servers:
  flashkey:
    command: flashkey-mcp
    args: []
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
        mimo mcp add flashkey-mcp --command flashkey-mcp 2>/dev/null && {
            info "MiMo Code: 已添加 MCP"
            CONFIGURED=$((CONFIGURED + 1))
        }
    fi
}

# ── 检测并配置 ────────────────────────────────────────────────────────
configure_claude_code
configure_claude_desktop
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
    echo -e "  2. 插入 FlashKey FK-01"
    echo -e "  3. 在 AI 工具中调用 ${BOLD}flashkey_status()${NC} 确认连接"
    echo ""
    echo -e "  如需烧录知识：在 AI 对话中说 ${BOLD}\"加载 flashkey-mcp skill\"${NC}"
else
    warn "未检测到已知 AI 工具，无法自动配置。"
    echo ""
    echo "  请参考 README.md 手动配置 MCP："
    echo "  https://github.com/Ai-Thinker-Open/FlashKey_MCP-Server#readme"
fi
