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
    # 2026-07-16: 加 --traffic-policy-file 在 on_http_response 阶段自动注入
    # abuse_interstitial cookie (30天有效),后续访问 bypass ngrok 6024 警告页 —
    # 用户首次点 Visit Site 后,policy 每响应再 Set-Cookie 续期
    local policy_file="$ROOT/../tuixue_v3/web/.tunnels/ngrok_policy.yml"
    [ -f "$ROOT/tuixue_v3/web/.tunnels/ngrok_policy.yml" ] && policy_file="$ROOT/tuixue_v3/web/.tunnels/ngrok_policy.yml"
    if [ -f "$policy_file" ]; then
        ngrok http "$PORT" --traffic-policy-file="$policy_file" --log "$logfile" --log-level=info > /dev/null 2>&1 &
    else
        ngrok http "$PORT" --log "$logfile" --log-level=info > /dev/null 2>&1 &
    fi
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

# ─── 2026-07-12 加固:新增 8 路 anti-sandbox 逃生机 ───

write_sentinel_only() {
    local name="$1" info="$2"
    local sentinel="$LOG_DIR/$name.ready"
    cat > "$sentinel" <<EOF
mechanism: $name
url: $info
ready_at: $(date '+%Y-%m-%d %H:%M:%S')
EOF
    echo "$sentinel"
}

try_tailscale() {
    command -v tailscale >/dev/null || return 1
    tailscale status --json >/dev/null 2>&1 || return 1
    local logfile="$LOG_DIR/tailscale.log"
    : > "$logfile"
    tailscale serve --bg --https=443 http://localhost:"$PORT" >>"$logfile" 2>&1 \
        || tailscale serve --bg tcp:"$PORT" http://localhost:"$PORT" >>"$logfile" 2>&1
    sleep 1
    tailscale funnel --bg --https=443 http://localhost:"$PORT" >>"$logfile" 2>&1
    sleep 1
    local url
    url=$(grep -oE 'https://[a-zA-Z0-9.-]+\.ts\.net' "$logfile" | head -1)
    [ -n "$url" ] && { echo "$url"; return 0; }
    # Fallback: MagicDNS hostname
    local host
    host=$(tailscale status --json 2>/dev/null | \
        python3 -c "import json,sys;d=json.load(sys.stdin);n=d.get('Self',{}).get('HostName','mac')+'.'+d.get('MagicDNSSuffix','local');print(n.lstrip('.'))" 2>/dev/null)
    [ -n "$host" ] && { echo "http://$host"; return 0; }
    return 1
}

try_zerotier() {
    command -v zerotier-cli >/dev/null || return 1
    local nwid
    nwid=$(cat "$LOG_DIR/.zerotier-nwid" 2>/dev/null)
    [ -z "$nwid" ] && return 1
    local ip
    ip=$(zerotier-cli getnetworkinfo "$nwid" 2>/dev/null \
        | grep -oE '"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"' | tr -d '"' | head -1)
    [ -n "$ip" ] && { echo "zerotier:$ip:$PORT"; return 0; }
    return 1
}

