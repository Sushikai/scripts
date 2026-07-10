#!/bin/bash
# 华工科技实时监控服务管理
# 用法: ./realtime_manager.sh {install|start|stop|restart|status|logs|setbuy|uninstall}

PLIST_SRC="/Users/kaikai/scripts/stock/com.kaikai.stock-realtime.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.kaikai.stock-realtime.plist"
SCRIPT="/Users/kaikai/scripts/stock_realtime_monitor.py"
LOG_FILE="/Users/kaikai/scripts/stock/realtime_monitor.log"
STATE_FILE="/Users/kaikai/scripts/stock/realtime_state.json"

case "$1" in
    install)
        mkdir -p "$HOME/Library/LaunchAgents"
        cp "$PLIST_SRC" "$PLIST_DST"
        launchctl load -w "$PLIST_DST" 2>&1
        echo "✓ 已安装并启动服务"
        sleep 2
        $0 status
        ;;
    start)
        launchctl load -w "$PLIST_DST" 2>&1
        echo "✓ 启动服务"
        sleep 2
        $0 status
        ;;
    stop)
        launchctl unload "$PLIST_DST" 2>&1
        echo "✓ 停止服务"
        ;;
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;
    status)
        echo "进程状态:"
        ps aux | grep -E "stock_realtime_monitor" | grep -v grep | head -3
        echo ""
        echo "LaunchAgent 状态:"
        launchctl list | grep "kaikai.stock-realtime" || echo "  未加载"
        echo ""
        echo "持仓成本:"
        if [ -f "$STATE_FILE" ]; then
            python3 -c "import json; d=json.load(open('$STATE_FILE')); print(f'  成本价: {d.get(\"buy_price\", \"未设置\")}')"
        else
            echo "  状态文件不存在"
        fi
        echo ""
        echo "最近日志 (最后 15 行):"
        if [ -f "$LOG_FILE" ]; then
            tail -15 "$LOG_FILE"
        else
            echo "  日志文件不存在"
        fi
        ;;
    logs)
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "日志不存在: $LOG_FILE"
        fi
        ;;
    setbuy)
        # 设置持仓成本价
        if [ -z "$2" ]; then
            echo "用法: $0 setbuy <成本价>"
            echo "当前状态:"
            cat "$STATE_FILE" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  无状态文件"
            exit 1
        fi
        python3 << EOF
import json
from pathlib import Path
state_file = Path("$STATE_FILE")
if state_file.exists():
    state = json.loads(state_file.read_text())
else:
    state = {"signals": {}, "buy_price": None, "last_daily_summary": None}
state["buy_price"] = float("$2")
state_file.parent.mkdir(parents=True, exist_ok=True)
state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
print(f"✓ 已设置持仓成本价: $2")
EOF
        ;;
    clearbuy)
        # 清除持仓成本
        python3 << EOF
import json
from pathlib import Path
state_file = Path("$STATE_FILE")
if state_file.exists():
    state = json.loads(state_file.read_text())
    state["buy_price"] = None
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print("✓ 已清除持仓成本")
else:
    print("状态文件不存在")
EOF
        ;;
    uninstall)
        $0 stop
        rm -f "$PLIST_DST"
        echo "✓ 已卸载"
        ;;
    test)
        echo "执行一次测试扫描（30秒超时）..."
        timeout 30 /Users/kaikai/.hermes/hermes-agent/venv/bin/python3 "$SCRIPT" 2>&1 | head -30
        echo ""
        echo "提示: $0 start 启动 24 小时监控服务"
        ;;
    *)
        echo "华工科技 (000988) 实时监控管理"
        echo ""
        echo "用法: $0 <命令>"
        echo ""
        echo "命令:"
        echo "  install    安装并启动服务"
        echo "  start      启动服务"
        echo "  stop       停止服务"
        echo "  restart    重启服务"
        echo "  status     查看服务状态"
        echo "  logs       实时跟踪日志"
        echo "  setbuy N   设置持仓成本价 N (触发止盈/止损)"
        echo "  clearbuy   清除持仓成本"
        echo "  test       执行一次测试扫描"
        echo "  uninstall  卸载服务"
        exit 1
        ;;
esac
