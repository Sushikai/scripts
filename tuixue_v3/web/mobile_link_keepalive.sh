#!/usr/bin/env bash
# mobile_link_keepalive.sh — 手机端链接保活守护 (2026-08-03)
#
# 解决的问题: 移动端 (尤其是 iPhone Safari) 多久无访问 + ngrok session idle 断开 → 用户日重启发现链接死
# 改良现有 tunnel_keepalive.sh (只监视 URL 变化 + TG 推送), 加入主动健康检查 + 自愈
#
# 设计原则 (3 层防御):
#   L1 被动监视 — 每 30s: 拉 ngrok API / curl cf URL,URL 变化 → 推 TG (变更新)
#   L2 主动探活 — 每 60s: GET {public_url}/api/health,模拟移动端真实访问
#                 - 失败 1 次: 记日志
#                 - 失败 3 次 (5min 内): 判定 tunnel 假死 (URL 还在但实际不可达)
#   L3 自愈     — L2 失败 3 次后:
#                 - kill process 但不解 PID (让 launchd KeepAlive 自动启新实例)
#                 - 等待 30s, 再 curl 验
#                 - 还失败 → 推 TG 告警
#
# 与现有 tunnel_keepalive 关系:
#   - 现有 tunnel_keepalive 仍负责 URL 变化检测 + 推送 (URL_FILE writer)
#   - 本脚本只负责: 主动探活 + 自愈 + 告警
#   - 一个 URL 文件只有一个 writer, 一个 health 只有一个 probe (本脚本)
#
# 与 launchd 关系:
#   - 跟 tunnel_keepalive 同样 launchd 守护 (com.kaikai.tuixue.mobile-link-keepalive)
#   - 不启动 tunnel, 不写 URL_FILE — 只监视 + 自愈 + 告警
#   - 自愈路径: kill ngrok/cf 进程 → launchd KeepAlive=true 自动重启
#   - 不会 ssh spawn (避免 7-26 教训)

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-7799}"
URL_FILE="/Users/kaikai/scripts/tuixue_v3/tunnel_url.txt"
LAN_URL="http://192.168.101.50:7799"
LOG="/tmp/tuixue_tunnels/mobile_link_keepalive.log"
STATE_FILE="/tmp/tuixue_tunnels/mobile_link.state"
mkdir -p /tmp/tuixue_tunnels

