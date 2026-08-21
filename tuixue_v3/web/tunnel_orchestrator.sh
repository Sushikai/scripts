#!/usr/bin/env bash
# tunnel_orchestrator.sh v3 — 激进快速 lt 重启 (≤10s 恢复)
#
# 2026-07-25 R4 优化:
#   - 启动 lt 后等 URL 只 sleep 0.5s (不是 1s)
#   - 检测到失败立即重启 (不等 2 次失败)
#   - 加进程探活: lt --port 每 3s check (不是 15s)
#   - 重启时先 pkill 旧的 (不等 1s)
#
# 目标: lt 死了 10s 内恢复, 60min 内 ≥99% 可用

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-7799}"
LOG_DIR="/tmp/tuixue_tunnels"
mkdir -p "$LOG_DIR"

URL_FILE="$ROOT/tunnel_url.txt"
HEALTH_FILE="$LOG_DIR/health.json"
UPTIME_LOG="$LOG_DIR/uptime.log"
ORCH_LOG="$LOG_DIR/orchestrator.log"

note()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$ORCH_LOG"; }
fail()  { note "✗ $*"; }
ok()    { note "✓ $*"; }

UA="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
HEALTH_INTERVAL=3

LT_PIDFILE="/tmp/tuixue_tunnels/lt.pid"
LT_LOGFILE="/tmp/tuixue_tunnels/lt.log"
LT_URLFILE="/tmp/tuixue_tunnels/lt_url.txt"

is_server_up() {
  curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/" --max-time 2 2>/dev/null | grep -q "^200$"
}

is_lt_running() {
  [ -f "$LT_PIDFILE" ] && kill -0 "$(cat $LT_PIDFILE)" 2>/dev/null
}

# 快速启动 lt — 优化: 用更短的 sleep 轮询
start_lt_fast() {
  pkill -f "lt --port ${PORT}" 2>/dev/null || true
  rm -f "$LT_PIDFILE" "$LT_URLFILE"
  nohup /opt/homebrew/bin/lt --port "${PORT}" > "$LT_LOGFILE" 2>&1 &
  local pid=$!
  echo $pid > "$LT_PIDFILE"

  # 等 URL — 0.5s 间隔, 最多 20 次 = 10s
  for i in $(seq 1 20); do
    sleep 0.5
    url=$(grep -oE "https://[a-z-]+\.loca\.lt" "$LT_LOGFILE" 2>/dev/null | head -1)
    if [ -n "$url" ]; then
      echo "$url" > "$LT_URLFILE"
      return 0
    fi
  done
  return 1
}

get_lt_url() {
  cat "$LT_URLFILE" 2>/dev/null
}

test_url() {
  local url="$1"
  local start=$(date +%s.%N)
  local code=$(curl -s -o /dev/null -w "%{http_code}" -A "$UA" --max-time 6 "$url/" 2>/dev/null || echo "000")
  local end=$(date +%s.%N)
  local latency=$(echo "$end - $start" | bc 2>/dev/null || echo "0")
  echo "${code}|${latency}"
}

