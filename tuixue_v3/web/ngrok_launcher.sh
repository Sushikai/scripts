#!/usr/bin/env bash
# ngrok_launcher.sh — launchd 入口，防 ERR_NGROK_334 会话冲突
#
# 问题: ngrok 被 kill/断网后，云端 session 需要 ~30s 才能释放。
# 如果 launchd KeepAlive 立即重启新进程，新进程会报 ERR_NGROK_334。
#
# 解决: 启动前先杀掉残留 ngrok，等 5s 让云端 session 释放，
# 再用 --pooling-enabled 兜底。

# 杀掉所有残留 ngrok（除了自己）
pkill -9 -f 'ngrok (http|start).*7799' 2>/dev/null || true
sleep 5

exec /opt/homebrew/bin/ngrok start tuixue \
  --traffic-policy-file /Users/kaikai/scripts/tuixue_v3/web/.tunnels/ngrok_policy.yml \
  --pooling-enabled \
  --log /tmp/tuixue_tunnels/ngrok.log \
  --log-level info
