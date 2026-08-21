#!/usr/bin/env bash
# restart.sh — 优雅重启 + 就绪门 (R100, 2026-08-12; R102-A 加固 2026-08-14)
# =====================================================================
# 为什么不用 `launchctl kickstart -k` (之前改代码后数据断的根因):
#   kickstart -k 是硬杀 → 4 个 uvicorn worker 在途请求直接 RST;
#   新进程立刻对外服务但内存缓存空, 后台预热 25-35s 内首请求冷算 12-21s → "数据断"。
# 本脚本:
#   1) SIGTERM master → uvicorn 排干在途请求 (优雅, 15s grace)
#   2) launchd KeepAlive=true 自动拉起新实例 (不是 launchctl stop, stop 会抑制重启)
#   3) 等 /api/healthz (进程活) → 等 /api/ready (关键缓存温) → smoke 探慢端点
#   4) 报告耗时 + 失败时吐 server 日志尾部
# 维护窗口标记: 写 /tmp/tuixue_tunnels/server_maintenance, 让 server_keepalive.sh 别 kickstart 撞车。
#
# R102-A (2026-08-14) 加固:
#   - grace 60s → 15s (uvicorn 默认 graceful 5s 内完成)
#   - master 强杀前显式 kill -9 7799 残留 worker (孤儿 fork 占 LISTEN)
#   - 不再 `|| true` 吞 kickstart 失败, 失败 → bootstrap 兜底
# =====================================================================
set -uo pipefail

PORT="${TUIXUE_PORT:-7799}"
LABEL="com.kaikai.tuixue.server"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
BASE="http://127.0.0.1:${PORT}"
STATEDIR="/tmp/tuixue_tunnels"
mkdir -p "$STATEDIR"
LOG="$STATEDIR/restart.log"
ERR_LOG="/tmp/tuixue_server.err"

now() { date "+%F %T"; }
note() { echo "[$(now)] $*"; echo "[$(now)] $*" >> "$LOG"; }
alive() { curl -s -o /dev/null --max-time 4 "$BASE/api/healthz"; }

# R102-A: kill 7799 LISTEN 残留 worker (孤儿 fork 占端口), 不抢 launchd 重启节奏
_kill_port_residual() {
  local stale_pids=$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null)
  if [ -n "$stale_pids" ]; then
    note "  强杀 ${PORT} 残留 PID $stale_pids (孤儿 worker 兜底)"
    kill -9 $stale_pids 2>/dev/null
    sleep 2
  fi
}

# R102-A: kickstart 失败时 → bootstrap 兜底 (service 被 bootout 过会 kickstart 报 501)
_kickstart_or_bootstrap() {
  if launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>>"$LOG"; then
    return 0
  fi
  note "  kickstart 失败, 试 bootstrap 兜底"
  if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>>"$LOG"; then
    sleep 2
    # bootstrap 不会自动启动, 显式 kickstart 拉起
    launchctl kickstart "gui/$(id -u)/$LABEL" 2>>"$LOG" || return 1
    return 0
  fi
  note "  bootstrap 也失败, 需人工排查"
  return 1
}

# ── 维护窗口标记 (server_keepalive 检测后跳过本轮 kickstart) ──
touch "$STATEDIR/server_maintenance"

# ── 0) 当前是否活着 ──
if alive; then
  # ── 1) 优雅 SIGTERM master ──
  MASTER_PID=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | awk '/pid =/{print $3; exit}')
  if [ -z "$MASTER_PID" ]; then
    note "找不到 master pid, 走 kickstart 兜底"
    _kill_port_residual
    _kickstart_or_bootstrap || { note "kickstart 失败"; exit 1; }
  else
    note "优雅 SIGTERM master pid=$MASTER_PID (uvicorn 排干在途请求)"
    kill -TERM "$MASTER_PID" 2>>"$LOG"
    GRACE=15
    for _i in $(seq 1 $GRACE); do
      kill -0 "$MASTER_PID" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$MASTER_PID" 2>/dev/null; then
      note "${GRACE}s 未退出, 强杀端口残留 + kickstart 兜底"
      _kill_port_residual
      _kickstart_or_bootstrap || note "  kickstart 失败, 但 master 已被 SIGTERM, 等 launchd KeepAlive"
    else
      note "master 已退出, launchd KeepAlive 拉起新实例"
      _kill_port_residual
    fi
  fi
else
  note "当前 /api/healthz 不通, 清端口残留 + kickstart 拉起"
  _kill_port_residual
  _kickstart_or_bootstrap || { note "kickstart 失败"; exit 1; }
fi

# ── 2) 等 healthz (进程活, 最多 120s) ──
note "等待 healthz (进程活)..."
for _i in $(seq 1 40); do
  alive && break
  sleep 3
done
if ! alive; then
  note "✗ 120s 后 healthz 仍不通 — server 日志尾部:"
  tail -n 30 "$ERR_LOG" 2>/dev/null | sed 's/^/    /' | tee -a "$LOG"
  rm -f "$STATEDIR/server_maintenance"
  exit 1
fi
note "✓ healthz 通"

# ── 3) 等 /api/ready (关键缓存温, 最多 120s) ──
note "等待 /api/ready (dashboard/index_trend/dexin 预热)..."
READY=0
for _i in $(seq 1 40); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "$BASE/api/ready")" = "200" ] && { READY=1; break; }
  sleep 3
done
[ "$READY" = "1" ] && note "✓ /api/ready 就绪" || note "⚠ 120s 仍未就绪 — 服务可访问, 继续 smoke 探"

# ── 4) smoke 探慢端点 — 应已从缓存秒回 (非冷算) ──
note "smoke 探慢端点 (应 <3s)..."
OK_ALL=1
for path in "/api/dashboard/signal" "/api/dashboard/index_trend?period=day" "/api/dexin/screen"; do
  t0=$(date +%s)
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$BASE$path")
  took=$(( $(date +%s) - t0 ))
  note "  $path → HTTP $code (${took}s)"
  [ "$code" = "200" ] || OK_ALL=0
done

rm -f "$STATEDIR/server_maintenance"
if [ "$OK_ALL" = "1" ]; then
  note "✓ 重启完成, 服务就绪"
else
  note "⚠ 部分 smoke 未 200, 见上"
fi