health_check_loop() {
  note "健康检查循环 v3 启动 (interval=${HEALTH_INTERVAL}s, 激进快速)"
  local lan_ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "127.0.0.1")
  local lan_url="http://${lan_ip}:${PORT}"

  while true; do
    local best_url="$lan_url"
    local best_name="lan"
    local best_latency=99999
    local best_code="200"
    local action="none"

    # 检查 lt 进程
    if ! is_lt_running; then
      fail "lt 进程死了, 立即重启"
      start_lt_fast
      action="restart_dead"
    fi

    # 检查 URL 可达
    local url=$(get_lt_url)
    local lt_ok=0
    local test_latency=999
    if [ -n "$url" ]; then
      local result=$(test_url "$url")
      local code="${result%|*}"
      local latency="${result#*|}"
      if [ "$code" = "200" ]; then
        lt_ok=1
        test_latency=${latency%.*}
        test_latency=${test_latency:-999}
        best_name="lt"
        best_url="$url"
        best_latency=$test_latency
        best_code="200"
      else
        # lt 进程在但 URL 不可达 — 立即重启
        if [ "$action" = "none" ]; then
          fail "lt URL 不可达 ($code), 立即重启"
          start_lt_fast
          action="restart_unreachable"
          # 立即重测
          url=$(get_lt_url)
          if [ -n "$url" ]; then
            result=$(test_url "$url")
            code="${result%|*}"
            latency="${result#*|}"
            if [ "$code" = "200" ]; then
              lt_ok=1
              test_latency=${latency%.*}
              test_latency=${test_latency:-999}
              best_name="lt"
              best_url="$url"
              best_latency=$test_latency
              best_code="200"
            fi
          fi
        fi
      fi
    else
      # 没 URL (lt 没起来)
      if [ "$action" = "none" ]; then
        fail "lt URL 为空, 启动"
        start_lt_fast
        action="start_empty"
        url=$(get_lt_url)
        if [ -n "$url" ]; then
          result=$(test_url "$url")
          code="${result%|*}"
          if [ "$code" = "200" ]; then
            lt_ok=1
            latency="${result#*|}"
            test_latency=${latency%.*}
            test_latency=${test_latency:-999}
            best_name="lt"
            best_url="$url"
            best_latency=$test_latency
            best_code="200"
          fi
        fi
      fi
    fi

    # ALWAYS write URL_FILE
    echo "$best_url" > "$URL_FILE"

    # write JSON health
    local lt_url=$(get_lt_url)
    local lt_alive=$(is_lt_running && echo true || echo false)
    cat > "$HEALTH_FILE" <<EOF
{
  "ts": $(date +%s),
  "best": "${best_name}",
  "url": "${best_url}",
  "latency_ms": $((best_latency * 1000)),
  "code": "${best_code}",
  "lan_fallback": "${lan_url}",
  "action": "${action}",
  "candidates": {
    "lt": "${lt_url}",
    "lt_alive": ${lt_alive}
  }
}
EOF
    sleep $HEALTH_INTERVAL
  done
}

uptime_loop() {
  while true; do
    sleep 60
    local url=$(cat "$URL_FILE" 2>/dev/null)
    if [ -n "$url" ]; then
      local result=$(test_url "$url")
      local code="${result%|*}"
      local latency="${result#*|}"
      local minute=$(date '+%Y-%m-%d %H:%M')
      local now=$(date +%s)
      echo "${minute}|${now}|${code}|${latency}|${url}" >> "$UPTIME_LOG"
    else
      echo "$(date '+%Y-%m-%d %H:%M')|$(date +%s)|NO_URL|0|none" >> "$UPTIME_LOG"
    fi
  done
}

# Watchdog: 监控 orchestrator 自身不死
watchdog_loop() {
  while true; do
    sleep 30
    if [ -f /tmp/tuixue_tunnels/health.pid ]; then
      local hpid=$(cat /tmp/tuixue_tunnels/health.pid)
      if ! kill -0 "$hpid" 2>/dev/null; then
        fail "健康检查 PID ${hpid} 死了, 重启"
        nohup bash "$0" _health_loop >> "$ORCH_LOG" 2>&1 &
        echo $! > /tmp/tuixue_tunnels/health.pid
      fi
    fi
    if [ -f /tmp/tuixue_tunnels/uptime.pid ]; then
      local upid=$(cat /tmp/tuixue_tunnels/uptime.pid)
      if ! kill -0 "$upid" 2>/dev/null; then
        fail "Uptime loop PID ${upid} 死了, 重启"
        nohup bash "$0" _uptime_loop >> "$ORCH_LOG" 2>&1 &
        echo $! > /tmp/tuixue_tunnels/uptime.pid
      fi
    fi
  done
}

