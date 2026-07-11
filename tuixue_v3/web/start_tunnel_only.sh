#!/usr/bin/env bash
# start_tunnel_only.sh — server-safe 多路隧道 fallback
#
# 与 start_remote.sh 区别:本脚本只管 tunnel 进程,不碰 server。
# 调用场景: server 已在运行, 用户点「启动隧道」按钮 → 后端 spawn 这个脚本。
# 输出:
#   $ROOT/tunnel_url.txt    — 公网 URL (一行)
#   $ROOT/tunnel_method.txt — 机制名 (cloudflare-quic / ngrok / lhr / serveo ...)
#   $ROOT/tunnel_pid.txt    — 后台 tunnel 进程 PID
#   /tmp/tuixue_tunnels/*.log — 每路 tunnel 自己的日志
#
# 6 路 fallback (按可信度排序):
#   1) cloudflared QUIC
#   2) cloudflared HTTP2
#   3) cloudflared IPv4
#   4) ngrok (authtoken 已配)
#   5) localhost.run (ssh)
#   6) serveo.net   (ssh)
#
# 用法:  bash web/start_tunnel_only.sh
# 退出:  脚本 60s 内拿到 URL → 写文件 → 退出 0
#        全失败 → exit 1
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="${PORT:-7799}"
TUNNELS_DIR="/tmp/tuixue_tunnels"
mkdir -p "$TUNNELS_DIR"
URL_FILE="$ROOT/tunnel_url.txt"
METHOD_FILE="$ROOT/tunnel_method.txt"
PID_FILE="$ROOT/tunnel_pid.txt"
LOG_DIR="$TUNNELS_DIR"

note()  { echo "  [tunnel] $*"; }
ok()    { echo "  ✓ $*"; }
fail()  { echo "  ✗ $*"; }

# ─── 清理上一轮残留 ───
# 杀旧 tunnel 进程（不动 server）
for pat in "cloudflared tunnel --url" "ngrok http $PORT" "ssh -tt -R 80:localhost:$PORT"; do
    pkill -f "$pat" 2>/dev/null || true
done
sleep 1
rm -f "$URL_FILE" "$METHOD_FILE" "$PID_FILE"

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || echo "0.0.0.0")
TS() { date '+%Y-%m-%d %H:%M:%S'; }

TUNNEL_PID=""

# 简单自检: /api/health 200 + body 含 ok=true
self_check() {
    local url="$1"
    local code body
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 "$url/api/health" 2>/dev/null || echo "000")
    body=$(curl -s --max-time 6 "$url/api/health" 2>/dev/null || echo "")
    if [ "$code" = "200" ] && echo "$body" | grep -qE '"ok":\s*true|"status":\s*"ok"'; then
        return 0
    fi
    return 1
}

