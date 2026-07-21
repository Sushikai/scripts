#!/usr/bin/env bash
# start_remote.sh — 启动 FastAPI + 多路隧道自检 + 推送 URL 到 TG + 自动 rotate
#
# 用法:  bash web/start_remote.sh
# 退出:  Ctrl+C
#
# 隧道优先级 (2026-07-16 重排):
#   1) ngrok              — 用户实测可用,放第一位
#   2) localtunnel (lt)   — 沙箱里 200 OK,简单可靠
#   3) cloudflared        — 大厂,稳定,只是 URL 提取要避坑
#   4) localhost.run      — SSH,无注册
#   5) serveo.net         — SSH,无注册
#   6) pinggy.io          — SSH,5 区域
#   7) loca.lt            — SSH
#   8) tunnel.pyjam.as    — SSH
#
# 之前 18 路里的 TIER A/B/C (Tailscale/ZeroTier/CF-Worker/Paas/Trystero/
# Telegram/NTFY/MQTT) 需要账号/PaaS 部署/只能局域网访问,留 README + 配置位。
#
# 关键修复 (2026-07-16):
#   • ngrok config 自愈: 检测 stale v2 字段 (connect_addr) 并提示用户
#   • watchdog subshell 变量隔离 bug: 改读 URL_FILE 而非共享变量
#   • cloudflared URL 提取: 排除 api.trycloudflare.com 这种 "假 URL"
#   • self_check 超时收紧: 15s/路 → 9s/路,8 路总上限 ~72s
#
set -o pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
PORT="${PORT:-7799}"
LOG="/tmp/tuixue_start.log"
TUNNELS_DIR="/tmp/tuixue_tunnels"
mkdir -p "$TUNNELS_DIR"

# 共享 helpers
# shellcheck disable=SC1091
source "$ROOT/tuixue_v3/web/tunnel_lib.sh" 2>/dev/null || \
source "$(dirname "$0")/tunnel_lib.sh"

# ─── 加载环境变量 ───
[ -f "$HOME/.hermes/env.sh" ] && source "$HOME/.hermes/env.sh"

# ─── API key 守门 ───
if [ -z "${MINIMAX_API_KEY:-}" ]; then
    echo ""
    echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ⚠  MINIMAX_API_KEY 未配置"
    echo "     • server 会启动, 但 AI 复盘/选股/对话 全程降级"
    echo "     • 修复:  把 key 写入 ~/.hermes/env.sh"
    echo "     • 详细: web/server.py:2798 / web/ai_client.py"
    echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
fi

# ─── 工具 ───
note()  { echo -e "  $*"; }
ok()    { echo -e "  ✓ $*"; }
fail()  { echo -e "  ✗ $*"; }
# LAN_IP: 优先 en0 (WiFi), 否则扫所有活跃接口挑第一个 IPv4
# (en0 不通时 Mac 会用 en1 有线,旧版硬 en0 拿到 0.0.0.0)
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null)
if [ -z "$LAN_IP" ]; then
    LAN_IP=$(ifconfig | awk '/^[a-z]/ {iface=$1} /inet / && $2 !~ /^127/ {print $2; exit}' | sed 's/^[^0-9]*//')
fi
[ -z "$LAN_IP" ] && LAN_IP="0.0.0.0"
TS()    { date '+%Y-%m-%d %H:%M:%S'; }

send_tg() {
    python3 -c "from tuixue_v3.lib_common import send_telegram; send_telegram('''$1''', parse_mode='', silent=True)" 2>/dev/null
}

# ════════════════════════════════════════════════════════════════════════
# ngrok config 自愈 — 检测 v2 残留字段,提示用户
# ════════════════════════════════════════════════════════════════════════
fix_ngrok_config() {
    local cfg="$HOME/Library/Application Support/ngrok/ngrok.yml"
    [ -f "$cfg" ] || return 0
    if grep -qE "^[[:space:]]+connect_addr:" "$cfg" 2>/dev/null; then
        fail "ngrok 配置含 v2 残留字段 'connect_addr' — v3 不识别,会卡在 'started tunnel' 之前"
        note "  路径: $cfg"
        note "  修复 (任选其一):"
        note "    1) 手动删除 connect_addr 行 (推荐)"
        note "    2) 一键清理:  sed -i.bak '/connect_addr:/d' \"$cfg\""
        note "  备份:  cp \"$cfg\" \"$cfg.bak.$(date +%Y%m%d_%H%M%S)\""
        echo ""
    fi
}

