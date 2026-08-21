#!/usr/bin/env bash
# server_keepalive.sh — server 进程级看门狗 (R100, 2026-08-12)
# =====================================================================
# 兜 launchd KeepAlive 补不到的"挂死未崩溃"场景: 进程活着但 event loop 卡死,
# launchd 不会重启, 用户访问全断。本脚本每 30s 探 127.0.0.1:7799/api/healthz,
# 3 连败 (≈90s 无响应) → launchctl kickstart -k 强制重启 server。
#
# 与 restart.sh 互斥: restart.sh 会 touch /tmp/tuixue_tunnels/server_maintenance,
# 本脚本检测到该文件新鲜 (≤180s) 则跳过本轮 — 避免"优雅重启中被看门狗强杀"撞车。
#
# launchd 守护: com.kaikai.tuixue.server-keepalive (KeepAlive=true)
# =====================================================================
set -uo pipefail

PORT="${TUIXUE_PORT:-7799}"
LABEL="com.kaikai.tuixue.server"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
BASE="http://127.0.0.1:${PORT}"
STATEDIR="/tmp/tuixue_tunnels"
mkdir -p "$STATEDIR"
LOG="$STATEDIR/server_keepalive.log"
MNT="$STATEDIR/server_maintenance"

now() { date "+%F %T"; }
# 只写文件不 echo — launchd StandardOutPath 已把 stdout 重定向到同 LOG,
# echo 会导致每行写两遍 (实测启动行 ×2)。
note() { echo "[$(now)] $*" >> "$LOG"; }

init_ts=$(date +%s)
INIT_GRACE=120   # 启动后 120s 静默, 避免与 launchd 重启 / restart.sh 撞车
FAIL=0
MAX_FAIL=3
INTERVAL=30

# R102-A (2026-08-14): kickstart 失败时不要静默, 主动清端口残留 + 重新 bootstrap
# 根因: uvicorn master 死后 4 个 worker fork 仍持 LISTEN fd, launchd 拉新 master 必 EADDRINUSE
# 之前 keepalive 只 note "kickstart 失败" 继续循环, 用户实际永久断线 (23:32-00:49 真实空窗)
_kickstart_with_recovery() {
  local label="$1"
  local plist="$2"
  # (1) 先清端口残留 (孤儿 worker 占着 7799 时 launchd 拉新必失败)
  local stale_pids=$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null)
  if [ -n "$stale_pids" ]; then
    note "  检测到 ${PORT} 残留 PID $stale_pids, kill -9 释放"
    kill -9 $stale_pids 2>/dev/null
    sleep 2
  fi
  # (2) kickstart (失败时不静默)
  if launchctl kickstart -k "gui/$(id -u)/$label" 2>>"$LOG"; then
    return 0
  fi
  note "  kickstart 失败, 试 bootstrap (service 可能被 bootout 过)"
  # (3) service 可能被 bootout 过, 重新 bootstrap
  if launchctl bootstrap "gui/$(id -u)" "$plist" 2>>"$LOG"; then
    sleep 2
    # bootstrap 不会自动启动 (RunAtLoad=false 时), 显式 kickstart 拉起
    launchctl kickstart "gui/$(id -u)/$label" 2>>"$LOG" || return 1
    return 0
  fi
  note "  bootstrap 也失败, 需人工排查"
  return 1
}

note "===== server_keepalive 启动, PORT=$PORT (探 $BASE/api/healthz) ====="

while true; do
  sleep "$INTERVAL"
  [ $(( $(date +%s) - init_ts )) -lt "$INIT_GRACE" ] && continue

  # restart.sh 维护窗口内跳过
  if [ -f "$MNT" ]; then
    if [ $(( $(date +%s) - $(stat -f %m "$MNT" 2>/dev/null || echo 0) )) -lt 180 ]; then
      FAIL=0
      continue
    fi
  fi

  if curl -s -o /dev/null --max-time 4 "$BASE/api/healthz"; then
    if [ "$FAIL" -ge 1 ]; then note "✓ healthz 恢复 (失败 $FAIL 次后)"; fi
    FAIL=0
  else
    FAIL=$((FAIL + 1))
    note "✗ healthz 失败 $FAIL/$MAX_FAIL"
    if [ "$FAIL" -ge "$MAX_FAIL" ]; then
      note "L3 自愈: kickstart -k $LABEL (server 挂死无响应)"
      _kickstart_with_recovery "$LABEL" "$PLIST"
      FAIL=0
      init_ts=$(date +%s)   # 重启后重新进入 grace 期
    fi
  fi
done
