#!/usr/bin/env bash
# 安装/卸载/验证 com.helicloud.preflight LaunchAgent
# 用法：
#   ./install_preflight_launchd.sh install    # 安装并加载（需用户批准）
#   ./install_preflight_launchd.sh uninstall  # 卸载并卸载
#   ./install_preflight_launchd.sh status     # 查看加载状态
#   ./install_preflight_launchd.sh test       # 手动触发一次运行（不依赖定时）

set -euo pipefail

PROJECT_DIR="/Users/anong/Developer/market_for_team"
SCRIPT_DIR="$PROJECT_DIR/omnipost/scripts/launchd"
PLIST_NAME="com.helicloud.preflight.plist"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LAUNCHD_LABEL="com.helicloud.preflight"
RUN_SCRIPT="$PROJECT_DIR/omnipost/scripts/run_daily_preflight.py"
PYTHON_BIN="$PROJECT_DIR/omnipost/.venv/bin/python"

log() { printf '\033[1;32m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
warn() { printf '\033[1;33m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
err() { printf '\033[1;31m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }

validate_paths() {
    if [[ ! -f "$PLIST_SRC" ]]; then
        err "找不到 plist 源文件: $PLIST_SRC"; exit 1
    fi
    if [[ ! -f "$RUN_SCRIPT" ]]; then
        err "找不到运行脚本: $RUN_SCRIPT"; exit 1
    fi
    if [[ ! -x "$PYTHON_BIN" ]]; then
        err "Python 不存在或不可执行: $PYTHON_BIN"; exit 1
    fi
}

install_plist() {
    log "校验 plist 语法..."
    plutil -lint "$PLIST_SRC" >/dev/null || { err "plist 语法错误"; exit 1; }

    log "创建目标目录并复制 plist..."
    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$PLIST_SRC" "$PLIST_DST"
    chmod 644 "$PLIST_DST"
    log "已安装到: $PLIST_DST"

    log "加载 LaunchAgent..."
    launchctl bootout "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" || { err "加载失败，需在 系统设置>隐私与安全性>辅助功能/登录项 中授权"; exit 1; }
    log "加载完成。标签: $LAUNCHD_LABEL"
}

uninstall_plist() {
    log "卸载 LaunchAgent..."
    launchctl bootout "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    log "已卸载。"
}

status_plist() {
    log "LaunchAgent 状态："
    launchctl print "gui/$(id -u)/$LAUNCHD_LABEL" 2>&1 || warn "未加载或标签不存在"
    echo
    log "磁盘上的 plist："
    [[ -f "$PLIST_DST" ]] && cat "$PLIST_DST" || warn "不存在 $PLIST_DST"
    echo
    log "日志目录："
    ls -l "$PROJECT_DIR/logs/preflight/" 2>/dev/null || warn "日志目录不存在"
}

test_run() {
    log "手动触发一次预检（静默模式）..."
    "$PYTHON_BIN" "$RUN_SCRIPT" --quiet
    local rc=$?
    if [[ $rc -eq 0 ]]; then
        log "预检完成：全部正常"
    elif [[ $rc -eq 1 ]]; then
        warn "预检完成：发现账号异常（退出码 1）"
    elif [[ $rc -eq 2 ]]; then
        err "配置错误（退出码 2）"
    elif [[ $rc -eq 3 ]]; then
        err "运行故障或告警结果未知（退出码 3）"
    else
        err "未知退出码: $rc"
    fi
    return $rc
}

config_check() {
    log "检查告警配置..."
    "$PYTHON_BIN" "$RUN_SCRIPT" --config-check --quiet
}

cmd="${1:-status}"
case "$cmd" in
    install) validate_paths; install_plist; status_plist ;;
    uninstall) uninstall_plist ;;
    status) status_plist ;;
    test) validate_paths; test_run ;;
    config-check) config_check ;;
    *) err "未知命令: $cmd"; echo "用法: $0 {install|uninstall|status|test|config-check}"; exit 1 ;;
esac