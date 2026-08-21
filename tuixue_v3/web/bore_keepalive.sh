#!/usr/bin/env bash
# bore_keepalive.sh — 保活 bore.pub TCP 隧道 (2026-08-07)
#
# 解决的问题: Akari Networks 出口 IP 段 ban 了 ngrok/cloudflared API,
#             公网 HTTPS 全挂 (8/5 股票源, 8/7 ngrok/crl/api.trycloudflare.com)
#             → 唯一可用公网方案: bore.pub (Rust TCP 隧道, 服务端纯 TCP 转发)
#
# 设计:
#   1. bore 由本脚本拉起 (不是 launchd, 因为 bore 端口可能变)
#   2. 每 60s 探活 bore.pub:42576,失败 3 次 → kill bore 重新拉起
#   3. 重新拉起后端口可能变 (42576 → 42577),写到 tunnel_url.txt 第 1 行
#   4. LAN URL (第 2 行) 保留不动
#
# 与其他 keepalive 关系:
#   - tunnel_keepalive: 写 URL_FILE 的"tunnel_url.txt",本脚本直接操作此文件
#   - mobile_link_keepalive: 探活 + 自愈 (只看 URL_FILE 内容, 不管是谁起的)
#
# launchd 守护: com.kaikai.tuixue.bore-keepalive (KeepAlive=true)

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-7799}"
# R-FIX 2026-08-10: /tmp 会被 macOS 清空,bore 二进制丢失会导致 keepalive 静默失败
# 优先找永久位置,没有再 fallback /tmp/bore
BORE_BIN=""
for cand in \
  "$HOME/.local/bin/bore" \
  "/opt/homebrew/bin/bore" \
  "/usr/local/bin/bore" \
  "/tmp/bore"; do
  if [ -x "$cand" ]; then BORE_BIN="$cand"; break; fi
done
if [ -z "$BORE_BIN" ]; then
  fail "bore 二进制未找到 (搜过 ~/.local/bin / brew / /tmp), 请 brew install bore-cli"
  send_tg "❌ bore 二进制缺失,请 brew install bore-cli" 2>/dev/null
  exit 1
fi
URL_FILE="/Users/kaikai/scripts/tuixue_v3/tunnel_url.txt"
LOG="/tmp/tuixue_tunnels/bore_keepalive.log"
LAN_IP=$(
  /sbin/ifconfig 2>/dev/null | awk '
    /^[a-z]/ { iface=$1; sub(":", "", iface); next }
    /inet / && !/inet6/ && $2 != "127.0.0.1" { print $2; exit }
  '
)
[ -z "$LAN_IP" ] && LAN_IP="192.168.5.101"
LAN_URL="http://${LAN_IP}:${PORT}"
mkdir -p /tmp/tuixue_tunnels

note()  { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG"; }
ok()    { note "✓ $*"; }
fail()  { note "✗ $*"; }

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
  local state_file="/tmp/tuixue_tunnels/bore_keepalive.state"
  local now=$(date +%s)
  local last=0
  [ -f "$state_file" ] && last=$(grep -E "^last_push_${reason}:" "$state_file" | cut -d: -f2)
  [ -z "$last" ] && last=0
  if [ $((now - last)) -lt "$cooldown" ]; then return 1; fi
  echo "${reason}:${now}" >> "$state_file"
  return 0
}

# ─── ngrok 健康门控 ───
# 2026-08-12: 仅查本地 ngrok agent 不够 (8/9 后 Akari 出口 IP 段被 ban,
# agent 本地能返回 tunnels JSON, 但公网永远 timeout → 误判 healthy → bore 永不写 URL_FILE).
# 现在必须真实 HTTP 探活 ngrok 公网 URL + Akari 公网出口 IP 双重检测.
ngrok_is_healthy() {
  local url code
  url=$(curl -s --max-time 4 "http://127.0.0.1:4040/api/tunnels" 2>/dev/null | \
    python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    ts=d.get('tunnels',[])
    print(ts[0]['public_url'] if ts else '')
except: pass
" 2>/dev/null)
  [ -z "$url" ] && return 1
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
    -H "ngrok-skip-browser-warning: 1" "$url/api/health" 2>/dev/null)
  [ "$code" = "200" ] || return 1
  # 二次校验: 出口 IP 必须不在 Akari 被 ban 段 (160.248.x.x)
  local client_ip
  client_ip=$(curl -s --max-time 6 \
    -H "ngrok-skip-browser-warning: 1" "$url/api/health" 2>/dev/null | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('client_ip',''))" 2>/dev/null)
  if [[ "$client_ip" == 160.248.* ]]; then
    note "ngrok 公网 reachable 但出口 IP $client_ip 在 Akari ban 段, 视为不健康"
    return 1
  fi
  return 0
}

# ─── 启动 bore ───
start_bore() {
  pkill -9 -f "bore local $PORT" 2>/dev/null
  sleep 1
  rm -f /tmp/bore.log
  # bore local <port> --to bore.pub → 输出含 "listening at bore.pub:<PORT>"
  "$BORE_BIN" local "$PORT" --to bore.pub >/tmp/bore.log 2>&1 &
  local pid=$!
  sleep 6
  if ! ps -p $pid > /dev/null 2>&1; then
    fail "bore 启动失败 (pid $pid 不存在)"
    return 1
  fi
  # 解析端口
  local remote_port
  remote_port=$(grep -oE "listening at bore.pub:[0-9]+" /tmp/bore.log | head -1 | awk -F: '{print $2}')
  if [ -z "$remote_port" ]; then
    fail "bore 启动但未拿到端口, log: $(cat /tmp/bore.log | tail -3)"
    return 1
  fi
  PUB_URL="http://bore.pub:${remote_port}"
  ok "bore 启动成功 → $PUB_URL (pid=$pid)"
  # 2026-08-09: ngrok 健康时跳过 URL_FILE 写入 — bore 只做热备,
  # 否则 bore 每次重启换端口会把稳定 ngrok URL 覆盖成死链
  if ngrok_is_healthy; then
    ok "ngrok 健康, 跳过 URL_FILE 写入 (bore 保活中, url=$PUB_URL)"
    return 0
  fi
  # 写 URL_FILE 第 1 行 (LAN 保留在第 2 行)
  cat > "$URL_FILE" <<EOF
${PUB_URL}
${LAN_URL}
EOF
  echo "bore" > "/Users/kaikai/scripts/tuixue_v3/tunnel_method.txt"
  if should_push "bore_url"; then
    send_tg "🆕 tuixue_v3 bore tunnel 启动\n公网: ${PUB_URL}\nLAN: ${LAN_URL}" && ok "推 TG bore URL"
  fi
  return 0
}