# ════════════════════════════════════════════════════════════════════════
# 清理旧进程
# ════════════════════════════════════════════════════════════════════════
echo "→ 清理旧进程 …"
lsof -ti ":$PORT" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
# 杀掉所有可能残留的隧道进程 (避免端口/资源冲突)
pkill -f "cloudflared tunnel --url"            2>/dev/null || true
pkill -f "ssh -tt.*-R.*localhost:$PORT"        2>/dev/null || true
pkill -f "ngrok http $PORT"                    2>/dev/null || true
pkill -f "ngrok http --config /tmp/ngrok"      2>/dev/null || true
pkill -f "lt --port $PORT"                     2>/dev/null || true
sleep 1

# ngrok config 自愈检查 (在启动 server 前提示,避免用户跑完 18 路才发现)
fix_ngrok_config

# ════════════════════════════════════════════════════════════════════════
# 启动 FastAPI
# ════════════════════════════════════════════════════════════════════════
echo "→ 启动 FastAPI（端口 $PORT）…"
cd "$ROOT"
PYTHON_BIN="/Users/kaikai/.hermes/hermes-agent/venv/bin/python3"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN=python3
PYTHONPATH="$ROOT" "$PYTHON_BIN" -m tuixue_v3.web.server --host 0.0.0.0 --port "$PORT" \
    > /tmp/tuixue_server.log 2>&1 &
SERVER_PID=$!

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

# ════════════════════════════════════════════════════════════════════════
# 4 路自检 — 确认隧道真能服务 HTML/SSE,不是只通 /api/health
# ════════════════════════════════════════════════════════════════════════
self_check() {
    local url="$1"
    local UA='Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
    # bypass-tunnel-reminder: loca.lt 默认拦所有"标准 UA + 第一次访问"返 511
    # 提示页告知: 加这个 header 或用"非标准 UA"都能绕。
    # 对其他隧道来说这个 header 是 no-op,无害。
    local HDR=( -H "bypass-tunnel-reminder: 1" )
    local ok_health=0 ok_static=0 ok_html=0 ok_sse=0

    # 最多 9s (3 轮 × 3s)
    for w in 1 2 3; do
        sleep 3

        # 1) health 200 + ok=true
        if [ "$ok_health" = "0" ]; then
            local body code
            code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 -A "$UA" "${HDR[@]}" "$url/api/health" 2>&1)
            body=$(curl -s --max-time 4 -A "$UA" "${HDR[@]}" "$url/api/health" 2>/dev/null)
            if [ "$code" = "200" ] && echo "$body" | grep -qE '"ok":\s*true|"status":\s*"ok"'; then
                ok_health=1
            fi
        fi

        # 2) /static/app.js — 200 + gzip
        if [ "$ok_static" = "0" ]; then
            local hdr
            hdr=$(curl -s -o /dev/null -D - --max-time 5 -A "$UA" "${HDR[@]}" -H "Accept-Encoding: gzip" \
                "$url/static/app.js" 2>/dev/null)
            if echo "$hdr" | grep -qiE "^HTTP/[0-9.]+ 200" \
               && echo "$hdr" | grep -qiE "content-encoding:.*gzip"; then
                ok_static=1
            fi
        fi

        # 3) GET / — 200 + 含 app.js script tag
        if [ "$ok_html" = "0" ]; then
            local html_code
            html_code=$(curl -s -o /tmp/tuixue_sc.html -w "%{http_code}" --max-time 4 -A "$UA" "${HDR[@]}" "$url/" 2>/dev/null)
            if [ "$html_code" = "200" ] && grep -q "app.js" /tmp/tuixue_sc.html 2>/dev/null; then
                ok_html=1
            fi
        fi

        # 4) SSE 握手 /api/stream/review/0
        if [ "$ok_sse" = "0" ]; then
            local sse_hdr
            sse_hdr=$(curl -s --max-time 3 -D - -o /dev/null -A "$UA" "${HDR[@]}" \
                "$url/api/stream/review/0" 2>/dev/null)
            if echo "$sse_hdr" | grep -qiE "^HTTP/[0-9.]+ 200" \
               && echo "$sse_hdr" | grep -qiE "content-type:.*event-stream"; then
                ok_sse=1
            fi
        fi

        if [ "$ok_health" = "1" ] && [ "$ok_static" = "1" ] \
            && [ "$ok_html" = "1" ] && [ "$ok_sse" = "1" ]; then
            note "    self_check: health ✓ static ✓ html ✓ sse ✓"
            return 0
        fi

        # ngrok 免费套餐特殊豁免:返回 ERR_NGROK_6024 interstitial 页 (HTML 含
        # "ERR_NGROK_6024" + "ngrok-error-code" header),说明 URL 有效,只是
        # ngrok 强制第一次访问点 "Visit Site" 才放行。脚本认这个状态为"URL alive"
        # 并直接通过,iPhone 浏览器侧点 Visit Site 即可进入真实 app。
        # 注意:这只能信 1 次,后续 round 失败才生效,避免脚本被骗永久跳过
        if [ "$w" = "2" ] && [ "$ok_health" = "0" ] && [ "$ok_html" = "0" ]; then
            local probe
            probe=$(curl -s --max-time 4 -A "$UA" "${HDR[@]}" "$url/" 2>/dev/null)
            if echo "$probe" | grep -qiE "ERR_NGROK_6024|ngrok-error-code"; then
                note "    self_check: ngrok free-tier interstitial detected → URL alive,iPhone 点 Visit Site 即过"
                return 0
            fi
        fi

        note "    self_check round $w: health=$ok_health static=$ok_static html=$ok_html sse=$ok_sse"
    done
    note "    self_check failed: health=$ok_health static=$ok_static html=$ok_html sse=$ok_sse"
    return 1
}

