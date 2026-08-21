#!/usr/bin/env bash
# cloudflared launcher — 解析 trycloudflare URL → 写入 tunnel_url.txt
# launchd 管理，KeepAlive=true 确保断了自动重连
#
# R-fix-2026-08-07 03:50: 已 DISABLE 7799 的 cloudflared 守护
#   原因: 与 ngrok 抢 tunnel_url.txt 第一行,导致 mobile_link_keepalive
#         探测死循环(杀 → 重启 → 新域名 → 探测失败 → 杀 → ...)
#   现在: ngrok 独占 7799 公网,cloudflared 只保 8810 (flow 项目)
#   此脚本保留但 launchd plist 已 unload,不要重新 load
#   (如要恢复,把 plist load 回来即可,但要先修 mobile_link_keepalive 的多源策略)
LOG="/tmp/tuixue_tunnels/cloudflared.log"
URL_FILE="/Users/kaikai/scripts/tuixue_v3/tunnel_url.txt"
LAN_URL="http://$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1):7799"

# 清理旧日志
:> "$LOG"

# 启动 cloudflared，tee 到日志同时解析 URL
# 2026-08-04: 必须用 127.0.0.1 而非 localhost — localhost 在 macOS 优先解析成 IPv6 [::1],
# 而 server 只 bind IPv4 (*:7799),cloudflared 会一直 502 "connection refused dial tcp [::1]:7799"
/opt/homebrew/bin/cloudflared tunnel --url http://127.0.0.1:7799 --no-autoupdate 2>&1 | while IFS= read -r line; do
  echo "$line" >> "$LOG"
  # 匹配 trycloudflare URL
  # 2026-08-04: 必须排除 api.trycloudflare.com — 那是 cloudflared 的控制面地址,
  # 失败日志 (failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": EOF)
  # 也会被旧正则捕获, 把控制面地址当隧道 URL 写进 tunnel_url.txt, 手机打开就是死链。
  if [[ "$line" =~ (https://[a-zA-Z0-9_-]+\.trycloudflare\.com) ]]; then
    cf_url="${BASH_REMATCH[1]}"
    if [[ "$cf_url" == "https://api.trycloudflare.com" ]]; then
      echo "[$(date '+%H:%M:%S')] 跳过控制面地址 (非隧道 URL): $cf_url" >> "$LOG"
      continue
    fi
    # 写入前先探活 — 隧道刚注册需几秒,最多等 20s;不通就不覆盖 tunnel_url.txt
    ok=""
    for _ in $(seq 1 10); do
      sleep 2
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$cf_url/api/health" 2>/dev/null)
      if [ "$code" = "200" ]; then ok="1"; break; fi
    done
    if [ -z "$ok" ]; then
      echo "[$(date '+%H:%M:%S')] 隧道探活失败 (最后 code=$code), 保留原 tunnel_url.txt: $cf_url" >> "$LOG"
      continue
    fi
    printf '%s\n' "$cf_url" "$LAN_URL" > "$URL_FILE"
    echo "cloudflared" > "/Users/kaikai/scripts/tuixue_v3/tunnel_method.txt"
    echo "[$(date '+%H:%M:%S')] CF URL (已探活): $cf_url" >> "$LOG"

    # 推送 TG 通知
    if [ -f "$HOME/.hermes/env.sh" ]; then
      source "$HOME/.hermes/env.sh" 2>/dev/null
      PYTHONPATH="/Users/kaikai/scripts" python3 -c "
from tuixue_v3.lib_common import send_telegram
send_telegram('tuixue_v3 cloudflared: $cf_url\nLAN: $LAN_URL', parse_mode='', silent=True)
" 2>/dev/null &
    fi
  fi
done
