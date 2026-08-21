#!/usr/bin/env bash
# bore-fixed.sh — bore-fixed launchd 的实际入口 (R-FIX 2026-08-10)
#
# 问题: 原 plist 直接调 /tmp/bore,但 /tmp 会被 macOS 清空
#       → bore 二进制丢失 → launchd 静默退出 -9
# 解决: 这里多路径查找 (类似 bore_keepalive.sh),并配合 launchd KeepAlive 自愈
set -u

# 多路径搜索 bore 二进制
BORE_BIN=""
for cand in \
  "$HOME/.local/bin/bore" \
  "/opt/homebrew/bin/bore" \
  "/usr/local/bin/bore" \
  "/tmp/bore"; do
  if [ -x "$cand" ]; then BORE_BIN="$cand"; break; fi
done
if [ -z "$BORE_BIN" ]; then
  echo "bore 二进制未找到" >&2
  exit 1
fi

exec "$BORE_BIN" local 7799 --to bore.pub