# ─── main ───
case "${1:-start}" in
  start)
    if ! is_server_up; then
      fail "Server ${PORT} 不通"
      exit 1
    fi
    note "启动 lt (v3 激进模式)..."
    start_lt_fast && ok "lt started: $(get_lt_url)" || fail "lt 启动失败"

    nohup bash "$0" _health_loop >> "$ORCH_LOG" 2>&1 &
    echo $! > /tmp/tuixue_tunnels/health.pid
    nohup bash "$0" _uptime_loop >> "$ORCH_LOG" 2>&1 &
    echo $! > /tmp/tuixue_tunnels/uptime.pid
    nohup bash "$0" _watchdog >> "$ORCH_LOG" 2>&1 &
    echo $! > /tmp/tuixue_tunnels/watchdog.pid

    sleep 6
    note "当前最优: $(cat $URL_FILE 2>/dev/null || echo none)"
    note "健康检查 PID: $(cat /tmp/tuixue_tunnels/health.pid)"
    note "Uptime loop PID: $(cat /tmp/tuixue_tunnels/uptime.pid)"
    note "Watchdog PID: $(cat /tmp/tuixue_tunnels/watchdog.pid)"
    ;;
  _health_loop) health_check_loop ;;
  _uptime_loop) uptime_loop ;;
  _watchdog) watchdog_loop ;;
  stop)
    pkill -f "lt --port ${PORT}" 2>/dev/null
    for f in health.pid uptime.pid watchdog.pid; do
      [ -f /tmp/tuixue_tunnels/$f ] && kill "$(cat /tmp/tuixue_tunnels/$f)" 2>/dev/null
    done
    note "stopped"
    ;;
  status)
    echo "═══ Tunnel Status (v3) ═══"
    echo "Best URL: $(cat $URL_FILE 2>/dev/null || echo none)"
    echo ""
    if [ -f "$HEALTH_FILE" ]; then
      cat "$HEALTH_FILE" | python3 -m json.tool 2>/dev/null || cat "$HEALTH_FILE"
    fi
    echo ""
    echo "Processes:"
    echo -n "  lt: "; if is_lt_running; then echo "✓ pid=$(cat $LT_PIDFILE) url=$(get_lt_url)"; else echo "✗"; fi
    echo -n "  health: "; if [ -f /tmp/tuixue_tunnels/health.pid ] && kill -0 "$(cat /tmp/tuixue_tunnels/health.pid)" 2>/dev/null; then echo "✓ pid=$(cat /tmp/tuixue_tunnels/health.pid)"; else echo "✗"; fi
    echo -n "  uptime: "; if [ -f /tmp/tuixue_tunnels/uptime.pid ] && kill -0 "$(cat /tmp/tuixue_tunnels/uptime.pid)" 2>/dev/null; then echo "✓ pid=$(cat /tmp/tuixue_tunnels/uptime.pid)"; else echo "✗"; fi
    echo -n "  watchdog: "; if [ -f /tmp/tuixue_tunnels/watchdog.pid ] && kill -0 "$(cat /tmp/tuixue_tunnels/watchdog.pid)" 2>/dev/null; then echo "✓ pid=$(cat /tmp/tuixue_tunnels/watchdog.pid)"; else echo "✗"; fi
    echo ""
    echo "Last 20 uptime entries:"
    tail -20 "$UPTIME_LOG" 2>/dev/null
    echo ""
    total=$(wc -l < "$UPTIME_LOG" 2>/dev/null || echo 0)
    ok=$(grep -c "^[^|]*|[^|]*|200|" "$UPTIME_LOG" 2>/dev/null || echo 0)
    if [ "$total" -gt 0 ]; then
      pct=$(echo "scale=1; $ok * 100 / $total" | bc 2>/dev/null)
      echo "Uptime: ${ok}/${total} = ${pct}%"
    fi
    ;;
  test)
    url=$(cat $URL_FILE 2>/dev/null)
    if [ -z "$url" ]; then echo "no URL"; exit 1; fi
    echo "URL: $url"
    echo "Server direct: $(curl -s -o /dev/null -w "%{http_code} %{time_total}s" http://127.0.0.1:${PORT}/ --max-time 3)"
    echo "Tunnel /: $(curl -s -o /dev/null -w "%{http_code} %{time_total}s %{size_download}B" -A "$UA" "$url/" --max-time 10)"
    echo "Tunnel /api/dashboard/signal: $(curl -s -o /dev/null -w "%{http_code} %{time_total}s" -A "$UA" "$url/api/dashboard/signal" --max-time 15)"
    echo "Tunnel /api/all_stocks/board: $(curl -s -o /dev/null -w "%{http_code} %{time_total}s" -A "$UA" "$url/api/all_stocks/board" --max-time 30)"
    ;;
  *)
    echo "Usage: $0 {start|stop|status|test}"
    exit 1
    ;;
esac