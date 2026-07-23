#!/bin/bash
# cloudflared 临时隧道(避开 ngrok 跟 tuixue_v3 撞车)
# 用法: ./run_tunnel.sh  → 终端打印 trycloudflare.com 链接
set -e

PORT="${FLOW_PORT:-8810}"

# 检查 cloudflared 是否安装
if ! command -v cloudflared >/dev/null 2>&1; then
    echo "!! cloudflared not found"
    echo "   brew install cloudflared"
    exit 1
fi

# 检查 server 是否跑着
if ! nc -z 127.0.0.1 "${PORT}" 2>/dev/null; then
    echo "!! flow server 没起,请先另开终端跑: ./run.sh"
    exit 1
fi

echo ">>> cloudflared 临时隧道 (端口 ${PORT})"
exec cloudflared tunnel --url "http://localhost:${PORT}" --no-autoupdate