#!/usr/bin/env bash
# start_remote.sh — 启动 FastAPI + 多路逃生隧道自检 + 推送 URL 到 TG
#
# 流程：清理旧进程 → 启动 server → 多路隧道顺序自检（每路 curl 验通）→
#       第一个自检通过的 URL 推 TG → 等待 SIGINT
#
# 18 路逃生（全部免费，按优先级插入；前 8 路是 2026-07-12 新增的"反劫持"逃生机
# 制，每条都走不同的网络出口，避开 DNS 劫持到 198.18.x + 任意 IP TLS 阻断）：
#
#   TIER A · Overlay / 主机之间私有网络（LAN 之外，survive sandbox）
#   A1) Tailscale serve (controlplane + DERP relay)
#   A2) ZeroTier (planet root on 443)
#   A3) Trystero (BitTorrent trackers / MQTT signaling，纯浏览器 P2P)
#
#   TIER B · 云平台 relay（Mac 出站连到 free PaaS 的 WebSocket 中转）
#   B1) Cloudflare Worker + Durable Object (workers.dev)
#   B2) Koyeb / Render / Fly.io / HF Spaces (一次性 docker deploy)
#
#   TIER C · 消息平台 / 推送作为代理（API 域名被白名单）
#   C1) Telegram bot 双向桥 (api.telegram.org)  ← 关键，沙箱只放行这个
#   C2) NTFY pipe (ntfy.sh)
#   C3) MQTT-over-TLS public broker (broker.hivemq.com)
#
#   TIER D · 已有隧道（保留作为最后兜底，网络宽松时会工作）
#   D1) ngrok
#   D2) cloudflared QUIC / HTTP2 / IPv4 (3 路)
#   D3) localhost.run / serveo.net / pinggy (5 regions)
#   D4) loca.lt / tunnel.pyjam.as / localtunnel
#
#   终极兜底：LAN IP + QR code + TG 推送（Telegram 必发，11 路全挂也能上 LAN）
#
# 用法:  bash start_remote.sh
# 退出:  Ctrl+C（同时结束 server 和当前 tunnel）
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
PORT="${PORT:-7799}"
LOG="/tmp/tuixue_start.log"
TUNNELS_DIR="/tmp/tuixue_tunnels"
mkdir -p "$TUNNELS_DIR"

# 共享 helpers (URL IO / 健康检查 / TG 推送 / 进程管理)
# shellcheck disable=SC1091
source "$ROOT/tuixue_v3/web/tunnel_lib.sh" 2>/dev/null || \
source "$(dirname "$0")/tunnel_lib.sh"

# ─── 加载环境变量 ───
[ -f "$HOME/.hermes/env.sh" ] && source "$HOME/.hermes/env.sh"

# ─── API key 守门 ───
# 缺 key 时 UI 上 AI 模块会全部降级 (server.py:2798, 1905 等),
# 在此显式告警,避免用户卡在 "AI 未配置" 找不到原因。
# 真要硬阻断请把 exit 1 取消注释。
if [ -z "${MINIMAX_API_KEY:-}" ]; then
    echo ""
    echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ⚠  MINIMAX_API_KEY 未配置"
    echo "     • server 会启动, 但 AI 复盘/选股/对话 全程降级"
    echo "     • 修复:  把 key 写入 ~/.hermes/env.sh  (参考 .env.example)"
    echo "            或 export MINIMAX_API_KEY=sk-cp-...  后再 bash $0"
    echo "     • ⚠ 如果之前把 key 明文写进了代码,先在 MiniMax 控制台 revoke 重发"
    echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    # exit 1   # 取消注释可硬阻断启动
fi

# ─── 工具 ───
note()  { echo -e "  $*"; }
ok()    { echo -e "  ✓ $*"; }
fail()  { echo -e "  ✗ $*"; }
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || echo "0.0.0.0")
TS()    { date '+%Y-%m-%d %H:%M:%S'; }

send_tg() {
    python3 -c "from tuixue_v3.lib_common import send_telegram; send_telegram('''$1''', parse_mode='', silent=True)" 2>/dev/null
}

# ─── 清理旧进程 ───
echo "→ 清理旧进程 …"
lsof -ti ":$PORT" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
pkill -f "cloudflared tunnel --url" 2>/dev/null || true
pkill -f "ssh -tt -R 80:localhost:$PORT" 2>/dev/null || true
pkill -f "ngrok http $PORT" 2>/dev/null || true
sleep 1

# ─── 启动 FastAPI ───
echo "→ 启动 FastAPI（端口 $PORT）…"
cd "$ROOT"
PYTHON_BIN="/Users/kaikai/.hermes/hermes-agent/venv/bin/python3"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN=python3
PYTHONPATH="$ROOT" "$PYTHON_BIN" -m tuixue_v3.web.server --host 0.0.0.0 --port "$PORT" \
    > /tmp/tuixue_server.log 2>&1 &
