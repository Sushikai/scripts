#!/bin/bash
# 退学 v3 · 每日 8 AM 自动重启 LaunchAgent 管理器
# 模式: 仿 realtime_manager.sh
# 用法: ./auto_restart_manager.sh {install|start|stop|restart|status|uninstall|run-now}

PLIST_SRC="/Users/kaikai/scripts/tuixue_v3/web/com.kaikai.tuixue.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.kaikai.tuixue.daily.plist"
START_SCRIPT="/Users/kaikai/scripts/tuixue_v3/web/start_remote.sh"
LOG_DIR="/tmp/tuixue_*.log"

case "$1" in
    install)
        mkdir -p "$HOME/Library/LaunchAgents"
        cp "$PLIST_SRC" "$PLIST_DST"
        launchctl load -w "$PLIST_DST" 2>&1
        echo "✓ 已安装并注册(每天 8 AM 自动重启)"
        sleep 1
        $0 status
        ;;
    start)
        launchctl load -w "$PLIST_DST" 2>&1
        echo "✓ 启动调度器"
        ;;
    stop)
        launchctl unload -w "$PLIST_DST" 2>&1
        echo "✓ 停止调度器(8 AM 不再触发)"
        ;;
    restart)
        $0 stop
        sleep 1
        $0 start
        ;;
    run-now)
        # 立即执行一次(忽略日历触发)
        echo "→ 立即触发 start_remote.sh …"
        bash "$START_SCRIPT" &
        echo "  ✓ 已后台启动 (pid=$!)，几秒后查 TG 应收到 URL 推送"
        ;;
    status)
        echo "LaunchAgent 状态:"
        launchctl list | grep "kaikai.tuixue.daily" || echo "  未加载"
        echo ""
        echo "当前服务进程:"
        ps aux | grep -E "tuixue_v3.web.server|cloudflared tunnel" | grep -v grep | awk '{printf "  pid=%s  %s\n", $2, $11" "$12" "$13}'
        echo ""
        echo "本机健康:"
        curl -s --max-time 3 http://localhost:7799/api/health | python3 -m json.tool 2>/dev/null | sed 's/^/  /' || echo "  ✗ 服务未启动"
        echo ""
        echo "日志列表:"
        ls -1 /tmp/tuixue_*.log /tmp/cloudflared.log 2>/dev/null | xargs -I{} sh -c 'echo "  {} ($(wc -l < {} 2>/dev/null) 行)"'
        ;;
    uninstall)
        launchctl unload "$PLIST_DST" 2>/dev/null
        rm -f "$PLIST_DST"
        echo "✓ 已卸载"
        ;;
    logs)
        # 实时跟踪启动过程
        tail -f /tmp/tuixue_server.log /tmp/cloudflared.log 2>/dev/null
        ;;
    *)
        echo "用法: $0 {install|start|stop|restart|run-now|status|uninstall|logs}"
        echo ""
        echo "  install    安装调度(每天 8 AM)"
        echo "  start      启用已安装的调度"
        echo "  stop       暂停调度"
        echo "  restart    重启调度"
        echo "  run-now    立即执行一次启动(不等 8 AM)"
        echo "  status     查看进程 / 健康 / 日志"
        echo "  uninstall  卸载"
        echo "  logs       tail -f 启动 + 隧道日志"
        exit 1
        ;;
esac
