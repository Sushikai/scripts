#!/usr/bin/env bash
# start_remote.sh — 启动 FastAPI + Cloudflare 远程隧道（Quick Tunnel）
# 流程：清理旧进程 → 启动 server → 启动 cloudflared（最多 3 次重试）→
#       解析远程 URL → 推送到 Telegram → 等待 SIGINT
#
# 用法:  bash start_remote.sh
# 退出:  Ctrl+C（同时结束 server 和 cloudflared）
# 注意：不用 set -u（Mac bash 3.2 + pipe + 空输出会触发奇怪的 unbound 报错）
# kill / curl 等失败用 || true 容错
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
PORT="${PORT:-7799}"

# ─── 加载环境变量（MINIMAX_API_KEY / TELEGRAM_BOT_TOKEN 等）───
[ -f "$HOME/.hermes/env.sh" ] && source "$HOME/.hermes/env.sh"

# ─── 清理旧进程 ───
echo "→ 清理旧进程 …"
lsof -ti ":$PORT" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
pkill -f "cloudflared tunnel --url http://localhost:$PORT" 2>/dev/null || true
sleep 1

# ─── 启动 FastAPI ───
echo "→ 启动 FastAPI 控制台（端口 $PORT）…"
python -m tuixue_v3.web.server --host 0.0.0.0 --port "$PORT" \
    > /tmp/tuixue_server.log 2>&1 &
SERVER_PID=$!

# 健康检查（最长 10s）
for i in $(seq 1 10); do
    sleep 1
    if curl -s --max-time 2 "http://localhost:$PORT/api/health" > /dev/null 2>&1; then
        echo "  ✓ server up (pid=$SERVER_PID)"
        break
    fi
    if [ "$i" = "10" ]; then
        echo "  ✗ server failed; tail of /tmp/tuixue_server.log:"
        tail -20 /tmp/tuixue_server.log
        kill -9 "$SERVER_PID" 2>/dev/null || true
        exit 1
    fi
done

# ─── 启动 cloudflared 隧道（带重试） ───
TUNNEL_URL=""
TUNNEL_PID=""

start_tunnel() {
    : > /tmp/cloudflared.log
    cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate \
        >> /tmp/cloudflared.log 2>&1 &
    TUNNEL_PID=$!
}

stop_tunnel() {
    if [ -n "${TUNNEL_PID:-}" ] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
        kill -9 "$TUNNEL_PID" 2>/dev/null || true
    fi
    pkill -f "cloudflared tunnel --url http://localhost:$PORT" 2>/dev/null || true
}

for attempt in 1 2 3; do
    echo "→ 启动 cloudflared 隧道（尝试 $attempt/3）…"
    start_tunnel

    URL=""
    for i in $(seq 1 30); do
        sleep 1
        # 关键：只从 "Your quick Tunnel has been created" 行之后捕获 URL
        # 避免抓到 POST https://api.trycloudflare.com/tunnel 这种请求地址
        URL=$(awk '/Your quick Tunnel has been created/{flag=1} flag' /tmp/cloudflared.log \
            | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | head -1 || true)
        if [ -n "$URL" ]; then
            break
        fi
        # cloudflared 进程已退出 → 本次失败
        if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
            break
        fi
    done

    if [ -n "$URL" ]; then
        TUNNEL_URL="$URL"
        echo "  ✓ 隧道 URL: $TUNNEL_URL"
        break
    fi

    echo "  ✗ 尝试 $attempt 失败（30s 内未拿到 URL）"
    stop_tunnel
    sleep 2
done

# ─── 打印最终状态 ───
echo ""
echo "════════════════════════════════════════════════════════"
echo "  本机访问  http://localhost:$PORT"
if [ -n "$TUNNEL_URL" ]; then
    echo "  远程访问  $TUNNEL_URL"
else
    echo "  ⚠ 远程 URL 未生成（cloudflared 三次重试都失败）"
    echo "     仍可本地/局域网访问 http://<mac-lan-ip>:$PORT"
fi
echo "════════════════════════════════════════════════════════"
echo ""
echo "日志:"
echo "  server  tail -f /tmp/tuixue_server.log"
echo "  tunnel  tail -f /tmp/cloudflared.log"
echo "退出: Ctrl+C（两个进程都会结束）"

# ─── 推 TG（成功 / 失败两种都推，失败时告知用户怎么救场） ───
TS=$(date '+%Y-%m-%d %H:%M:%S')
send_tg() {
    python -c "from tuixue_v3.lib_common import send_telegram; send_telegram('''$1''', parse_mode='', silent=True)" 2>/dev/null
}

if [ -n "$TUNNEL_URL" ]; then
    TG_MSG="🟢 退学 v3 控制台已上线

📡 本机:   http://localhost:$PORT
🌐 远程:   $TUNNEL_URL
⏰ $TS

iPhone 浏览器直接打开远程 URL 即可。每天 8 AM 自动重启。
⚠️ 临时隧道约 24h 后失效，届时自动重启会换新 URL。"

    if send_tg "$TG_MSG"; then
        echo "  ✓ 已推送到 TG"
    else
        echo "  ⚠ TG 推送失败（检查 ~/.hermes/.env 里的 TELEGRAM_BOT_TOKEN）"
    fi
else
    TG_MSG="⚠️ 退学 v3 控制台：cloudflared 隧道建立失败
📡 本机: http://localhost:$PORT
⏰ $TS
本机服务仍可访问，重试: bash web/start_remote.sh"

    if send_tg "$TG_MSG"; then
        echo "  ✓ 已推送失败通知到 TG"
    else
        echo "  ⚠ TG 推送失败（检查 ~/.hermes/.env 里的 TELEGRAM_BOT_TOKEN）"
    fi
fi

# ─── 退出即清理 ───
trap "echo ''; echo '→ 关闭中 …'; kill $SERVER_PID ${TUNNEL_PID:-} 2>/dev/null; exit" INT TERM
wait $SERVER_PID 2>/dev/null
