#!/usr/bin/env bash
# launch_server.sh — launchd 包装启动脚本 (R323, 2026-08-19)
# =====================================================================
# 根治 uvicorn workers=4 的 "残留 worker 占端口 → 新 master EADDRINUSE → 启动失败" 循环。
#
# 背景: master 死后, 4 个 worker fork 仍持 LISTEN fd (master 不会自动回收),
#   launchd KeepAlive 立即拉起新 master → 端口被占 → 退出码 1 → launchd 无限重试。
#   server_keepalive.sh 虽然能清端口, 但它要 3 次失败(90s)才动手, 比 launchd 慢。
#
# 修法: launchd 的 ProgramArguments 指向本脚本 (而非直接 python)。
#   每次拉起前先清干净 7799 残留, 保证 master 一定能绑上端口。
# =====================================================================
set -uo pipefail

PORT="${TUIXUE_PORT:-7799}"
PYTHON="/Users/kaikai/.hermes/hermes-agent/venv/bin/python3"
WORKDIR="/Users/kaikai/scripts"
PYTHONPATH="/Users/kaikai/scripts"

# 1) 清端口残留 (孤儿 worker 占着 7799 时新 master 必失败)
# R324 (2026-08-20): launchd kickstart -k 与 worker 退出有 race — master 死后 worker
#   fork 仍持 LISTEN fd, launchd 立刻拉新 master → lsof 时 master 已死但 worker 还没被 OS
#   回收 → 新 master 绑端口失败。改用"等端口空 → 再启"循环(最长 10s),确保旧 worker 全部
#   退干净。
for _i in 1 2 3 4 5 6 7 8 9 10; do
  STALE_PIDS=$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null || true)
  if [ -z "$STALE_PIDS" ]; then
    break
  fi
  # 只杀 python 的残留 (别误杀其它服务)
  for p in $STALE_PIDS; do
    _cmd=$(ps -o command= -p "$p" 2>/dev/null | grep -E "tuixue_v3.web.server" || true)
    if [ -n "$_cmd" ]; then
      kill -9 "$p" 2>/dev/null
    fi
  done
  sleep 1
done

# 2) 启动 server (exec 让信号直达 python)
cd "$WORKDIR"
export PYTHONPATH
exec "$PYTHON" -m tuixue_v3.web.server --host 0.0.0.0 --port "$PORT" --no-preheat
