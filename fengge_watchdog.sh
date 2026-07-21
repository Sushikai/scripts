#!/bin/bash
# fengge_watchdog.sh — 峰哥视频上传保底
# 每天 23:30 跑一次：检查当天 upload_history 是否有 ≥2 条记录
# - 达标：发 TG 简报（每日 2/2 上传）
# - 没达标：发 TG 告警 + 调手动 fengge_pipeline 补传一次
# 同样规则如果连续两天没达标，告警带红框
# 用法：fengge_watchdog.sh [--dry-run] [--force-below]
export LANG=C.UTF-8 LC_ALL=C.UTF-8
set -eo pipefail
# 注:不用 set -u,TODAY/COUNT 在 notify 函数展开时保险起见

HISTORY=/Users/kaikai/tiktok_automation/fengge_upload_history.json
LOCK=/tmp/fengge_pipeline.lock
PIPELINE=/Users/kaikai/scripts/video/fengge_pipeline.py
PYTHON=/Users/kaikai/.hermes/hermes-agent/venv/bin/python3
LOG=/tmp/fengge_watchdog.log

TODAY=$(date +%Y-%m-%d)
COUNT=$(python3 -c "
import json
with open('$HISTORY') as f: h=json.load(f)
print(sum(1 for v in h.values() if v.get('uploaded_at','').startswith('${TODAY}')))
")

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

log "=== fengge watchdog run ==="
log "today=${TODAY} uploads=$COUNT (target=2)"

# TG 通知 helper (用 base64 传中文,避免 bash heredoc surrogate pair 问题)
notify() {
    local msg="$1"
    echo "DEBUG: msg bytes:" >> "$LOG"
    printf "%s" "$msg" | xxd >> "$LOG"
    echo "DEBUG: msg ends" >> "$LOG"
    local b64
    b64=$(printf '%s' "$msg" | base64 | tr -d '\n')
    PYTHONPATH=/Users/kaikai/scripts "$PYTHON" -c "
import sys, base64
sys.path.insert(0, '/Users/kaikai/scripts/tuixue_v3')
from lib_common import send_telegram
msg = base64.b64decode('$b64').decode('utf-8')
send_telegram(msg, parse_mode='text', silent=True)
" 2>&1 | tail -3
}

if [ "$COUNT" -ge 2 ]; then
    notify "✅ 峰哥视频 watchdog｜${TODAY}｜${COUNT}/2 上传达标"
    log "OK, exit"
    exit 0
fi

# 没达标：告警
notify "⚠️ 峰哥视频 watchdog｜${TODAY}｜仅 ${COUNT}/2 上传,正在尝试补传..."
log "below target, attempting补救"

# 防止锁冲突：等锁释放
for i in $(seq 1 60); do
    if ! [ -f "$LOCK" ]; then break; fi
    if ! lsof "$LOCK" >/dev/null 2>&1; then break; fi
    log "wait lock release ($i/60s)"
    sleep 1
done

# 调一次手动跑 (非阻塞,后台)
nohup "$PYTHON" "$PIPELINE" >> /tmp/fengge_watchdog_manual.log 2>&1 &
MANUAL_PID=$!
log "manual fengge_pipeline pid=$MANUAL_PID"

# 等 5 分钟看是否上传成功
for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 30
    NEW_COUNT=$(python3 -c "
import json
with open('$HISTORY') as f: h=json.load(f)
print(sum(1 for v in h.values() if v.get('uploaded_at','').startswith('${TODAY}')))
")
    log "  ${i}*30s: count=$NEW_COUNT"
    if [ "$NEW_COUNT" -ge 2 ]; then
        notify "✅ 峰哥视频 watchdog｜${TODAY}｜补传成功,现 ${NEW_COUNT}/2"
        exit 0
    fi
done

# 5 分钟还没补上：手动进程可能还跑着 (pipeline 正常要 5-15 分钟)
if kill -0 "$MANUAL_PID" 2>/dev/null; then
    log "manual still running, exit watch (will retry tomorrow)"
    notify "⚠️ 峰哥视频 watchdog｜${TODAY}｜补传进程 pid=${MANUAL_PID} 仍在跑,等明早复查"
    exit 0
fi

# 进程已死 + 没补上 = 失败
notify "❌ 峰哥视频 watchdog｜${TODAY}｜补传失败,请人工检查 /tmp/fengge_pipeline.log"
log "manual FAILED"
exit 1