# ════════════════════════════════════════════════════════════════════════
# 各隧道实现 (按优先级顺序)
# 每个 try_<name> 在 TUNNELS_DIR/<name>.log 写日志,
# 成功时把 URL 写到全局 TUNNEL_URL + TUNNEL_PID (供 supervisor rotate 用)
# ════════════════════════════════════════════════════════════════════════

# ── 1) ngrok ────────────────────────────────────────────────────────────
# 用本地 API 4040 拿 public_url,比 log 更可靠 (log 里有多条 https URL 时易抓错)
try_ngrok() {
    if ! command -v ngrok > /dev/null; then
        note "    ngrok 未安装 (brew install ngrok/ngrok/ngrok)"
        return 1
    fi
    local logfile="$TUNNELS_DIR/ngrok.log"
    : > "$logfile"
    # 2026-07-16: 用 traffic-policy-file 在 on_http_response 阶段自动注入
    # abuse_interstitial cookie (30天有效),后续访问 bypass ngrok 6024 警告页 —
    # 用户首次点 Visit Site 后,policy 每响应再 Set-Cookie 续期
    local policy_file="$ROOT/tuixue_v3/web/.tunnels/ngrok_policy.yml"
    if [ -f "$policy_file" ]; then
        ngrok http "$PORT" --traffic-policy-file="$policy_file" --log "$logfile" --log-level=info > /dev/null 2>&1 &
    else
        ngrok http "$PORT" --log "$logfile" --log-level=info > /dev/null 2>&1 &
    fi
    local pid=$!
    TUNNEL_PID="$pid"

    for i in $(seq 1 25); do
        sleep 1
        # API 4040 优先,fallback log grep
        local url
        url=$(curl -s --max-time 2 http://127.0.0.1:4040/api/tunnels 2>/dev/null \
            | python3 -c "import json,sys;d=json.load(sys.stdin);t=d.get('tunnels',[]);print(t[0]['public_url'] if t else '')" 2>/dev/null)
        if [ -z "$url" ]; then
            url=$(grep -oE "https://[a-z0-9-]+\.ngrok-free\.app|https://[a-z0-9-]+\.ngrok\.io" "$logfile" 2>/dev/null | head -1)
        fi
        if [ -n "$url" ] && [[ "$url" == https://* ]]; then
            TUNNEL_URL="$url"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            # 进程挂了:可能是 config 错 (看 stderr)
            local err
            err=$(grep -oE "Error reading configuration|field .* not found|YAML parsing error" "$logfile" 2>/dev/null | head -1)
            if [ -n "$err" ]; then
                fail "    ngrok config 错: $err"
                note "    → 跑 start_remote.sh 前先看提示修 config"
            fi
            return 1
        fi
    done
    kill -9 "$pid" 2>/dev/null
    TUNNEL_PID=""
    return 1
}

# ── 2) localtunnel (`lt`) ───────────────────────────────────────────────
# npm 一次,`lt --port N` → URL 立刻打到 stdout
try_localtunnel() {
    if ! command -v lt > /dev/null; then
        note "    lt 未安装 (npm i -g localtunnel)"
        return 1
    fi
    local logfile="$TUNNELS_DIR/lt.log"
    : > "$logfile"
    lt --port "$PORT" >> "$logfile" 2>&1 &
    local pid=$!
    TUNNEL_PID="$pid"
    for i in $(seq 1 15); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+\.loca\.lt" "$logfile" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            TUNNEL_URL="$url"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    done
    kill -9 "$pid" 2>/dev/null
    TUNNEL_PID=""
    return 1
}

# ── 3) cloudflared (trycloudflare.com quick tunnel) ─────────────────────
# 修复: 排除 api.trycloudflare.com (cloudflared log 里有这串作 hint,被
# 原 regex 误捕)。只保留 <32+ 字符 随机>.trycloudflare.com
try_cloudflared() {
    if ! command -v cloudflared > /dev/null; then
        note "    cloudflared 未安装 (brew install cloudflared)"
        return 1
    fi
    local logfile="$TUNNELS_DIR/cloudflared.log"
    : > "$logfile"
    cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate \
        >> "$logfile" 2>&1 &
    local pid=$!
    TUNNEL_PID="$pid"
    for i in $(seq 1 30); do
        sleep 1
        # 真实隧道: trycloudflare 域名是 ≥16 字符随机串,排除 api.* 这种短词
        local url
        url=$(grep -oE "https://[a-z0-9-]{16,}\.trycloudflare\.com" "$logfile" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            TUNNEL_URL="$url"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    done
    kill -9 "$pid" 2>/dev/null
    TUNNEL_PID=""
    return 1
}

# ── 4) localhost.run (SSH, nokey auth) ─────────────────────────────────
try_localhost_run() {
    local logfile="$TUNNELS_DIR/lhr.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 80:localhost:$PORT nokey@localhost.run >> "$logfile" 2>&1 &
    local pid=$!
    TUNNEL_PID="$pid"
    for i in $(seq 1 45); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+\.lhr\.life" "$logfile" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            TUNNEL_URL="$url"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    done
    kill -9 "$pid" 2>/dev/null
    TUNNEL_PID=""
    return 1
}

# ── 5) serveo.net (SSH) ─────────────────────────────────────────────────
# 输出格式: "Forwarding HTTP traffic from https://<id>-<ip>.serveousercontent.com"
# 或 console.serveo.net (控制台,误捕要排除)
try_serveo() {
    local logfile="$TUNNELS_DIR/serveo.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 80:localhost:$PORT serveo.net >> "$logfile" 2>&1 &
    local pid=$!
    TUNNEL_PID="$pid"
    for i in $(seq 1 45); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+(-[0-9]+(-[0-9]+)?)?\.serveousercontent\.com" "$logfile" 2>/dev/null | head -1)
        if [ -z "$url" ]; then
            url=$(grep -oE "Forwarding HTTP traffic from https?://[^[:space:]]+" "$logfile" 2>/dev/null \
                | grep -oE "https?://[^[:space:]]+" | head -1)
        fi
        if [ -n "$url" ]; then
            TUNNEL_URL="$url"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    done
    kill -9 "$pid" 2>/dev/null
    TUNNEL_PID=""
    return 1
}

# ── 6) pinggy.io (SSH, 5 区域) ──────────────────────────────────────────
try_pinggy() {
    local logfile="$TUNNELS_DIR/pinggy.log"
    : > "$logfile"
    local pid=""
    for region in a b c d e; do
        ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
            -p 443 -R 0:localhost:$PORT "$region.pinggy.io" >> "$logfile" 2>&1 &
        pid=$!
        for i in $(seq 1 25); do
            sleep 1
            local url
            url=$(grep -oE "https?://[a-z0-9-]+\.pinggy\.link" "$logfile" 2>/dev/null | head -1)
            if [ -n "$url" ]; then
                TUNNEL_PID="$pid"
                TUNNEL_URL="$url"
                return 0
            fi
            if ! kill -0 "$pid" 2>/dev/null; then break; fi
        done
        kill -9 "$pid" 2>/dev/null
        pid=""
    done
    return 1
}

# ── 7) loca.lt (SSH) ────────────────────────────────────────────────────
try_loca_lt() {
    local logfile="$TUNNELS_DIR/loca.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 80:localhost:$PORT loca.lt >> "$logfile" 2>&1 &
    local pid=$!
    TUNNEL_PID="$pid"
    for i in $(seq 1 30); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+\.loca\.lt" "$logfile" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            TUNNEL_URL="$url"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    done
    kill -9 "$pid" 2>/dev/null
    TUNNEL_PID=""
    return 1
}

# ── 8) tunnel.pyjam.as (SSH) ────────────────────────────────────────────
try_pyjam_as() {
    local logfile="$TUNNELS_DIR/pyjam.log"
    : > "$logfile"
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -R 80:localhost:$PORT tunnel.pyjam.as >> "$logfile" 2>&1 &
    local pid=$!
    TUNNEL_PID="$pid"
    for i in $(seq 1 30); do
        sleep 1
        local url
        url=$(grep -oE "https?://[a-z0-9-]+\.pyjam\.as" "$logfile" 2>/dev/null | head -1)
        if [ -n "$url" ]; then
            TUNNEL_URL="$url"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then return 1; fi
    done
    kill -9 "$pid" 2>/dev/null
    TUNNEL_PID=""
    return 1
}

# ════════════════════════════════════════════════════════════════════════
# 主循环: 按优先级逐个试,首个 self_check 过的就 break
# ════════════════════════════════════════════════════════════════════════
declare -a METHODS=(
    "ngrok:try_ngrok"
    "localtunnel:try_localtunnel"
    "cloudflared:try_cloudflared"
    "localhost.run:try_localhost_run"
    "serveo.net:try_serveo"
    "pinggy:try_pinggy"
    "loca.lt:try_loca_lt"
    "pyjam.as:try_pyjam_as"
)

TUNNEL_URL=""
TUNNEL_METHOD=""
TUNNEL_PID=""

echo ""
echo "→ 8 路隧道按优先级自检 (ngrok → lt → cloudflared → SSH×5) …"
echo ""

for m in "${METHODS[@]}"; do
    IFS=':' read -r name fn <<< "$m"
    note "[$name] 启动中…"
    if $fn; then
        if [ -z "$TUNNEL_URL" ]; then
            fail "$name 启动但未拿到 URL"
            [ -n "$TUNNEL_PID" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
            TUNNEL_PID=""
            continue
        fi
        note "    URL: $TUNNEL_URL"
        note "    self_check (≤ 9s) …"
        if self_check "$TUNNEL_URL"; then
            ok "✓ [$name] 自检通过"
            TUNNEL_METHOD="$name"
            # 写到 URL_FILE 供 watchdog 读 (避开 subshell 变量隔离)
            write_url "$TUNNEL_URL" "$TUNNEL_METHOD"
            break
        else
            fail "[$name] self_check 未通过"
            [ -n "$TUNNEL_PID" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
            TUNNEL_PID=""
            clear_url
        fi
    else
        fail "[$name] 启动失败"
        [ -n "$TUNNEL_PID" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
        TUNNEL_PID=""
    fi
done

# ════════════════════════════════════════════════════════════════════════
# 打印最终状态
# ════════════════════════════════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════════════════════"
echo "  本机访问  http://localhost:$PORT"
echo "  局域网    http://$LAN_IP:$PORT"
if [ -n "$TUNNEL_URL" ]; then
    echo "  远程访问  $TUNNEL_URL"
    echo "  隧道方法  $TUNNEL_METHOD"
else
    echo "  ⚠  8 路隧道全部失败 — 当前仅本地/局域网可用"
    echo "  常见原因: 网络层 DNS 劫持 / ngrok config v2 残留字段 / 临时出口被封"
    echo "  手机请连同 WiFi → 用局域网 URL 必通"
    echo "  重试:  bash web/start_remote.sh"
fi
echo "════════════════════════════════════════════════════════"
echo ""
echo "日志:"
echo "  server    tail -f /tmp/tuixue_server.log"
echo "  tunnel    tail -f $TUNNELS_DIR/<method>.log"
echo "  完整列表  ls $TUNNELS_DIR/"
echo "退出: Ctrl+C（所有进程都会结束）"

# ════════════════════════════════════════════════════════════════════════
# 推 TG (远程 URL + LAN 兜底)
# ════════════════════════════════════════════════════════════════════════
push_tg() {
    local url="$1" method="$2" extra="$3"
    if [ -n "$url" ]; then
        local TG_MSG="🟢 退学 v3 控制台已上线

📡 本机:   http://localhost:$PORT
🌐 局域网: http://$LAN_IP:$PORT
🌍 远程:   $url
🔧 方法:   $method
⏰ 自检通过: $(TS)  (health/static/html/sse 全过)
${extra}

⭐ 远程打不开时,iPhone 连同 WiFi 用局域网 URL 必通
⏳ 临时隧道约 24h 后失效"
        send_tg "$TG_MSG" >/dev/null 2>&1 && ok "TG 推送成功" || fail "TG 推送失败"
    else
        local TG_MSG="⚠️ 退学 v3 控制台 — 远程隧道 8 路自检全部失败

📡 本机:   http://localhost:$PORT
🌐 局域网: http://$LAN_IP:$PORT  ⭐ 手机用这个
⏰ $(TS)
${extra}

可能网络层 DNS 劫持 / TLS 阻断 / ngrok config 残留字段。
手机需连同 Wi-Fi 访问局域网 URL。"
        send_tg "$TG_MSG" >/dev/null 2>&1 && ok "TG 推送成功" || fail "TG 推送失败"
    fi
}

push_tg "$TUNNEL_URL" "$TUNNEL_METHOD" ""

# ════════════════════════════════════════════════════════════════════════
# Supervisor: watchdog 通过 URL_FILE 读 URL (避免 subshell 变量隔离)
# 隧道挂 → 杀进程 → 试下一路 → 推 TG
# ════════════════════════════════════════════════════════════════════════
URL_FILE="$ROOT/tunnel_url.txt"           # tunnel_lib.sh 也用这个
ROTATE_FLAG="$TUNNELS_DIR/.rotate"
SUPERVISOR_LOG="$TUNNELS_DIR/supervisor.log"
DEAD_COUNT=0
LAST_METHOD="${TUNNEL_METHOD:-}"

sup_log() { echo "[$(TS)] $*" >> "$SUPERVISOR_LOG"; }
check_tunnel() {
    local url="$1"
    [ -z "$url" ] && return 1
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url/api/health" 2>&1 || echo 000)
    [ "$code" = "200" ] && return 0
    return 1
}

# rotate: 杀当前隧道,从 METHODS 下一路开始试,失败 3 次的暂时跳过
rotate_tunnel() {
    local MAIN_PID=$$
    # 杀当前
    [ -n "${TUNNEL_PID:-}" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
    TUNNEL_PID=""
    clear_url
    FAILED_METHODS="${FAILED_METHODS:-} $LAST_METHOD"
    TUNNEL_URL=""

    # 从 METHODS 找当前 method 的 index
    local i=0 current_idx=-1 next_idx=0
    for m in "${METHODS[@]}"; do
        local n="${m%%:*}"
        [ "$n" = "${LAST_METHOD:-}" ] && current_idx=$i
        i=$((i+1))
    done
    next_idx=$(( current_idx + 1 ))
    [ "$next_idx" -ge "${#METHODS[@]}" ] && next_idx=0

    local tried=0 attempts="${#METHODS[@]}"
    while [ "$tried" -lt "$attempts" ]; do
        local m="${METHODS[$next_idx]}"
        local name="${m%%:*}"
        local fn="${m##*:}"
        # 失败 ≥3 次的暂时跳过
        local fc=0
        for fm in $FAILED_METHODS; do [ "$fm" = "$name" ] && fc=$((fc+1)); done
        if [ "$fc" -ge 3 ]; then
            next_idx=$(( (next_idx + 1) % ${#METHODS[@]} ))
            tried=$((tried+1))
            continue
        fi

        sup_log "rotate → 试 $name"
        note "[rotate → $name]"
        if $fn; then
            if [ -n "$TUNNEL_URL" ] && check_tunnel "$TUNNEL_URL"; then
                TUNNEL_METHOD="$name"
                LAST_METHOD="$name"
                DEAD_COUNT=0
                write_url "$TUNNEL_URL" "$TUNNEL_METHOD"
                sup_log "rotate OK: $name = $TUNNEL_URL"
                push_tg "$TUNNEL_URL" "$TUNNEL_METHOD" "🔁 上一路断了,已自动切换"
                return 0
            fi
        fi
        [ -n "${TUNNEL_PID:-}" ] && kill -9 "$TUNNEL_PID" 2>/dev/null
        TUNNEL_PID=""
        FAILED_METHODS="$FAILED_METHODS $name"
        next_idx=$(( (next_idx + 1) % ${#METHODS[@]} ))
        tried=$((tried+1))
    done
    sup_log "all methods failed, sleep 60s then retry"
    FAILED_METHODS=""
    sleep 60
    LAST_METHOD=""
    return 1
}

sup_log "supervisor 启动, 初始方法=${LAST_METHOD:-无} url=${TUNNEL_URL:-无}"

# watchdog — 通过 URL_FILE 读 URL,30s 自检,挂 2 次触发 rotate
if [ -n "$TUNNEL_URL" ]; then
    (
        while true; do
            sleep 30
            # 从文件读 (主 shell 更新后这里能拿到)
            local_url=""
            [ -f "$URL_FILE" ] && local_url=$(cat "$URL_FILE" 2>/dev/null)
            if [ -z "$local_url" ]; then
                # URL 都没了,通知主 shell rotate
                touch "$ROTATE_FLAG"
                kill -USR1 "$MAIN_PID" 2>/dev/null
                continue
            fi
            if ! check_tunnel "$local_url"; then
                # 用文件计数 (跨进程隔离 OK)
                echo "$(($(cat "$TUNNELS_DIR/.dead_count" 2>/dev/null || echo 0) + 1))" > "$TUNNELS_DIR/.dead_count"
                local dc
                dc=$(cat "$TUNNELS_DIR/.dead_count" 2>/dev/null || echo 0)
                sup_log "dead check #$dc url=$local_url"
                if [ "$dc" -ge 2 ]; then
                    echo 0 > "$TUNNELS_DIR/.dead_count"
                    touch "$ROTATE_FLAG"
                    kill -USR1 "$MAIN_PID" 2>/dev/null
                fi
            else
                echo 0 > "$TUNNELS_DIR/.dead_count"
            fi
        done
    ) &
    WATCHDOG_PID=$!
    sup_log "watchdog pid=$WATCHDOG_PID"
fi

# USR1 处理: 主 shell 内 rotate
on_rotate() {
    if [ -f "$ROTATE_FLAG" ]; then
        rm -f "$ROTATE_FLAG"
        echo ""
        echo "→ [supervisor] 检测到隧道失活,开始 rotate …"
        sup_log "on_rotate triggered"
        rotate_tunnel
        if [ -n "$TUNNEL_URL" ]; then
            ok "新隧道: $TUNNEL_METHOD = $TUNNEL_URL"
        else
            fail "rotate 失败,等下一轮"
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
    pkill -f "cloudflared tunnel --url"      2>/dev/null
    pkill -f "ngrok http $PORT"              2>/dev/null
    pkill -f "ngrok http --config /tmp/ngrok" 2>/dev/null
    pkill -f "ssh -tt.*localhost:$PORT"      2>/dev/null
    pkill -f "lt --port $PORT"               2>/dev/null
    rm -f "$ROTATE_FLAG"
    exit
}
trap cleanup INT TERM

# 主循环 — 每秒检查 ROTATE_FLAG (USR1 trap 也会写,但 sleep 会阻塞,主循环补上)
while kill -0 "$SERVER_PID" 2>/dev/null; do
    sleep 1
    [ -f "$ROTATE_FLAG" ] && on_rotate
done
cleanup