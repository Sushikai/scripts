#!/usr/bin/env bash
# 启动 Cloudflare Quick Tunnel 把 localhost:7799 暴露到公网
# 临时 URL,重启进程后会变。适合先验证访问。
#
# 用法:
#   ./start_tunnel.sh           # 默认绑 localhost:7799
#   PORT=9000 ./start_tunnel.sh # 自定义端口
#   ./start_tunnel.sh stop      # 停掉
set -euo pipefail

PORT="${PORT:-7799}"
LOG="${TUNNEL_LOG:-/tmp/tuixue_tunnel.log}"
URL_FILE="${TUNNEL_URL_FILE:-$(dirname "$0")/../tunnel_url.txt}"
PID_FILE="/tmp/tuixue_tunnel.pid"

stop() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "[tunnel] stop pid=$pid"
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
  pkill -f "cloudflared tunnel --url" 2>/dev/null || true
  rm -f "$URL_FILE"
  echo "[tunnel] stopped"
}

if [[ "${1:-}" == "stop" ]]; then
  stop
  exit 0
fi

# 默认启动前先清理
stop

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "[tunnel] cloudflared not installed. brew install cloudflared" >&2
  exit 1
fi

echo "[tunnel] starting on port $PORT, log=$LOG"

# nohup + setsid 让 tunnel 独立于父进程
nohup setsid cloudflared tunnel --no-autoupdate --url "http://localhost:$PORT" \
  > "$LOG" 2>&1 &
echo $! > "$PID_FILE"

# 等 URL 出现 (最多 30s)
for i in {1..30}; do
  if [[ -f "$URL_FILE" ]] && [[ -s "$URL_FILE" ]]; then
    break
  fi
  # 从日志里抓 https://*.trycloudflare.com
  url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1 || true)"
  if [[ -n "$url" ]]; then
    echo "$url" > "$URL_FILE"
    break
  fi
  sleep 1
done

if [[ -f "$URL_FILE" ]]; then
  echo ""
  echo "════════════════════════════════════════════"
  echo "  🌐 公网 URL: $(cat "$URL_FILE")"
  echo "════════════════════════════════════════════"
  echo "  局域网:    http://192.168.101.3:$PORT"
  echo "  本机:      http://localhost:$PORT"
  echo "  日志:      $LOG"
  echo "  停止:      $0 stop"
  echo "════════════════════════════════════════════"
else
  echo "[tunnel] ⚠️ URL 30s 内未出现,查看日志: $LOG"
  tail -30 "$LOG" 2>&1 | sed 's/^/  /'
fi