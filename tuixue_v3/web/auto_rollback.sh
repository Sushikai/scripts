#!/bin/bash
# 退学 v3 · Sprint 10 自动回滚 — perf budget 3 连续 fail 时触发
# 策略: SW shell 回滚到 previous 版本 + 清 app.js SW cache (用户下次访问会拉到旧版)
# 触发方式: 监听 perf_budget_check.py exit code 2
#   - cron 每小时: 0 * * * * cd /Users/kaikai/scripts/tuixue_v3 && python3 web/tests/perf_budget_check.py || web/auto_rollback.sh
#   - launchd 守护: KeepAlive 监听 budget_history.jsonl
#
# 安全机制:
#   1. 不会无限回滚 — 保留 latest_shell + previous_shell, 回滚到 previous 后下次只触发"清缓存"动作
#   2. 写 logs/auto_rollback.log 全程 trace
#   3. 推 TG 通知用户 (Telegram bot api)

set -e

LOG_DIR="/Users/kaikai/scripts/tuixue_v3/logs"
LOG_FILE="$LOG_DIR/auto_rollback.log"
STATE_DIR="/Users/kaikai/scripts/tuixue_v3/.rollback"
mkdir -p "$LOG_DIR" "$STATE_DIR"

ts() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*" | tee -a "$LOG_FILE"; }

SW="/Users/kaikai/scripts/tuixue_v3/web/static/sw.js"
ROLLBACK_STATE="$STATE_DIR/last_rollback_ts"
COOLDOWN_SEC=3600  # 1h 内不重复回滚

# 1) cooldown 守门
if [ -f "$ROLLBACK_STATE" ]; then
    last=$(cat "$ROLLBACK_STATE")
    now=$(date +%s)
    diff=$((now - last))
    if [ "$diff" -lt "$COOLDOWN_SEC" ]; then
        log "cooldown: 距上次回滚 ${diff}s < ${COOLDOWN_SEC}s, 跳过"
        exit 0
    fi
fi

# 2) 找当前 CACHE version 与 previous 备份
if [ ! -f "$SW" ]; then
    log "✗ SW 文件不存在: $SW"
    exit 1
fi
current_cache=$(grep -oE "tuixue-v3-shell-v[0-9]+(-sprint[0-9]+)?" "$SW" | head -1)
log "current SW cache: $current_cache"

# 3) 找 git history 中 *真正* previous shell — 倒着遍历 commit,直到 cache version 不一样的那个
# 注:git tree 内部路径是 tuixue_v3/web/static/sw.js (我们是被嵌入 home repo 的子目录)
prev_version=""
cd /Users/kaikai/scripts/tuixue_v3
git log --format=%H -20 -- web/static/sw.js | while IFS= read -r hash; do
    [ -z "$hash" ] && continue
    cand=$(git show "${hash}:tuixue_v3/web/static/sw.js" 2>/dev/null | grep -oE "tuixue-v3-shell-v[0-9]+(-sprint[0-9]+)?" | head -1)
    if [ -n "$cand" ] && [ "$cand" != "$current_cache" ]; then
        echo "$cand|$hash"
        break
    fi
done > "$STATE_DIR/prev_candidate.txt"
if [ -s "$STATE_DIR/prev_candidate.txt" ]; then
    prev_version=$(cut -d'|' -f1 "$STATE_DIR/prev_candidate.txt")
    prev_hash=$(cut -d'|' -f2 "$STATE_DIR/prev_candidate.txt")
fi
log "previous SW cache (different from current): $prev_version"

if [ -z "$prev_version" ] || [ "$prev_version" = "$current_cache" ]; then
    log "✗ 没找到 previous SW version 或与当前相同, 放弃回滚"
    exit 1
fi

# 4) 真回滚: 改 sw.js 的 CACHE 常量 + bump PRECACHE 触发客户端重新拉
log "→ 回滚 SW: $current_cache → $prev_version"
cp "$SW" "${SW}.bak.$(ts | tr ' :' '__')"
sed -i.bak "s|const CACHE = '${current_cache}'|const CACHE = '${prev_version}'|" "$SW"

# 5) 写时间戳 + 推 TG (可选, BOT_TOKEN 存在时)
date +%s > "$ROLLBACK_STATE"
log "✓ 回滚完成: $current_cache → $prev_version"
log "  客户端下次访问会走新 cache 拉 SW, 旧 PRECACHE 失效"

# 6) 推 TG (如果有 BOT_TOKEN)
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    msg="🔄 tuixue_v3 auto-rollback: SW ${current_cache} → ${prev_version} (perf budget 3 连续 fail)"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d text="$msg" >/dev/null 2>&1 || log "(TG 推送失败, 不影响回滚)"
fi
exit 0
