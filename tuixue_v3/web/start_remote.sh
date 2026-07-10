#!/usr/bin/env bash
# start_remote.sh — 启动 FastAPI + 6 路隧道自检 + 推送 URL 到 TG
#
# 流程：清理旧进程 → 启动 server → 6 路隧道顺序自检（每路 curl 验通）→
#       第一个自检通过的 URL 推 TG → 等待 SIGINT
#
# 6 路逃生（全部免费）：
#   1) cloudflared 默认 (QUIC)
#   2) cloudflared --protocol http2 (TCP 走 443，QUIC 被 ban 时兜底)
#   3) cloudflared --edge-ip-version 4 (强制 v4)
#   4) localhost.run (ssh 反向，零注册)
#   5) serveo.net (ssh 反向，零注册)
#   6) ngrok (authtoken 已配，~/.ngrok2/ngrok.yml)
# + 最终兜底：LAN IP
#
# 用法:  bash start_remote.sh
# 退出:  Ctrl+C（同时结束 server 和当前 tunnel）
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
PORT="${PORT:-7799}"
LOG="/tmp/tuixue_start.log"
TUNNELS_DIR="/tmp/tuixue_tunnels"
mkdir -p "$TUNNELS_DIR"

# ─── 加载环境变量 ───
[ -f "$HOME/.hermes/env.sh" ] && source "$HOME/.hermes/env.sh"

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
    # 隧道自检：最长 30s，每 2s curl 一次 /api/health，
    # 命中 200 即通过；返回 HTML/控制台/connect-page 都视为假阳性
    for w in $(seq 1 15); do
        sleep 2
        local body
        body=$(curl -s --max-time 5 "$url/api/health" 2>/dev/null)
        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url/api/health" 2>&1)
        if [ "$code" = "200" ] && echo "$body" | grep -qE '"ok":\s*true|"status":\s*"ok"|"code":\s*"ok"'; then
            return 0
        fi
    done
    return 1
}

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

# 主流程：逐个试
declare -a METHODS=(
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
echo "→ 10 路隧道自检（每路最多 30s 拿 URL + 16s curl 验通）…"
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