SERVER_PID=$!

# 健康检查
for i in $(seq 1 10); do
    sleep 1
    if curl -s --max-time 2 "http://localhost:$PORT/api/health" > /dev/null 2>&1; then
        ok "server up (pid=$SERVER_PID, ${i}s)"
        break
    fi
    if [ "$i" = "10" ]; then
        fail "server 启动失败："
        tail -20 /tmp/tuixue_server.log
        kill -9 "$SERVER_PID" 2>/dev/null || true
        exit 1
    fi
done

# ─── 6 路隧道自检 ───
TUNNEL_URL=""
TUNNEL_METHOD=""
TUNNEL_PID=""

self_check() {
    local url="$1"
    # 隧道自检：3 路全验通才算通过
    #   1) /api/health           — 控制面入口 (200 + ok=true)
    #   2) /static/app.js        — 大资源能完整传 (200 + content-encoding=gzip 或 ok 大小)
    #   3) HEAD /api/stream/backtest?start=... — SSE 长连接握手能开 (响应 ≤ 5s 内,server 会被预热拉过缓存)
    # 全部满足 → 真可用,过。否则失败切下一路。
    local ok_health=0 ok_static=0 ok_sse=0

    for w in $(seq 1 15); do
        sleep 2

        # 1) health 200 + ok=true
        if [ "$ok_health" = "0" ]; then
            local body code
            code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url/api/health" 2>&1)
            body=$(curl -s --max-time 5 "$url/api/health" 2>/dev/null)
            if [ "$code" = "200" ] && echo "$body" | grep -qE '"ok":\s*true|"status":\s*"ok"|"code":\s*"ok"'; then
                ok_health=1
            fi
        fi

        # 2) /static/app.js 验 gzip: 必须用 GET,不能 curl -sI —— hypercorn 对 HEAD 一律 405
        #    (app.js 54KB,丢弃 body 用 -o /dev/null,只留 -D - 的响应头;tunnel 加 ms 可忽略)
        if [ "$ok_static" = "0" ]; then
            local hdr
            hdr=$(curl -s -o /dev/null -D - --max-time 6 -H "Accept-Encoding: gzip" \
                "$url/static/app.js" 2>/dev/null)
            if echo "$hdr" | grep -qiE "^HTTP/[0-9.]+ 200" && echo "$hdr" | grep -qiE "content-encoding:.*gzip"; then
                ok_static=1
            fi
        fi

        # 3) SSE 握手 /api/stream/review/0: 这是 GET 单 path param,无 body。
        #    原 /api/stream/backtest 用 -X GET --data-urlencode 把 body 塞进 GET,FastAPI 422,握手永远过不去。
        #    SSE 端点立刻返 200 + text/event-stream 并开始流;max-time 4s 截断时已读到握手头。
        if [ "$ok_sse" = "0" ]; then
            local sse_hdr
            sse_hdr=$(curl -s --max-time 4 -D - -o /dev/null \
                "$url/api/stream/review/0" 2>/dev/null)
            if echo "$sse_hdr" | grep -qiE "^HTTP/[0-9.]+ 200" \
               && echo "$sse_hdr" | grep -qiE "content-type:.*event-stream"; then
                ok_sse=1
            fi
        fi

        if [ "$ok_health" = "1" ] && [ "$ok_static" = "1" ] && [ "$ok_sse" = "1" ]; then
            note "  self_check: health ✓ static ✓ sse-handshake ✓"
            return 0
        fi
        note "  self_check round $w: health=$ok_health static=$ok_static sse=$ok_sse"
    done
    note "  self_check failed: health=$ok_health static=$ok_static sse=$ok_sse"
    return 1
}

# =======================================================================
# TIER A — Overlay / P2P
# =======================================================================

# A1) Tailscale — `tailscale serve --bg 7799` exposes port via MagicDNS hostname
#     or via Funnel for a real public https://<host>.ts.net URL.
#     Most reliable: NAT-traversing, free, official iOS app.
try_tailscale() {
    if ! command -v tailscale > /dev/null; then return 1; fi
    if ! tailscale status --json >/dev/null 2>&1; then return 1; fi
    local logfile="$TUNNELS_DIR/tailscale.log"
    : > "$logfile"
    # tailscale serve exposes port to tailnet. Funnel adds public HTTPS ingress.
    if tailscale serve --bg --https=443 http://localhost:"$PORT" >>"$logfile" 2>&1; then :; \
    else tailscale serve --bg tcp:"$PORT" http://localhost:"$PORT" >>"$logfile" 2>&1; fi
    sleep 1
    # Try funnel for a public URL (free public ingress on *.ts.net)
    if tailscale funnel --bg --https=443 http://localhost:"$PORT" >>"$logfile" 2>&1; then
        url=$(grep -oE 'https://[a-zA-Z0-9.-]+\.ts\.net' "$logfile" | head -1)
    fi
    # Fallback: MagicDNS hostname in tailnet (works for iPhone iOS app)
    if [[ -z "$url" ]]; then
        hostname=$(tailscale status --json 2>/dev/null | \
            python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('Self',{}).get('HostName','')+'.'+d.get('MagicDNSSuffix',''))" 2>/dev/null | sed 's/^\.*//')
        if [[ -n "$hostname" ]]; then
            url="http://$hostname"
        fi
    fi
    if [[ -n "$url" ]]; then
        TUNNEL_PID=$(pgrep -f "tailscaled" | head -1 || echo $$)
        return 0
    fi
    return 1
}

