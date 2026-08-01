#!/bin/bash
# R-fix-2026-08-01: launchd-managed tuixue server log rotation
# launchd 持有 fd 写入原 inode, mv 后新写入仍走旧 inode (但旧 inode 已改名)。
# 通过 restart 服务 让 launchd 重新打开 标准流 fd 到 fresh 文件。
set -e
TS=$(date +%Y%m%d_%H%M)
LOG="/tmp/tuixue_server.log"
ERR="/tmp/tuixue_server.err"

# 1. 移动旧 log → 加时间戳
if [ -f "$LOG" ] && [ -s "$LOG" ]; then
  mv "$LOG" "/tmp/tuixue_server.log.${TS}"
  gzip "/tmp/tuixue_server.log.${TS}" 2>/dev/null || true
fi
if [ -f "$ERR" ] && [ -s "$ERR" ]; then
  mv "$ERR" "/tmp/tuixue_server.err.${TS}"
  gzip "/tmp/tuixue_server.err.${TS}" 2>/dev/null || true
fi

# 2. 删 30 天前的
find /tmp -maxdepth 1 -name "tuixue_server.log.*" -mtime +30 -delete 2>/dev/null || true
find /tmp -maxdepth 1 -name "tuixue_server.err.*" -mtime +30 -delete 2>/dev/null || true

# 3. kickstart 让 launchd 重启 server (新 fd 写到 fresh 文件)
launchctl kickstart -k "gui/$(id -u)/com.kaikai.tuixue.server" 2>&1 || true