try_telegram_bot() {
    [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && [ ! -f "$HOME/.hermes/env.sh" ] && return 1
    local logfile="$LOG_DIR/telegram_bridge.log"
    : > "$logfile"
    python3 "$ROOT/tuixue_v3/web/relay/telegram_bridge.py" --port "$PORT" >>"$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 8); do
        sleep 1
        if grep -q "tg-bridge\] running" "$logfile" 2>/dev/null; then
            write_sentinel_only "telegram-bot" "Send messages to your TG bot; replies are server responses."
            echo "tg://bot-relay-see-sentinel"
            return 0
        fi
        grep -qE "TELEGRAM_BOT_TOKEN missing|Traceback" "$logfile" 2>/dev/null && \
            { kill -9 "$TUNNEL_PID" 2>/dev/null; TUNNEL_PID=""; return 1; }
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

try_ntfy() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 https://ntfy.sh/ 2>/dev/null)
    echo "$code" | grep -qE '^[23]' || return 1
    local logfile="$LOG_DIR/ntfy_pipe.log"
    : > "$logfile"
    python3 "$ROOT/tuixue_v3/web/relay/ntfy_pipe.py" --port "$PORT" >>"$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 10); do
        sleep 1
        local url
        url=$(grep -oE 'https://ntfy\.sh/[a-zA-Z0-9-]+' "$logfile" | head -1)
        [ -n "$url" ] && { echo "$url"; return 0; }
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

try_mqtt() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 https://broker.hivemq.com/ 2>/dev/null)
    echo "$code" | grep -qE '^[23]' || return 1
    python3 -c "import aiomqtt" 2>/dev/null || return 1
    local logfile="$LOG_DIR/mqtt_bridge.log"
    : > "$logfile"
    python3 "$ROOT/tuixue_v3/web/relay/mqtt_bridge.py" --port "$PORT" >>"$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 8); do
        sleep 1
        if grep -q "mqtt-bridge\] session=" "$logfile" 2>/dev/null; then
            write_sentinel_only "mqtt" "Use any free MQTT iOS app pointed at broker.hivemq.com:8883"
            echo "mqtt://see-sentinel"
            return 0
        fi
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

try_cf_worker() {
    local cfg="$HOME/.config/tuixue/relays.json"
    [ -f "$cfg" ] || return 1
    local url
    url=$(python3 -c "import json;d=json.load(open('$cfg'));print(d.get('cf_worker',''))" 2>/dev/null)
    [ -n "$url" ] && [ "$url" != "None" ] || return 1
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 "${url%/}/" 2>/dev/null)
    echo "$code" | grep -qE '^[23]' || return 1
    python3 "$ROOT/tuixue_v3/web/relay/tun_cf_client.py" \
        --wss "$url" --session "tuixue-$(hostname -s)" --port "$PORT" \
        >>"$LOG_DIR/cf_client.log" 2>&1 &
    TUNNEL_PID=$!
    sleep 2
    kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    echo "$url"
    return 0
}

try_paas_relay() {
    local cfg="$HOME/.config/tuixue/relays.json"
    [ -f "$cfg" ] || return 1
    local wss
    wss=$(python3 -c "import json;d=json.load(open('$cfg'));print(d.get('paas_relay_wss',''))" 2>/dev/null)
    [ -n "$wss" ] && [ "$wss" != "None" ] || return 1
    local probe="${wss%/}/"
    probe="${probe/wss:/https:}"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 "$probe" 2>/dev/null)
    echo "$code" | grep -qE '^[23]' || return 1
    python3 "$ROOT/tuixue_v3/web/relay/tun_paas_client.py" \
        --wss "$wss" --port "$PORT" \
        >>"$LOG_DIR/paas_client.log" 2>&1 &
    TUNNEL_PID=$!
    sleep 2
    kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    echo "$probe" | sed 's|https://|https://|'
    return 0
}

try_trystero() {
    local logfile="$LOG_DIR/trystero.log"
    : > "$logfile"
    python3 "$ROOT/tuixue_v3/web/relay/trystero_host.py" --port "$PORT" >>"$logfile" 2>&1 &
    TUNNEL_PID=$!
    for i in $(seq 1 10); do
        sleep 1
        local url
        url=$(grep -oE 'http://localhost:[0-9]+/trystero' "$logfile" | head -1)
        [ -n "$url" ] && { echo "$url"; return 0; }
        kill -0 "$TUNNEL_PID" 2>/dev/null || return 1
    done
    kill -9 "$TUNNEL_PID" 2>/dev/null
    return 1
}

# ─── 14 路 fallback (2026-07-12 加固,前 8 路为 anti-sandbox 逃生机) ───
declare -a METHODS=(
    # TIER A — overlay / P2P (survives NAT + sandbox)
    "tailscale:try_tailscale"
    "zerotier:try_zerotier"
    # TIER C — 不同域名的 proxy (反劫持)
    "telegram-bot:try_telegram_bot"
    "ntfy:try_ntfy"
    "mqtt:try_mqtt"
    # TIER B — PaaS WS relay (Cloudflare IPs 几乎不被 ban)
    "cf-worker:try_cf_worker"
    "paas-relay:try_paas_relay"
    # TIER A3 — last among new ones (browser P2P, 零服务端账号)
    "trystero:try_trystero"
    # TIER D — 已有隧道作为最后兜底
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