# A2) ZeroTier — different control plane, fallback if Tailscale fails.
#     Free for ≤ 25 nodes. Mac joins a network, phone joins same.
try_zerotier() {
    if ! command -v zerotier-cli > /dev/null; then return 1; fi
    local nwid status
    nwid=$(grep -oE '[0-9a-f]{16}' "$TUNNELS_DIR/.zerotier-nwid" 2>/dev/null || true)
    if [[ -z "$nwid" ]]; then return 1; fi
    status=$(zerotier-cli status 2>/dev/null)
    if ! grep -q "ONLINE" <<<"$status"; then return 1; fi
    local ip
    ip=$(zerotier-cli getnetworkinfo "$nwid" 2>/dev/null | grep -oE '"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"' | tr -d '"' | head -1)
    if [[ -z "$ip" ]]; then
        ip=$(zerotier-cli listnetworks 2>/dev/null | awk -v n="$nwid" '$3==n {print $NF}')
    fi
    if [[ -n "$ip" ]]; then
        echo "$ip" > "$TUNNELS_DIR/.zerotier-ip"
        TUNNEL_PID=$(pgrep -f "zerotier-one" | head -1 || echo $$)
        return 0
    fi
    return 1
}

# A3) Trystero — WebRTC over BitTorrent trackers / public MQTT brokers.
#     Zero install server-side. Browser-only on both Mac + phone.
#     The relay isn't on the Mac; start_trystero.py opens an aiohttp static
#     page that contains the rendezvous room URL.
try_trystero() {
    local logfile="$TUNNELS_DIR/trystero.log"
    : > "$logfile"
    python3 "$ROOT/tuixue_v3/web/relay/trystero_host.py" --port "$PORT" >>"$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 10); do
        sleep 1
        local url
        url=$(grep -oE 'http://localhost:[0-9]+/trystero' "$logfile" | head -1)
        if [[ -n "$url" ]]; then
            return 0
        fi
        if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then return 1; fi
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

# =======================================================================
# TIER B — Free PaaS WebSocket relays (Mac opens outbound WSS)
# =======================================================================

# B1) Cloudflare Worker + Durable Object.
#     Reads URL from ~/.config/tuixue/relays.json (set by one-time wrangler deploy).
try_cf_worker() {
    local cfg="$HOME/.config/tuixue/relays.json"
    [[ -f "$cfg" ]] || return 1
    local url
    url=$(python3 -c "import json;d=json.load(open('$cfg'));print(d.get('cf_worker',''))" 2>/dev/null)
    [[ -n "$url" && "$url" != "None" ]] || return 1
    # Probe reachable in ≤ 6s (sandbox test)
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 "${url%/}/" 2>/dev/null)
    [[ "$code" =~ ^[23] ]] || return 1
    # Mac-side client lives in tun_cf_client.py — opens WSS and pipes localhost:7799
    python3 "$ROOT/tuixue_v3/web/relay/tun_cf_client.py" \
        --wss "$url" --session "${CF_SESSION:-tuixue-$(hostname -s)}" --port "$PORT" \
        >>"$TUNNELS_DIR/cf_client.log" 2>&1 &
    TUNNEL_PID=$!
    sleep 2
    if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then return 1; fi
    return 0
}

# B2) Generic PaaS container (Koyeb / Render / Fly / HF) — same relay image.
try_paas_relay() {
    local cfg="$HOME/.config/tuixue/relays.json"
    [[ -f "$cfg" ]] || return 1
    local wss
    wss=$(python3 -c "import json;d=json.load(open('$cfg'));print(d.get('paas_relay_wss',''))" 2>/dev/null)
    [[ -n "$wss" && "$wss" != "None" ]] || return 1
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 "${wss%/}/" 2>/dev/null)
    [[ "$code" =~ ^[23] ]] || return 1
    python3 "$ROOT/tuixue_v3/web/relay/tun_paas_client.py" \
        --wss "$wss" --port "$PORT" \
        >>"$TUNNELS_DIR/paas_client.log" 2>&1 &
    TUNNEL_PID=$!
    sleep 2
    kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    return 0
}

# =======================================================================
# TIER C — Messaging / Push bridges (different domain surface)
# =======================================================================

