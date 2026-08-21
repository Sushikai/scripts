#!/bin/bash
# 后台跑 10000 轮 ZT 参数寻优 (2026-08-04 升级: 200 → 10000)
# 输出: /tmp/zt_optimize_10000_2026-08-04.json
set -e
cd /Users/kaikai/scripts

LOG=/tmp/zt_optimize_10000.log
OUT=/tmp/zt_optimize_10000_$(date +%s).json

echo "[$(date +%T)] start 10000-round optimize" > "$LOG"
exec >> "$LOG" 2>&1

START=2025-12-01
END=2026-08-04
ITER=10000
POP=100

python3 -m tuixue_v3.zt_optimizer \
    --start "$START" --end "$END" \
    --iter "$ITER" --pop "$POP" \
    --board all --save 2>&1

echo "[$(date +%T)] done 10000-round optimize"
echo "OUT=$OUT" > /tmp/zt_optimize_10000.done
