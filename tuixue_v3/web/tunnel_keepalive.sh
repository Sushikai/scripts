#!/usr/bin/env bash
# tunnel_keepalive.sh — 守护: tunnel 状态变化时自动 + 推 TG
#
# 设计原则 (2026-07-26):
#   1. ngrok 由 launchd com.kaikai.tuixue.ngrok (KeepAlive=true) 拉起
#   2. 本脚本只监视, 不负责启动 tunnel
#   3. 每 30s:
#       a) curl ngrok API 拉公网 URL, 跟 tunnel_url.txt 比对
#       b) URL 变了 → 推 TG (含旧 URL / 新 URL / LAN / localhost)
#       c) tunnel URL 拿不到 (ngrok agent 死了 OR 没 URL) → 推 TG 告警
#       d) 5 分钟静默期内不重复推
#
# 解决"改代码就断"的根因:
#   - 改代码后我会 kill -9 server PID → launchd KeepAlive 自动重启 server
#   - ngrok 进程在 launchd 下保持不变 (server 重启对 ngrok 是瞬断)
#   - URL 不变 → 用户继续用相同链接
#   - 极特殊情况 (ngrok 进程也死了) → TG 推送告警, 不会静默

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-7799}"
NGROK_API="http://127.0.0.1:4040/api/tunnels"
URL_FILE="/Users/kaikai/scripts/tuixue_v3/tunnel_url.txt"
LOG="/tmp/tuixue_tunnels/keepalive.log"
STATE_FILE="/tmp/tuixue_tunnels/keepalive.state"
mkdir -p /tmp/tuixue_tunnels

# ─── TG push (复用 tunnel_lib.sh 的 send_tg 风格) ───
send_tg() {
  local msg="$1"
  [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && return 1
  # 加载环境
  [ -f "$HOME/.hermes/env.sh" ] && source "$HOME/.hermes/env.sh"
  PYTHONPATH="${PYTHONPATH:-$ROOT/..}" python3 -c "
from tuixue_v3.lib_common import send_telegram
import sys
try:
    send_telegram('''$msg''', parse_mode='', silent=True)
except Exception as e:
    sys.exit(1)
" 2>/dev/null
}

note()  { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG"; }
fail()  { note "✗ $*"; }
ok()    { note "✓ $*"; }

# 获取当前 ngrok URL (从 agent API)
get_ngrok_url() {
  curl -s --max-time 4 "$NGROK_API" 2>/dev/null | \
    python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    ts=d.get('tunnels',[])
    print(ts[0]['public_url'] if ts else '')
except: pass
" 2>/dev/null
}

# 检查 server 是否健康
check_server() {
  curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null
}

# 检查当前告警态 (避免 5min 内重复推)
should_push() {
  local reason="$1"
  local now=$(date +%s)
  local last=0
  [ -f "$STATE_FILE" ] && last=$(grep -E "^last_push_${reason}:" "$STATE_FILE" | cut -d: -f2)
  if [ -z "$last" ]; then last=0; fi
  local diff=$((now - last))
  if [ "$diff" -lt 300 ]; then
    return 1  # 静默期内
  fi
  echo "${reason}:${now}" >> "$STATE_FILE"
  return 0  # 可推
}

# LAN IP — 优先 en0, 否则 en1, 最后兜底 127.0.0.1
# (R-fix-2026-08-06: 旧版用 ipconfig getifaddr en0,但 macOS 上 PATH 不一定含 /sbin;
#  改用 /sbin/ifconfig 通用, 并 fallback 到 en1/en2 等活跃网卡)
LAN_IP=$(
  /sbin/ifconfig 2>/dev/null | awk '
    /^[a-z]/ { iface=$1; sub(":", "", iface); next }
    /inet / && !/inet6/ && $2 != "127.0.0.1" { print $2; exit }
  '
)
[ -z "$LAN_IP" ] && LAN_IP=$(/sbin/ifconfig en1 2>/dev/null | awk '/inet / && !/inet6/ && $2 != "127.0.0.1" {print $2; exit}')
[ -z "$LAN_IP" ] && LAN_IP="127.0.0.1"

prev_url=""
last_alive_dead_ts=0

note "===== tunnel_keepalive 启动, 监 PORT=$PORT, Ngrok API=$NGROK_API ====="

while true; do
  sleep 30

  cur_url=$(get_ngrok_url)
  server_code=$(check_server)

  # ── Case 1: ngrok URL 变化 ──
  if [ -n "$cur_url" ] && [ "$cur_url" != "$prev_url" ]; then
    ok "URL 变化: ${prev_url:-<none>} → $cur_url"
    # 写文件 (多源共存: 公网 ngrok 第 1 行, 局域网 IP 第 2 行)
    # 重要: 旧版直接 echo "$cur_url" > "$URL_FILE" 会把局域网 IP 覆盖掉,
    # 导致 send_telegram 末尾只带公网链接, 手机在同一 WiFi 下速度反而不快
    cat > "$URL_FILE" <<EOF
$cur_url
http://$LAN_IP:$PORT
EOF
    echo "ngrok" > "/Users/kaikai/scripts/tuixue_v3/tunnel_method.txt"
    if should_push "url_change"; then
      MSG=$(printf 'tuixue_v3 公网链接更新\n旧: %s\n新: %s\nLAN: http://%s:%s\n(server /api/health = %s)' \
        "${prev_url:-无}" "$cur_url" "$LAN_IP" "$PORT" "$server_code")
      send_tg "$MSG" && ok "TG 推送 URL 变化成功"
    fi
    prev_url="$cur_url"
  fi

  # ── Case 2: server down (kill -9 后 launchd 还没重启好) ──
  if [ "$server_code" != "200" ]; then
    now=$(date +%s)
    # 启动到当前, 仅在 60s 内不重复报
    if [ $((now - last_alive_dead_ts)) -ge 60 ]; then
      last_alive_dead_ts=$now
      fail "server /api/health = $server_code (url=$cur_url)"
      # 不推 TG 噪音, 仅记日志 — launchd KeepAlive 10s 必然重启
    fi
  fi

  # ── Case 3: ngrok 也死了 (URL 拿不到) ──
  if [ -z "$cur_url" ]; then
    if should_push "ngrok_dead"; then
      fail "ngrok agent 死了, 公网 URL 拿不到!"
      send_tg "⚠️ tuixue_v3 ngrok tunnel 死了, 公网暂不可用\nLAN: http://$LAN_IP:$PORT 仍可用" && ok "TG 告警 ngrok 死"
      # 不再尝试重启 (launchd KeepAlive 应该已经在重启); 等下一轮
    fi
    # 2026-08-07: 清掉 tunnel_url.txt 的死链,免得 send_telegram 把死链发给用户
    # 第 2 行 (LAN) 保留 — 手机在同一 WiFi 下仍可用
    if [ -f "$URL_FILE" ]; then
      local_lan=$(grep -E '^http://[0-9]+\.' "$URL_FILE" | head -1)
      [ -z "$local_lan" ] && local_lan="http://$LAN_IP:$PORT"
      printf '%s\n' "$local_lan" > "$URL_FILE"
      rm -f "/Users/kaikai/scripts/tuixue_v3/tunnel_method.txt"
      ok "已清空公网死链, 只保留 LAN: $local_lan"
    fi
    prev_url=""
  fi
done
