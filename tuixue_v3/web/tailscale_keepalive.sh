#!/usr/bin/env bash
# tailscale_keepalive.sh — 适配 tailscale 模式的 URL_FILE writer (2026-08-12)
#
# 历史背景:
#   旧 bore_keepalive.sh (2026-08-07): bore.pub TCP 隧道, 解决 Akari ban ngrok/cloudflared 出口 IP 段
#   现 (2026-08-12): tailscale 直接可达 100.x IP, server 0.0.0.0:7799 直听
#     - 无需任何 HTTP 隧道, 无端口漂移, 无 ngrok 6024 警告页, 无 bore 回收
#     - tailscale IP 由 tailscale daemon 管理, 机器重启/断网重连后会变,
#       本脚本负责把当前 tailscale IP 写到 tunnel_url.txt, 让前端 / mobile_link_keepalive 读到
#
# 设计:
#   1. 每 30s 读 tailscale ip -4 拿 100.x IP
#   2. IP 变化 → 写 URL_FILE (URL_FILE 第 1 行 = http://<tailscale_ip>:7799, 第 2 行 = LAN)
#      → 推 TG 告知新 IP
#      → 静默期内 (5min) 重复 IP 变化不重复推 TG
#   3. tailscale 离线 (拿不到 IP, daemons not running) → URL_FILE 不动, 让前端 fallback LAN
#   4. tailscale 重连 (IP 变了) → 写新 IP + 推 TG
#
# 与 mobile_link_keepalive.sh 关系:
#   - mobile_link_keepalive 是 URL_FILE reader + 主动探活
#   - 本脚本是 URL_FILE writer (写 tailscale IP)
#   - 一个 URL 文件一个 writer (本脚本), 一个 health 探针 (mobile_link_keepalive)
#
# launchd 守护: com.kaikai.tuixue.tailscale-keepalive (KeepAlive=true)

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-7799}"
URL_FILE="/Users/kaikai/scripts/tuixue_v3/tunnel_url.txt"
METHOD_FILE="/Users/kaikai/scripts/tuixue_v3/tunnel_method.txt"
LOG="/tmp/tuixue_tunnels/tailscale_keepalive.log"
STATE_FILE="/tmp/tuixue_tunnels/tailscale_keepalive.state"
mkdir -p /tmp/tuixue_tunnels

note()  { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG"; }
ok()    { note "✓ $*"; }
fail()  { note "✗ $*"; }

# ─── LAN URL (fallback 第 2 行) ───
LAN_IP=$(
  /sbin/ifconfig 2>/dev/null | awk '
    /^[a-z]/ { iface=$1; sub(":", "", iface); next }
    /inet / && !/inet6/ && $2 != "127.0.0.1" { print $2; exit }
  '
)
[ -z "$LAN_IP" ] && LAN_IP="192.168.5.101"
LAN_URL="http://${LAN_IP}:${PORT}"

# ─── TG push ───
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

should_push() {
  local reason="$1"
  local cooldown="${2:-300}"
  local now=$(date +%s)
  local last=0
  [ -f "$STATE_FILE" ] && last=$(grep -E "^last_push_${reason}:" "$STATE_FILE" | cut -d: -f2)
  [ -z "$last" ] && last=0
  if [ $((now - last)) -lt "$cooldown" ]; then return 1; fi
  echo "${reason}:${now}" >> "$STATE_FILE"
  return 0
}

# ─── 拿 tailscale IP ───
# 2026-08-12: tailscale 二进制位置不固定 (Intel brew /usr/local/bin/tailscale
# 或 Apple Silicon /opt/homebrew/bin/tailscale, 或 /Applications/Tailscale.app/.../Tailscale).
# 用 command -v 自动找,优先 wrapper, 找不到再 fallback app bundle.
get_tailscale_ip() {
  local ts_bin=""
  for cand in "$(command -v tailscale)" \
              "/usr/local/bin/tailscale" \
              "/opt/homebrew/bin/tailscale" \
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"; do
    if [ -x "$cand" ]; then ts_bin="$cand"; break; fi
  done
  [ -z "$ts_bin" ] && return 1
  # tailscale ip -4 返 100.x.x.x (无 daemon 时返空 / error)
  # 2026-08-22: GUI app 卡死时 CLI 会无限挂起, 把整个循环卡死 (曾致 keepalive
  # 停更 12h+)。perl alarm+exec 硬超时 15s, SIGALRM 杀掉挂死的 CLI。
  local ip
  ip=$(perl -e 'alarm shift; exec @ARGV' 15 "$ts_bin" ip -4 2>/dev/null | head -1 | tr -d '[:space:]')
  if [[ "$ip" =~ ^100\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$ip"
    return 0
  fi
  return 1
}

# ─── 写 URL_FILE (tailscale URL 第 1 行, LAN 第 2 行) ───
write_url_file() {
  local ts_ip="$1"
  local new_url="http://${ts_ip}:${PORT}"
  cat > "$URL_FILE" <<EOF
${new_url}
${LAN_URL}
EOF
  echo "tailscale" > "$METHOD_FILE"
  ok "URL_FILE 已写: $new_url (LAN=$LAN_URL)"
}

# ─── 入口 ───
note "===== tailscale_keepalive 启动, PORT=$PORT, LAN=$LAN_URL ====="

PREV_IP=""
INIT_TS=$(date +%s)
INIT_GRACE=90  # 启动后 90s 静默 (避免与 launchd 重启 / mobile_link_keepalive 重启撞车)

while true; do
  sleep 30

  TS_IP=$(get_tailscale_ip || echo "")
  if [ -z "$TS_IP" ]; then
    # tailscale 拿不到 IP (daemon 未起 / 离线 / 未登录)
    if [ $((( $(date +%s) - INIT_TS) % 600 )) -lt 30 ]; then
      # 每 ~10min 报一次 (LOOP 取模不精确,够用)
      if should_push "ts_offline" 600; then
        send_tg "⚠️ tailscale 拿不到 IP\nURL_FILE 未更新, 前端会 fallback LAN: $LAN_URL" && ok "推 TG tailscale offline"
      fi
    fi
    continue
  fi

  # tailscale 在线
  if [ "$TS_IP" != "$PREV_IP" ]; then
    if [ -n "$PREV_IP" ]; then
      # IP 变化 (机器重启 / tailscale 重连分配了新 IP)
      ok "tailscale IP 变化: $PREV_IP → $TS_IP"
      write_url_file "$TS_IP"
      # 启动后 90s 静默期内不推 TG (避免与启动 noise 重复)
      if [ $(($(date +%s) - INIT_TS)) -gt "$INIT_GRACE" ]; then
        if should_push "ip_change" 300; then
          send_tg "🔄 tailscale IP 变化\n旧: $PREV_IP\n新: http://${TS_IP}:${PORT}\n(LAN 备用: $LAN_URL)" && ok "推 TG IP 变化"
        fi
      fi
    else
      # 首次拿到 IP
      write_url_file "$TS_IP"
      ok "首次拿到 tailscale IP: $TS_IP"
      if should_push "ip_init" 60; then  # 1min 静默 (避免启动 race)
        send_tg "🆕 tailscale 链接就绪\n公网: http://${TS_IP}:${PORT}\n(LAN 备用: $LAN_URL)" && ok "推 TG tailscale 就绪"
      fi
    fi
    PREV_IP="$TS_IP"
  else
    # IP 没变, 每 30min 心跳一次
    if [ $(( $(date +%s) % 1800 )) -lt 30 ]; then
      note "💚 tailscale 心跳 OK, ip=$TS_IP"
    fi
  fi
done