note()  { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG"; }
ok()    { note "✓ $*"; }
fail()  { note "✗ $*"; }

# ─── TG push (复用 lib_common.send_telegram) ───
send_tg() {
  local msg="$1"
  [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && return 1
  [ -f "$HOME/.hermes/env.sh" ] && source "$HOME/.hermes/env.sh" 2>/dev/null
  PYTHONPATH="${PYTHONPATH:-$ROOT/..}" python3 -c "
from tuixue_v3.lib_common import send_telegram
import sys
try:
    send_telegram('''$msg''', parse_mode='', silent=True)
except Exception as e:
    sys.exit(1)
" 2>/dev/null
}

# 静默期检查 (避免重复推 TG 噪音)
should_push() {
  local reason="$1"
  local cooldown="${2:-300}"  # 默认 5min 静默
  local now=$(date +%s)
  local last=0
  [ -f "$STATE_FILE" ] && last=$(grep -E "^last_push_${reason}:" "$STATE_FILE" | tail -1 | cut -d: -f2)
  if [ -z "$last" ]; then last=0; fi
  local diff=$((now - last))
  if [ "$diff" -lt "$cooldown" ]; then
    return 1
  fi
  echo "last_push_${reason}:${now}" >> "$STATE_FILE"
  # 清理过老 state (10 字段)
  if [ -f "$STATE_FILE" ]; then
    tail -10 "$STATE_FILE" > "${STATE_FILE}.tmp" && mv "${STATE_FILE}.tmp" "$STATE_FILE"
  fi
  return 0
}

# ─── L1: 读取当前公开 URL (从 URL_FILE) ───
get_public_url() {
  [ -f "$URL_FILE" ] && head -1 "$URL_FILE" | grep -E "^https?://" || echo ""
}

# ─── L2: 主动探活 (模拟 iPhone Safari) ───
# 关键: 模拟 iPhone Safari UA + 不带 skip header, 测真实场景
# 失败条件: 1) 不返回 200  2) 返回 ERR_NGROK_6024 (interstitial) 3) 超时 12s
probe_public_url() {
  local url="$1"
  local iphone_ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
  local headers
  local code
  local body
  # 先用 iPhone UA 探 (没 skip header), 模拟最坏情况
  headers=$(curl -s -o /tmp/_probe_body.html -w "%{http_code}" --max-time 12 \
    -A "$iphone_ua" \
    "$url/api/health" 2>/dev/null)
  code="$headers"
  body=$(cat /tmp/_probe_body.html 2>/dev/null | head -1)
  if [ "$code" = "200" ] && ! grep -q "ERR_NGROK_6024" /tmp/_probe_body.html 2>/dev/null; then
    echo "OK"
    return 0
  fi
  # 第二次: 带 skip header (SW v296 场景), 测客户端有 SW 后的真实情况
  headers=$(curl -s -o /tmp/_probe_body2.html -w "%{http_code}" --max-time 12 \
    -H "ngrok-skip-browser-warning: 1" \
    -A "tuixue-v3-mobile/1.0" \
    "$url/api/health" 2>/dev/null)
  code="$headers"
  if [ "$code" = "200" ] && ! grep -q "ERR_NGROK_6024" /tmp/_probe_body2.html 2>/dev/null; then
    echo "OK_SW"
    return 0
  fi
  # 尝试带 skip header 拿 / 看是 6024 还是别的
  local ngrok_check
  ngrok_check=$(curl -s --max-time 8 -H "ngrok-skip-browser-warning: 1" -A "tuixue-v3-mobile/1.0" \
    -D /tmp/_probe_h.txt -o /dev/null "$url/" 2>/dev/null)
  local is_6024=$(grep -i "ngrok-error-code" /tmp/_probe_h.txt 2>/dev/null | head -1)
  if [ -n "$is_6024" ]; then
    echo "NGROK_6024"
  else
    echo "FAIL"
  fi
  return 1
}

# ─── L3: 自愈 — kill tunnel 进程让 launchd 重启 ───
heal_tunnel() {
  local url="$1"
  local reason="$2"
  fail "L3 自愈触发: $reason (url=$url)"
  # 1. 杀 ngrok 进程 (不论 PID, pkill 留给 launchd 拉起)
  if pgrep -f "ngrok http 7799" > /dev/null 2>&1; then
    pkill -f "ngrok http 7799"
    note "  killed ngrok (launchd KeepAlive 会重启)"
  fi
  # 2. 杀 cloudflared (如果在那)
  if pgrep -f "cloudflared tunnel --url" > /dev/null 2>&1; then
    pkill -f "cloudflared tunnel --url"
    note "  killed cloudflared (launchd KeepAlive 会重启)"
  fi
  # 3. 等 30s 让 launchd 重启 + 重建 tunnel
  sleep 30
  # 4. 复探
  local new_url
  new_url=$(get_public_url)
  if [ -n "$new_url" ]; then
    local result
    result=$(probe_public_url "$new_url")
    if [ "$result" = "OK" ] || [ "$result" = "OK_SW" ]; then
      ok "自愈成功: 新 URL OK ($result)"
      return 0
    fi
  fi
  fail "自愈 30s 后仍失败, 推 TG 告警"
  return 1
}

# ─── 状态机: 连续失败计数 ───
CONSEC_FAIL=0
HEAL_COOLDOWN_TS=0  # 上次自愈时间戳, 避免 5min 内重复自愈
HEAL_COOLDOWN=600   # 10min 静默

note "===== mobile_link_keepalive 启动, PORT=$PORT ====="
note "  L1: 30s 监视 URL_FILE"
note "  L2: 60s 主动探活 (iPhone Safari UA)"
note "  L3: 失败 3 次 → kill tunnel 让 launchd 重启"

# 立即跑一次 (启动时探活)
sleep 5
LOOP=0
while true; do
  LOOP=$((LOOP + 1))
  sleep 30
  # ── L1 ──
  PUB_URL=$(get_public_url)
  if [ -z "$PUB_URL" ]; then
    # URL_FILE 空, 静默 (server 启动中 / tunnel 未就绪)
    if [ $((LOOP % 20)) -eq 0 ]; then
      note "URL_FILE 仍空 (启动中?)"
    fi
    continue
  fi
  # ── L2: 每 60s 探一次 (LOOP % 2 = 0) ──
  if [ $((LOOP % 2)) -eq 0 ]; then
    PROBE_RESULT=$(probe_public_url "$PUB_URL")
    if [ "$PROBE_RESULT" = "OK" ] || [ "$PROBE_RESULT" = "OK_SW" ]; then
      if [ "$CONSEC_FAIL" -gt 0 ]; then
        ok "恢复 (was fail=$CONSEC_FAIL), url=$PUB_URL, result=$PROBE_RESULT"
      elif [ $((LOOP % 20)) -eq 0 ]; then
        # 每 10min 写一次心跳, 证明脚本活跃
        note "💚 心跳 probe OK ($PROBE_RESULT), url=$PUB_URL, fails=$CONSEC_FAIL"
      fi
      CONSEC_FAIL=0
    else
      CONSEC_FAIL=$((CONSEC_FAIL + 1))
      fail "主动探活失败 #$CONSEC_FAIL ($PROBE_RESULT), url=$PUB_URL"
      # ── L3: 3 次失败触发自愈 (考虑 10min 冷却) ──
      if [ "$CONSEC_FAIL" -ge 3 ]; then
        NOW=$(date +%s)
        if [ $((NOW - HEAL_COOLDOWN_TS)) -ge "$HEAL_COOLDOWN" ]; then
          HEAL_COOLDOWN_TS=$NOW
          if should_push "heal" 900; then  # 15min 静默
            MSG=$(printf '🚨 tuixue_v3 mobile link 探活失败3次 → 自愈\nURL: %s\nresult: %s\n失败序号: #%d' \
              "$PUB_URL" "$PROBE_RESULT" "$CONSEC_FAIL")
            send_tg "$MSG" && ok "推 TG 自愈告警"
          fi
          heal_tunnel "$PUB_URL" "$PROBE_RESULT #$CONSEC_FAIL"
          # 自愈后重置计数
          CONSEC_FAIL=0
        fi
      fi
    fi
  fi
done
