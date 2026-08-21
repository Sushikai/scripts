#!/usr/bin/env bash
# 后台跑 mobile guard,持续监控 web/static/*.{css,html,js} 改动
# 任何 view 红 → exit 1 + 失败截图复制到 web/static/audit/mobile_fail_*/
set -e
cd "$(dirname "$0")/.."

mkdir -p logs static/audit/mobile_fail
LOG=logs/mobile_guard.log
PID_FILE=logs/mobile_guard.pid

# 防止重复起
if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
    echo "already running pid=$(cat $PID_FILE) (log: $LOG)"
    exit 0
fi

nohup python3 -u tests/mobile_guard.py --watch --out /tmp/mobile_guard > "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "started pid=$PID (log: $LOG)"