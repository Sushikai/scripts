#!/usr/bin/env bash
# bore_url_sync.sh — DISABLED 2026-08-11 (R-FIX for mobile tunnel flap)
#
# 旧职责: 把 bore.pub 公网 URL 同步到 URL_FILE 兜底 ngrok 死链
# 旧 bug:   1) 每 10s 读 /tmp/tuixue_tunnels/bore-fixed.log 最后一行端口,
#              但 bore.pub 服务端会回收空闲端口 (老端口 11389/62813 都曾 dead),
#              → 持续把过期端口写回 URL_FILE → mobile probe 永远 000
#           2) 跟 bore_keepalive.sh (每秒探活 + 自启) 抢 URL_FILE 写入权,
#              两个脚本读各自的 bore 进程端口, 互不知道对方改了什么
# 新职责:   不再做任何事。bore_keepalive.sh 是唯一 URL_FILE writer。
#           如需恢复, 请确认 ngrok agent 协议层已恢复 (Akari ban 8/7 以来一直挂)
#           并重新评估 bore_url_sync 与 bore_keepalive 的协调机制。
#
# 启动项: com.kaikai.tuixue.bore-url-sync.plist 已加 Disabled=true
# 原文件备份: ${0}.bak.<日期> (R-FIX 当时由 git history / .bak.YYYYMMDD)

LOG="/tmp/tuixue_tunnels/bore_url_sync.log"
mkdir -p /tmp/tuixue_tunnels
echo "[$(date '+%H:%M:%S')] bore_url_sync.sh 已 DISABLED, 不写 URL_FILE; bore_keepalive 是唯一 writer" >> "$LOG"
sleep infinity