# ─── try functions ───
try_cloudflared_quic() {
    local logfile="$LOG_DIR/cf_quic.log"
    : > "$logfile"
    cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate \
        >> "$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 25); do
        sleep 1
        local url
        url=$(awk '/Your quick Tunnel has been created/{flag=1} flag' "$logfile" \
            | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | head -1)
        [ -n "$url" ] && { echo "$url"; return 0; }
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

try_cloudflared_http2() {
    local logfile="$LOG_DIR/cf_http2.log"
    : > "$logfile"
    cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate --protocol http2 \
        >> "$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 25); do
        sleep 1
        local url
        url=$(awk '/Your quick Tunnel has been created/{flag=1} flag' "$logfile" \
            | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | head -1)
        [ -n "$url" ] && { echo "$url"; return 0; }
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

try_cloudflared_v4() {
    local logfile="$LOG_DIR/cf_v4.log"
    : > "$logfile"
    cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate --edge-ip-version 4 \
        >> "$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 25); do
        sleep 1
        local url
        url=$(awk '/Your quick Tunnel has been created/{flag=1} flag' "$logfile" \
            | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | head -1)
        [ -n "$url" ] && { echo "$url"; return 0; }
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

try_ngrok() {
    command -v ngrok > /dev/null || return 1
    local logfile="$LOG_DIR/ngrok.log"
    : > "$logfile"
    # 先杀可能残留的 ngrok agent
    pkill -f "ngrok start" 2>/dev/null || true
    sleep 1
    ngrok http "$PORT" --log "$logfile" --log-level=info > /dev/null 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 25); do
        sleep 1
        local url
        url=$(curl -s --max-time 2 http://127.0.0.1:4040/api/tunnels 2>/dev/null \
            | python3 -c "import json,sys;d=json.load(sys.stdin);t=d.get('tunnels',[]);print(t[0]['public_url'] if t else '')" 2>/dev/null)
        [ -n "$url" ] && [[ "$url" == https://* ]] && { echo "$url"; return 0; }
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

try_localhost_run() {
    local logfile="$LOG_DIR/lhr.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 80:localhost:$PORT nokey@localhost.run \
        >> "$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 40); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+\.lhr\.life" "$logfile" 2>/dev/null | head -1)
        [ -n "$url" ] && { echo "$url"; return 0; }
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

try_serveo() {
    local logfile="$LOG_DIR/serveo.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 80:localhost:$PORT serveo.net \
        >> "$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 40); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+(-[0-9]+(-[0-9]+(-[0-9]+(-[0-9]+)?)?)?)\.serveousercontent\.com" "$logfile" 2>/dev/null | head -1)
        [ -z "$url" ] && url=$(grep -oE "Forwarding HTTP traffic from https?://[^[:space:]]+" "$logfile" 2>/dev/null | grep -oE "https?://[^[:space:]]+" | head -1)
        [ -n "$url" ] && { echo "$url"; return 0; }
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

# ─── 6 路 fallback ───
declare -a METHODS=(
    "cloudflare-quic:try_cloudflared_quic"
    "cloudflare-http2:try_cloudflared_http2"
    "cloudflare-ipv4:try_cloudflared_v4"
    "ngrok:try_ngrok"
    "localhost-run:try_localhost_run"
    "serveo:try_serveo"
)

echo "→ 启动 tunnel (端口 $PORT),尝试 ${#METHODS[@]} 路机制…"
FINAL_URL=""
FINAL_METHOD=""

for m in "${METHODS[@]}"; do
    IFS=':' read -r name fn <<< "$m"
    note "试 [$name]…"
    url=$($fn 2>/dev/null) || url=""
    if [ -z "$url" ]; then
        fail "$name 启动失败或未拿到 URL"
        [ -n "${TUNNEL_PID:-}" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
        TUNNEL_PID=""
        continue
    fi
    note "  URL: $url"
    note "  自检 /api/health…"
    if self_check "$url"; then
        ok "$name 自检通过"
        FINAL_URL="$url"
        FINAL_METHOD="$name"
        break
    else
        fail "$name 自检未通过 (/api/health 不可达)"
        [ -n "${TUNNEL_PID:-}" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
        TUNNEL_PID=""
    fi
done

if [ -z "$FINAL_URL" ]; then
    echo ""
    echo "════════════════════════════════════════════"
    echo "  ✗ 6 路隧道全部失败"
    echo "  本机/局域网仍可用:"
    echo "    http://localhost:$PORT"
    echo "    http://$LAN_IP:$PORT"
    echo "  日志: ls $LOG_DIR/*.log"
    echo "════════════════════════════════════════════"
    exit 1
fi

# ─── 写结果文件 ───
echo "$FINAL_URL"   > "$URL_FILE"
echo "$FINAL_METHOD" > "$METHOD_FILE"
echo "$TUNNEL_PID"   > "$PID_FILE"

echo ""
echo "════════════════════════════════════════════"
echo "  ✓ 公网 URL: $FINAL_URL"
echo "  ✓ 隧道方法: $FINAL_METHOD (PID=$TUNNEL_PID)"
echo "  局域网:    http://$LAN_IP:$PORT"
echo "  日志:      tail -f $LOG_DIR/$([ "$FINAL_METHOD" = "ngrok" ] && echo ngrok || echo cf_quic).log"
echo "  停止:      bash $SCRIPT_DIR/start_tunnel_only.sh stop"
echo "════════════════════════════════════════════"

exit 0