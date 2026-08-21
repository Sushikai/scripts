#!/bin/bash
# flow 专属 cloudflared 隧道守护 — 完全独立于 tuixue 的 ngrok(7799)。
# 由 launchd(com.kaikai.flow.tunnel)常驻拉起:cloudflared 挂了自动重连,
# 429 限频时长退避,拿到 URL 自动写 tunnel_url.txt。只操作指向本端口的进程。
set -uo pipefail

cd "$(dirname "$0")"
PORT="${FLOW_PORT:-8810}"
LOG="$(pwd)/cloudflared.log"
URL_FILE="$(pwd)/tunnel_url.txt"
METHOD_FILE="$(pwd)/tunnel_method.txt"
SELF_PATTERN="cloudflared tunnel --url http://localhost:${PORT}"

# 1) 等 flow server 起来(最多 60s)
for _ in $(seq 1 30); do
    nc -z 127.0.0.1 "${PORT}" 2>/dev/null && break
    sleep 2
done

# 2) 清掉指向本端口的旧 cloudflared(绝不碰 tuixue 的 ngrok 或别的隧道)
pkill -f "${SELF_PATTERN}" 2>/dev/null
sleep 1
: > "${LOG}"

# 3) 前台持有 cloudflared,launchd 靠本脚本存活判断隧道存活
cloudflared tunnel --url "http://localhost:${PORT}" --no-autoupdate --protocol http2 >> "${LOG}" 2>&1 &
CF_PID=$!

# 4) 后台抓 URL 写文件(最多等 60s)
(
    for _ in $(seq 1 30); do
        URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "${LOG}" | head -1)
        if [ -n "${URL}" ]; then
            echo "${URL}" > "${URL_FILE}"
            echo cloudflared > "${METHOD_FILE}"
            break
        fi
        sleep 2
    done
) &

# 5) 阻塞直到 cloudflared 退出
wait "${CF_PID}"

# 6) 退出前判断是否限频 → 决定退避时长,再让 launchd 重启
if grep -qE '429|1015' "${LOG}"; then
    sleep 180   # 被 Cloudflare 限频,长退避避免越冲越久
fi
exit 1