# C1) Telegram bot bidirectional bridge.
#     `api.telegram.org` is the canonical whitelist. Mac-side is a Python
#     long-poller that converts user messages → HTTP requests → response.
#     Phone doesn't need a URL — it just sends messages to @<bot>.
try_telegram_bot() {
    if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]] && [[ ! -f "$HOME/.hermes/env.sh" ]]; then
        return 1
    fi
    local logfile="$TUNNELS_DIR/telegram_bridge.log"
    : > "$logfile"
    python3 "$ROOT/tuixue_v3/web/relay/telegram_bridge.py" --port "$PORT" >>"$logfile" 2>&1 &
    TUNNEL_PID=$!
    # Wait for sentinel or up to 8s — fast-fail if no TG bot token
    for i in $(seq 1 8); do
        sleep 1
        if grep -q "tg-bridge\] running" "$logfile" 2>/dev/null; then
            write_sentinel "telegram-bot" "Send messages to your TG bot; replies are server responses."
            return 0
        fi
        if grep -qE "TELEGRAM_BOT_TOKEN missing|Exception|Traceback" "$logfile" 2>/dev/null; then
            kill -9 "$TUNNEL_PID" 2>/dev/null
            return 1
        fi
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

# C2) NTFY bidirectional pipe.
#     Mac subscribes to https://ntfy.sh/<topic>, phone publishes to same
#     topic. URL is real — can be opened in any browser, no auth required.
try_ntfy() {
    # Quick connectivity test on ntfy.sh
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 https://ntfy.sh/ 2>/dev/null)
    [[ "$code" =~ ^[23] ]] || return 1
    local logfile="$TUNNELS_DIR/ntfy_pipe.log"
    : > "$logfile"
    python3 "$ROOT/tuixue_v3/web/relay/ntfy_pipe.py" --port "$PORT" >>"$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 10); do
        sleep 1
        local url
        url=$(grep -oE 'https://ntfy\.sh/[a-zA-Z0-9-]+' "$logfile" | head -1)
        if [[ -n "$url" ]]; then return 0; fi
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

# C3) MQTT-over-TLS public broker.
#     Different protocol entirely. Different egress (broker.hivemq.com:8883).
#     Phone uses free MQTT iOS app.
try_mqtt() {
    # Probe broker.hivemq.com reachable in ≤ 5s
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 https://broker.hivemq.com/ 2>/dev/null)
    [[ "$code" =~ ^[23] ]] || return 1
    # Probe Python 'aiomqtt' module installed (was asyncio-mqtt, renamed)
    python3 -c "import aiomqtt" 2>/dev/null || return 1
    local logfile="$TUNNELS_DIR/mqtt_bridge.log"
    : > "$logfile"
    python3 "$ROOT/tuixue_v3/web/relay/mqtt_bridge.py" --port "$PORT" >>"$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 8); do
        sleep 1
        if grep -q "mqtt-bridge\] session=" "$logfile" 2>/dev/null; then
            write_sentinel "mqtt" "Use any free MQTT iOS app pointed at broker.hivemq.com:8883; subscribe tuixue/<session>/resp, publish to tuixue/<session>/req."
            return 0
        fi
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

# =======================================================================
# TIER D — Existing tunnels (kept as final backstop)
# =======================================================================

# 1) cloudflared (QUIC)
try_cloudflared_quic() {
    local logfile="$TUNNELS_DIR/cf_quic.log"
    : > "$logfile"
    cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate \
        >> "$logfile" 2>&1 &
    local pid=$!
    for i in $(seq 1 30); do
        sleep 1
        local url
        url=$(awk '/Your quick Tunnel has been created/{flag=1} flag' "$logfile" \
            | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | head -1)
        if [ -n "$url" ]; then
            TUNNEL_PID="$pid"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    return 1
}

# 2) cloudflared --protocol http2
try_cloudflared_http2() {
    local logfile="$TUNNELS_DIR/cf_http2.log"
    : > "$logfile"
    cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate --protocol http2 \
        >> "$logfile" 2>&1 &
    local pid=$!
    for i in $(seq 1 30); do
        sleep 1
        local url
        url=$(awk '/Your quick Tunnel has been created/{flag=1} flag' "$logfile" \
            | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | head -1)
        if [ -n "$url" ]; then
            TUNNEL_PID="$pid"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    return 1
}

# 3) cloudflared --edge-ip-version 4
try_cloudflared_v4() {
    local logfile="$TUNNELS_DIR/cf_v4.log"
    : > "$logfile"
    cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate --edge-ip-version 4 \
        >> "$logfile" 2>&1 &
    local pid=$!
    for i in $(seq 1 30); do
        sleep 1
        local url
        url=$(awk '/Your quick Tunnel has been created/{flag=1} flag' "$logfile" \
            | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | head -1)
        if [ -n "$url" ]; then
            TUNNEL_PID="$pid"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    return 1
}

# 4) localhost.run (ssh)
try_localhost_run() {
    local logfile="$TUNNELS_DIR/lhr.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=30 -R 80:localhost:$PORT nokey@localhost.run \
        >> "$logfile" 2>&1 &
    local pid=$!
    for i in $(seq 1 45); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+\.lhr\.life" "$logfile" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            TUNNEL_PID="$pid"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    return 1
}

# 5) serveo.net (ssh)
# 注意：serveo 默认输出两个 URL 字符串 — 真实隧道是
# "Forwarding HTTP traffic from https://<id>-<ip>.serveousercontent.com"
# 而 "console.serveo.net" 只是它的 web 控制台，会误捕
try_serveo() {
    local logfile="$TUNNELS_DIR/serveo.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=30 -R 80:localhost:$PORT serveo.net \
        >> "$logfile" 2>&1 &
    local pid=$!
    for i in $(seq 1 45); do
        sleep 1
        # 优先匹配 serveousercontent.com 域名（真实隧道），其次 .serveo.net
        local url
        url=$(grep -oE "https?://[a-z0-9-]+(-[0-9]+(-[0-9]+(-[0-9]+(-[0-9]+)?)?)?)\.serveousercontent\.com" "$logfile" 2>/dev/null | head -1)
        if [ -z "$url" ]; then
            url=$(grep -oE "Forwarding HTTP traffic from https?://[^[:space:]]+" "$logfile" 2>/dev/null | grep -oE "https?://[^[:space:]]+" | head -1)
        fi
        if [ -n "$url" ]; then
            TUNNEL_PID="$pid"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    return 1
}

# 6) ngrok (authtoken)
try_ngrok() {
    local logfile="$TUNNELS_DIR/ngrok.log"
    : > "$logfile"
    if ! command -v ngrok > /dev/null; then
        return 1
    fi
    ngrok http "$PORT" --log "$logfile" --log-level=info > /dev/null 2>&1 &
    local pid=$!
    for i in $(seq 1 30); do
        sleep 1
        # ngrok 写本地 API (4040) 拿 URL，不靠 stdout
        local url
        url=$(curl -s --max-time 2 http://127.0.0.1:4040/api/tunnels 2>/dev/null \
            | python3 -c "import json,sys;d=json.load(sys.stdin);t=d.get('tunnels',[]);print(t[0]['public_url'] if t else '')" 2>/dev/null)
        if [ -n "$url" ] && [[ "$url" == https://* ]]; then
            TUNNEL_PID="$pid"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    return 1
}

# 7) pinggy.io (ssh -p 443 -R 0:localhost:PORT a.pinggy.io → *.pinggy.link)
#   多区域 (a/b/c/d/e.pinggy.io) 任一可连即可。零注册
try_pinggy() {
    local logfile="$TUNNELS_DIR/pinggy.log"
    : > "$logfile"
    local pid=""
    for region in a b c d e; do
        note "  pinggy: 试 $region.pinggy.io"
        ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
            -p 443 -R 0:localhost:$PORT "$region.pinggy.io" \
            >> "$logfile" 2>&1 &
        pid=$!
        for i in $(seq 1 25); do
            sleep 1
            local url
            url=$(grep -oE "https?://[a-z0-9-]+\.pinggy\.link" "$logfile" 2>/dev/null | head -1)
            if [ -n "$url" ]; then
                TUNNEL_PID="$pid"
                return 0
            fi
            if ! kill -0 "$pid" 2>/dev/null; then
                break  # 当前 region 死了，试试下一个
            fi
        done
        kill -9 "$pid" 2>/dev/null
        pid=""
    done
    return 1
}

# 8) loca.lt (ssh -R 80:localhost:PORT loca.lt → *.loca.lt)
#   零注册；输入 y 接受条款时用 -o SendEnv=... 或提前在 log 里识别
try_loca_lt() {
    local logfile="$TUNNELS_DIR/loca.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 80:localhost:$PORT loca.lt \
        >> "$logfile" 2>&1 &
    local pid=$!
    for i in $(seq 1 30); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+\.loca\.lt" "$logfile" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            TUNNEL_PID="$pid"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    return 1
}

# 9) tunnel.pyjam.as (ssh -R 80:localhost:PORT tunnel.pyjam.as → *.pyjam.as)
#   零注册；Python 社区常用
try_pyjam_as() {
    local logfile="$TUNNELS_DIR/pyjam.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 80:localhost:$PORT tunnel.pyjam.as \
        >> "$logfile" 2>&1 &
    local pid=$!
    for i in $(seq 1 30); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+\.pyjam\.as" "$logfile" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            TUNNEL_PID="$pid"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    return 1
}

# 10) localtunnel (npm `lt` → *.loca.lt)
#   `lt --port PORT` 启动后会打印 URL 到 stdout；零注册
try_localtunnel() {
    if ! command -v lt > /dev/null; then
        return 1
    fi
    local logfile="$TUNNELS_DIR/lt.log"
    : > "$logfile"
    lt --port "$PORT" >> "$logfile" 2>&1 &
    local pid=$!
    for i in $(seq 1 20); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+\.loca\.lt" "$logfile" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            TUNNEL_PID="$pid"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    return 1
}

# 抽 URL 的统一函数（按 name）
get_url_for() {
    local name="$1"
    case "$name" in
        # New mechanisms (2026-07-12)
        tailscale)
            # Prefer Funnel URL (*.ts.net); fallback to MagicDNS hostname
            grep -oE 'https://[a-zA-Z0-9.-]+\.ts\.net' "$TUNNELS_DIR/tailscale.log" 2>/dev/null | head -1
            ;;
        zerotier)
            [[ -f "$TUNNELS_DIR/.zerotier-ip" ]] && echo "zerotier:$(cat "$TUNNELS_DIR/.zerotier-ip"):$PORT"
            ;;
        cf-worker)
            # Mac opens outbound WSS but the dashboard URL is the .workers.dev
            python3 -c "
import json
try:
    d = json.load(open('$HOME/.config/tuixue/relays.json'))
    print(d.get('cf_worker',''))
except Exception:
    pass
" 2>/dev/null
            ;;
        paas-relay)
            python3 -c "
import json
try:
    d = json.load(open('$HOME/.config/tuixue/relays.json'))
    print(d.get('paas_relay',''))
except Exception:
    pass
" 2>/dev/null
            ;;
        telegram-bot)
            # sentinel-based; URL is the @bot handle
            [[ -f "$TUNNELS_DIR/telegram-bot.ready" ]] && echo "telegram-bot://see-sentinel"
            ;;
        ntfy)
            grep -oE 'https://ntfy\.sh/[a-zA-Z0-9-]+' "$TUNNELS_DIR/ntfy_pipe.log" 2>/dev/null | head -1
            ;;
        mqtt)
            [[ -f "$TUNNELS_DIR/mqtt_bridge.ready" ]] && echo "mqtt://see-sentinel"
            ;;
        trystero)
            grep -oE 'http://localhost:[0-9]+/trystero' "$TUNNELS_DIR/trystero.log" 2>/dev/null | head -1
            ;;
        # Legacy tunnels (kept)
        cloudflared-*)  awk '/Your quick Tunnel has been created/{flag=1} flag' "$TUNNELS_DIR/${name#cloudflared-}.log" 2>/dev/null | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | head -1 ;;
        localhost.run)  grep -oE "https?://[a-z0-9-]+\.lhr\.life" "$TUNNELS_DIR/lhr.log" 2>/dev/null | head -1 ;;
        serveo.net)     grep -oE "https?://[a-z0-9-]+(-[0-9]+(-[0-9]+(-[0-9]+(-[0-9]+)?)?)?)\.serveousercontent\.com" "$TUNNELS_DIR/serveo.log" 2>/dev/null | head -1 ;;
        ngrok)          curl -s --max-time 2 http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "import json,sys;d=json.load(sys.stdin);t=d.get('tunnels',[]);print(t[0]['public_url'] if t else '')" 2>/dev/null ;;
        pinggy)         grep -oE "https?://[a-z0-9-]+\.pinggy\.link" "$TUNNELS_DIR/pinggy.log" 2>/dev/null | head -1 ;;
        loca.lt)        grep -oE "https?://[a-z0-9-]+\.loca\.lt" "$TUNNELS_DIR/loca.log" 2>/dev/null | head -1 ;;
        pyjam.as)       grep -oE "https?://[a-z0-9-]+\.pyjam\.as" "$TUNNELS_DIR/pyjam.log" 2>/dev/null | head -1 ;;
        localtunnel)    grep -oE "https?://[a-z0-9-]+\.loca\.lt" "$TUNNELS_DIR/lt.log" 2>/dev/null | head -1 ;;
        *)              echo "" ;;
    esac
}

