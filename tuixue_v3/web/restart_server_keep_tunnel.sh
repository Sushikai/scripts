#!/usr/bin/env bash
# restart_server_keep_tunnel.sh — 仅重启 server, 不动 tunnel (2026-07-26)
#
# 设计理由:
#   改完 server 代码后,我们只 kill server, 让 launchd KeepAlive 自动重启
#   ngrok agent 维持 pool, ngrok URL 不变 → 用户手机上原链接继续可用
#
# 用法:  bash web/restart_server_keep_tunnel.sh
# 退出:  0=OK, 1=fail

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="${PORT:-7799}"

note()  { echo "  $*"; }

# 1. kill server (launchd KeepAlive 会立即拉起,Throttle 10s)
PIDS=$(pgrep -f "tuixue_v3.web.server" || true)
if [ -n "$PIDS" ]; then
  note "killing server PIDs: $PIDS"
  # 用 SIGTERM 而不是 SIGKILL → launchd 干净重启 (无 race)
  kill -TERM $PIDS 2>/dev/null || true
fi

# 2. 等新 server 起来 (launchd ThrottleInterval=10s)
note "等 launchd 拉起新 server (Throttle 10s, 等 15s 保险)..."
for i in $(seq 1 30); do
  sleep 1
  CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "http://127.0.0.1:$PORT/api/health" 2>/dev/null || echo 000)
  if [ "$CODE" = "200" ]; then
    note "✓ server up (${i}s)"
    note "✓ ngrok tunnel 应维持原 URL (无 tunnel 重启)"
    note "  当前 URL: $(cat /Users/kaikai/scripts/tuixue_v3/tunnel_url.txt 2>/dev/null || echo '无')"
    exit 0
  fi
done

note "✗ 30s 内 server 未起来, 看 /tmp/tuixue_server.log"
exit 1