# ─── 探活 bore ───
probe_bore() {
  local url="$1"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "${url}/api/health" 2>/dev/null)
  [ "$code" = "200" ] && echo "OK" || echo "FAIL_${code}"
}

# ─── 入口 ───
note "===== bore_keepalive 启动, PORT=$PORT, LAN=$LAN_URL ====="

# 启动 bore (首次)
if ! start_bore; then
  fail "首次启动 bore 失败, 退出"
  exit 1
fi

# 当前 bore URL (从文件读)
PUB_URL=$(head -1 "$URL_FILE")
CONSEC_FAIL=0
LOOP=0

# 当前 bore URL (从文件读)
PUB_URL=$(head -1 "$URL_FILE")
CONSEC_FAIL=0
LOOP=0

# 探测目标 URL:
#   - 如果 URL_FILE 是 ngrok (ngrok 健康, bore 只做热备), 仍然 probe bore 自己启动的端口
#   - 如果 URL_FILE 是 bore (ngrok 不健康, bore 是主隧道), 用 URL_FILE 的端口
#   不能无脑用 URL_FILE: ngrok 健康时 URL_FILE 是 ngrok 固定域名, 用它 probe 不到 bore
# 用 BORE_URL (来自 start_bore) + 当前 URL_FILE 谁更倾向 bore 来判定
_get_probe_target() {
  local url_file_url bore_url
  url_file_url=$(head -1 "$URL_FILE" 2>/dev/null)
  # bore 进程实际端口: 从 /tmp/bore.log 最新一行解析 (start_bore 重写该文件)
  bore_url=$(grep -oE "listening at bore.pub:[0-9]+" /tmp/bore.log 2>/dev/null | tail -1 | awk '{print "http://" $NF}')
  if [ -z "$bore_url" ]; then
    # 没有 bore.log, 可能是 start_bore 还没跑过 → fallback URL_FILE
    echo "$url_file_url"
    return
  fi
  # ngrok 健康 → probe 自己的 bore 端口 (即使 URL_FILE 是 ngrok)
  if ngrok_is_healthy; then
    echo "$bore_url"
  else
    # ngrok 挂 → URL_FILE 应当是 bore URL; 优先用 bore 实际端口 (最新)
    if [[ "$url_file_url" == *"bore.pub"* ]]; then
      echo "$url_file_url"
    else
      echo "$bore_url"
    fi
  fi
}

while true; do
  LOOP=$((LOOP + 1))
  sleep 30

  # 每轮重新判定探测目标 (处理端口漂移 + URL_FILE 被外部脚本改写)
  PROBE_TARGET=$(_get_probe_target)
  if [ -n "$PROBE_TARGET" ] && [ "$PROBE_TARGET" != "$PUB_URL" ]; then
    note "探测目标变化: $PUB_URL → $PROBE_TARGET"
    PUB_URL="$PROBE_TARGET"
    CONSEC_FAIL=0
  fi

  # 检查 bore 进程还活着吗
  if ! pgrep -f "bore local $PORT" > /dev/null 2>&1; then
    fail "bore 进程死了, 自动重启"
    if start_bore; then
      PUB_URL=$(_get_pub_url)
    fi
    CONSEC_FAIL=0
    continue
  fi

  # 探活
  PROBE=$(probe_bore "$PUB_URL")
  if [ "$PROBE" = "OK" ]; then
    if [ "$CONSEC_FAIL" -gt 0 ]; then
      ok "恢复 (was fail=$CONSEC_FAIL), url=$PUB_URL"
      CONSEC_FAIL=0
    elif [ $((LOOP % 20)) -eq 0 ]; then
      note "💚 心跳 probe OK, url=$PUB_URL"
    fi
    CONSEC_FAIL=0
  else
    CONSEC_FAIL=$((CONSEC_FAIL + 1))
    fail "探活失败 #$CONSEC_FAIL ($PROBE), url=$PUB_URL"
    # bore 进程在, 但端口临时不通 → bore.pub server 周期性回收空闲端口的副作用
    # 先静默等 90s (不杀 bore, 不换端口), 给它自我恢复机会; 真死了再杀
    if [ "$CONSEC_FAIL" -ge 3 ] && [ "$CONSEC_FAIL" -lt 6 ] && pgrep -f "bore local $PORT" > /dev/null 2>&1; then
      fail "  bore 进程还在, 等 90s 给端口自我恢复 (fail=$CONSEC_FAIL)"
      sleep 90
      continue
    fi
    if [ "$CONSEC_FAIL" -ge 6 ] || ! pgrep -f "bore local $PORT" > /dev/null 2>&1; then
      fail "连续 $CONSEC_FAIL 次失败 / bore 真死, 重启 bore"
      if start_bore; then
        PUB_URL=$(_get_pub_url)
        CONSEC_FAIL=0
      fi
    fi
  fi
done