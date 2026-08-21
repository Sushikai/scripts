#!/usr/bin/env bash
# tunnel_supervisor.sh — 极简 lt 守护 (死了就重启, 不测 URL)
#
# 2026-07-25 R6 教训: orchestrator 测 URL 失败 → 重启 → 触发 loca.lt 限流 → 死亡螺旋
# 新方案: 只监控进程, 不测 URL (外网用户自己测)
# 死了等 30s 再起, 避免疯狂重启

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-7799}"
LOG_DIR="/tmp/tuixue_tunnels"
mkdir -p "$LOG_DIR"

LT_PIDFILE="/tmp/tuixue_tunnels/lt.pid"
LT_LOGFILE="/tmp/tuixue_tunnels/lt.log"
LT_URLFILE="/tmp/tuixue_tunnels/lt_url.txt"
URL_FILE="$ROOT/tunnel_url.txt"
ORCH_LOG="$LOG_DIR/supervisor.log"

note()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$ORCH_LOG"; }
fail()  { note "✗ $*"; }
ok()    { note "✓ $*"; }

is_lt_running() {
  [ -f "$LT_PIDFILE" ] && kill -0 "$(cat $LT_PIDFILE)" 2>/dev/null
}

start_lt() {
  pkill -f "lt --port ${PORT}" 2>/dev/null || true
  sleep 1
  rm -f "$LT_PIDFILE" "$LT_URLFILE"
  nohup /opt/homebrew/bin/lt --port "${PORT}" > "$LT_LOGFILE" 2>&1 &
  local pid=$!
  echo $pid > "$LT_PIDFILE"

  # wait up to 15s for URL
  for i in $(seq 1 15); do
    sleep 1
    url=$(grep -oE "https://[a-z-]+\.loca\.lt" "$LT_LOGFILE" 2>/dev/null | head -1)
    if [ -n "$url" ]; then
      echo "$url" > "$LT_URLFILE"
      echo "$url" > "$URL_FILE"
      return 0
    fi
  done
  return 1
}

case "${1:-start}" in
  start)
    note "启动 lt (极简监督)"
    if ! start_lt; then
      fail "lt 启动失败"
      exit 1
    fi
    ok "lt started: $(cat $LT_URLFILE)"
    # 启动后台监督循环 — 只监控进程, 不测 URL
    nohup bash "$0" _loop >> "$ORCH_LOG" 2>&1 &
    echo $! > /tmp/tuixue_tunnels/supervisor.pid
    note "Supervisor PID: $(cat /tmp/tuixue_tunnels/supervisor.pid)"
    ;;
  _loop)
    while true; do
      sleep 30  # 每 30s 检查一次
      if ! is_lt_running; then
        fail "lt 进程死了, 等 30s 后重启 (避免疯狂重启)"
        sleep 30
        start_lt && ok "lt 重启成功: $(cat $LT_URLFILE 2>/dev/null)" || fail "lt 重启失败, 60s 后再试"
      fi
    done
    ;;
  stop)
    pkill -f "lt --port ${PORT}" 2>/dev/null
    [ -f /tmp/tuixue_tunnels/supervisor.pid ] && kill "$(cat /tmp/tuixue_tunnels/supervisor.pid)" 2>/dev/null
    note "stopped"
    ;;
  status)
    echo "═══ Tunnel Supervisor (极简模式) ═══"
    if is_lt_running; then
      echo "lt: ✓ pid=$(cat $LT_PIDFILE) url=$(cat $LT_URLFILE 2>/dev/null)"
    else
      echo "lt: ✗ dead"
    fi
    [ -f /tmp/tuixue_tunnels/supervisor.pid ] && kill -0 "$(cat /tmp/tuixue_tunnels/supervisor.pid)" 2>/dev/null && echo "supervisor: ✓ pid=$(cat /tmp/tuixue_tunnels/supervisor.pid)" || echo "supervisor: ✗"
    ;;
  *)
    echo "Usage: $0 {start|stop|status}"
    exit 1
    ;;
esac