# 主流程：按优先级逐个试（新机制在最前；2026-07-12 加固）
declare -a METHODS=(
    # TIER A — overlay / P2P (NAT-traversing, usually works)
    "tailscale:try_tailscale"
    "zerotier:try_zerotier"
    # TIER C — different-domain proxies (the strong diversifiers)
    "telegram-bot:try_telegram_bot"
    "ntfy:try_ntfy"
    "mqtt:try_mqtt"
    # TIER B — PaaS-deployed WS relays (Cloudflare IPs almost always allowed)
    "cf-worker:try_cf_worker"
    "paas-relay:try_paas_relay"
    # TIER A3 — last among new ones (needs Python helper + browser on both ends)
    "trystero:try_trystero"
    # TIER D — legacy tunnels (kept as final backstop)
    "ngrok:try_ngrok"
    "cloudflared-QUIC:try_cloudflared_quic"
    "cloudflared-HTTP2:try_cloudflared_http2"
    "cloudflared-IPv4:try_cloudflared_v4"
    "pinggy:try_pinggy"
    "localhost.run:try_localhost_run"
    "serveo.net:try_serveo"
    "loca.lt:try_loca_lt"
    "pyjam.as:try_pyjam_as"
    "localtunnel:try_localtunnel"
)

echo ""
echo "→ 18 路隧道自检（前 8 路为新增的 anti-sandbox 机制，每路最多 15s 快速试）…"
for m in "${METHODS[@]}"; do
    IFS=':' read -r name fn <<< "$m"
    note "[$name] 启动中…"
    if $fn; then
        url=$(get_url_for "$name")
        if [ -z "$url" ]; then
            fail "$name 拿到 URL 失败"
            [ -n "${TUNNEL_PID:-}" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
            TUNNEL_PID=""
            continue
        fi
        note "  URL: $url"
        note "  自检 (16s 内 curl /api/health) …"
        if self_check "$url"; then
            ok "自检通过"
            TUNNEL_URL="$url"
            TUNNEL_METHOD="$name"
            break
        else
            fail "自检未通过"
            [ -n "${TUNNEL_PID:-}" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
            TUNNEL_PID=""
        fi
    else
        fail "$name 启动失败（30s 内未拿到 URL）"
        [ -n "${TUNNEL_PID:-}" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
        TUNNEL_PID=""
    fi
done

# ─── 打印最终状态 ───
echo ""
echo "════════════════════════════════════════════════════════"
echo "  本机访问  http://localhost:$PORT"
echo "  局域网    http://$LAN_IP:$PORT"
if [ -n "$TUNNEL_URL" ]; then
    echo "  远程访问  $TUNNEL_URL"
    echo "  隧道方法  $TUNNEL_METHOD"
else
    echo "  ⚠ 10 路隧道全部失败 — 当前仅本地/局域网可用"
    echo "  失败原因多为网络层 DNS 劫持到 198.18.x + TLS 阻断"
    echo "  重试: bash web/start_remote.sh"
fi
echo "════════════════════════════════════════════════════════"
echo ""
echo "日志:"
echo "  server  tail -f /tmp/tuixue_server.log"
echo "  tunnel  tail -f $TUNNELS_DIR/<method>.log"
echo "退出: Ctrl+C（所有进程都会结束）"

# ─── 推 TG ───
push_tg() {
    local url="$1" method="$2" extra="$3"
    if [ -n "$url" ]; then
        local TG_MSG="🟢 退学 v3 控制台已上线

📡 本机:   http://localhost:$PORT
🌐 局域网: http://$LAN_IP:$PORT
🌍 远程:   $url
🔧 方法:   $method
⏰ $(TS)
${extra}

iPhone 浏览器直接打开远程 URL。临时隧道约 24h 后失效。"
        send_tg "$TG_MSG" >/dev/null 2>&1 && ok "TG 推送成功" || fail "TG 推送失败"
    else
        local TG_MSG="⚠️ 退学 v3 控制台 — 远程隧道全部失败
📡 本机:   http://localhost:$PORT
🌐 局域网: http://$LAN_IP:$PORT
⏰ $(TS)
${extra}

可能网络层 DNS 劫持 + TLS 阻断。手机需连同 Wi-Fi 访问局域网 URL。"
        send_tg "$TG_MSG" >/dev/null 2>&1 && ok "TG 推送成功" || fail "TG 推送失败"
    fi
}

push_tg "$TUNNEL_URL" "$TUNNEL_METHOD" ""

# ─── Supervisor 守护：30s 自检，断 2 次切下一路 ───
# 状态文件做 IPC，watchdog 写 rotate.flag，main 读后清掉
ROTATE_FLAG="$TUNNELS_DIR/.rotate"
DEAD_COUNT=0
SUPERVISOR_LOG="$TUNNELS_DIR/supervisor.log"

# 写一行日志
sup_log() { echo "[$(TS)] $*" >> "$SUPERVISOR_LOG"; }

# 隧道活性检查
check_tunnel() {
    local url="$1"
    [ -z "$url" ] && return 1
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url/api/health" 2>&1)
    if [ "$code" = "200" ]; then
        return 0
    fi
    return 1
}

# 找下一个能用的方法（从失败的下一路开始）
rotate_tunnel() {
    # 杀当前
    if [ -n "${TUNNEL_PID:-}" ]; then
        kill -9 "$TUNNEL_PID" 2>/dev/null
        TUNNEL_PID=""
    fi
    # 标记当前方法失败
    FAILED_METHODS="${FAILED_METHODS:-} $TUNNEL_METHOD"
    TUNNEL_URL=""
    TUNNEL_METHOD=""

    # 从 METHODS 里找当前 method 的 index
    local i=0 current_idx=-1 next_idx=0
    for m in "${METHODS[@]}"; do
        local n="${m%%:*}"
        if [ "$n" = "${LAST_METHOD:-}" ]; then
            current_idx=$i
        fi
        i=$((i+1))
    done
    next_idx=$((current_idx + 1))
    if [ "$next_idx" -ge "${#METHODS[@]}" ]; then
        next_idx=0
    fi

    # 试 next_idx..end，然后 0..next_idx-1（即"循环一圈"）
    local tried=0 attempts="${#METHODS[@]}"
    while [ "$tried" -lt "$attempts" ]; do
        local m="${METHODS[$next_idx]}"
        local name="${m%%:*}"
        local fn="${m##*:}"
        # 跳过刚失败的（连续失败 3 次后放回去重试）
        local fail_count=0
        for fm in $FAILED_METHODS; do
            [ "$fm" = "$name" ] && fail_count=$((fail_count+1))
        done
        if [ "$fail_count" -ge 3 ]; then
            next_idx=$(( (next_idx + 1) % ${#METHODS[@]} ))
            tried=$((tried+1))
            continue
        fi

        sup_log "rotate → 试 $name"
        note "[rotate → $name]"
        if $fn; then
            local url
            url=$(get_url_for "$name")
            if [ -n "$url" ] && check_tunnel "$url"; then
                TUNNEL_URL="$url"
                TUNNEL_METHOD="$name"
                LAST_METHOD="$name"
                DEAD_COUNT=0
                sup_log "rotate OK: $name = $url"
                push_tg "$TUNNEL_URL" "$TUNNEL_METHOD" "🔁 上一路断了，已自动切换"
                return 0
            fi
        fi
        # 失败：杀残留 pid 并标失败
        [ -n "${TUNNEL_PID:-}" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
        TUNNEL_PID=""
        FAILED_METHODS="$FAILED_METHODS $name"
        next_idx=$(( (next_idx + 1) % ${#METHODS[@]} ))
        tried=$((tried+1))
    done
    # 一圈全失败 → 清空失败列表，等 60s 再来
    sup_log "all methods failed, sleep 60s then retry"
    FAILED_METHODS=""
    sleep 60
    LAST_METHOD=""
    return 1
}

LAST_METHOD="${TUNNEL_METHOD:-}"
MAIN_PID=$$
sup_log "supervisor 启动，初始方法=$LAST_METHOD url=$TUNNEL_URL main_pid=$MAIN_PID"

# 主循环：等 SIGINT
if [ -n "$TUNNEL_URL" ]; then
    (
        # 后台 watchdog 子进程：30s 自检，触发 rotate 时写 flag 文件
        # rotate 动作由主 shell 执行（更稳，避免子进程 fork 复杂业务）
        while true; do
            sleep 30
            if ! check_tunnel "$TUNNEL_URL"; then
                DEAD_COUNT=$((DEAD_COUNT+1))
                sup_log "dead check #$DEAD_COUNT, url=$TUNNEL_URL"
                if [ "$DEAD_COUNT" -ge 2 ]; then
                    DEAD_COUNT=0
                    # 通知主 shell rotate
                    touch "$ROTATE_FLAG"
                    kill -USR1 "$MAIN_PID" 2>/dev/null
                fi
            else
                DEAD_COUNT=0
            fi
        done
    ) &
    WATCHDOG_PID=$!
    sup_log "watchdog pid=$WATCHDOG_PID"
fi

# 主 shell 的 USR1 处理：rotate
on_rotate() {
    if [ -f "$ROTATE_FLAG" ]; then
        rm -f "$ROTATE_FLAG"
        echo ""
        echo "→ [supervisor] 检测到隧道失活，开始 rotate …"
        sup_log "on_rotate triggered"
        rotate_tunnel
        if [ -n "$TUNNEL_URL" ]; then
            echo "  ✓ 新隧道: $TUNNEL_METHOD = $TUNNEL_URL"
        else
            echo "  ✗ rotate 失败，等下一轮"
        fi
    fi
}
trap on_rotate USR1

# 退出清理
cleanup() {
    echo ""
    echo "→ 关闭中 …"
    [ -n "${WATCHDOG_PID:-}" ] && kill -9 "$WATCHDOG_PID" 2>/dev/null
    kill $SERVER_PID ${TUNNEL_PID:-} 2>/dev/null
    pkill -f "cloudflared tunnel --url" 2>/dev/null
    pkill -f "ngrok http $PORT" 2>/dev/null
    pkill -f "ssh -tt.*localhost:$PORT" 2>/dev/null
    pkill -f "lt --port $PORT" 2>/dev/null
    rm -f "$ROTATE_FLAG"
    exit
}
trap cleanup INT TERM

# 等 server（每 1s 轮询，同时手动处理 rotate flag，因为 wait 会阻塞 USR1）
while kill -0 "$SERVER_PID" 2>/dev/null; do
    sleep 1
    if [ -f "$ROTATE_FLAG" ]; then
        on_rotate
    fi
done
cleanup
