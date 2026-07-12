#!/usr/bin/env bash
# tunnel_lib.sh — shared helpers for start_remote.sh and the per-backend scripts.
# Centralizes URL discovery, health check, TG push, LAN detection.
# Source with:  source "$(dirname "$0")/tunnel_lib.sh"

# ──────────── Constants ────────────
TUNNELS_DIR="${TUNNELS_DIR:-/tmp/tuixue_tunnels}"
SUPERVISOR_LOG="${SUPERVISOR_LOG:-$TUNNELS_DIR/supervisor.log}"
PORT="${PORT:-7799}"
LAN_IP="${LAN_IP:-$(ipconfig getifaddr en0 2>/dev/null || echo '0.0.0.0')}"
ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
URL_FILE="${URL_FILE:-$ROOT_DIR/tunnel_url.txt}"
METHOD_FILE="${METHOD_FILE:-$ROOT_DIR/tunnel_method.txt}"
ROTATE_FLAG="${ROTATE_FLAG:-$TUNNELS_DIR/.rotate}"
TUNNEL_TIMEOUT="${TUNNEL_TIMEOUT:-30}"   # generous; lanes skip on their own

mkdir -p "$TUNNELS_DIR"

# ──────────── Logging ────────────
ts()  { date '+%Y-%m-%d %H:%M:%S'; }
note(){ echo -e "  $*"; }
ok()  { echo -e "  ✓ $*"; }
fail(){ echo -e "  ✗ $*"; }

# ──────────── URL file IO ────────────
write_url() {
    local url="$1" method="${2:-}"
    echo "$url" > "$URL_FILE"
    [[ -n "$method" ]] && echo "$method" > "$METHOD_FILE"
}

clear_url() {
    rm -f "$URL_FILE" "$METHOD_FILE"
}

# ──────────── Health check ────────────
# Different from self_check (which does full /api/health + /static + SSE handshake
# used for tunnel URL verification on first launch). This one is the *runtime*
# health probe used by the supervisor every 30s — single endpoint, fast timeout.
tunnel_health_check() {
    local url="$1"
    [[ -z "$url" ]] && return 1
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url/api/health" 2>&1 || echo 000)
    [[ "$code" == "200" ]] && return 0
    return 1
}

# ──────────── TG push ────────────
send_tg() {
    local msg="$1"
    [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]] && return 1
    PYTHONPATH="${PYTHONPATH:-$ROOT_DIR/..}" python3 -c "
from tuixue_v3.lib_common import send_telegram
import sys
try:
    send_telegram('''$msg''', parse_mode='', silent=True)
except Exception as e:
    sys.exit(1)
" 2>/dev/null
}

# ──────────── URL extractors ────────────
# Each backend stores logs into $TUNNELS_DIR/<name>.log with conventions.
# Centralize so any new backend can plug in.

url_from_logfile() {
    local log="$1" pattern="$2" timeout="${3:-$TUNNEL_TIMEOUT}"
    local url=""
    for i in $(seq 1 "$timeout"); do
        sleep 1
        url=$(grep -oE "$pattern" "$log" 2>/dev/null | head -1)
        [[ -n "$url" ]] && { echo "$url"; return 0; }
        [[ -s "$log" ]] || return 1
    done
    return 1
}

# ──────────── Process management ────────────
kill_pattern() {
    local pat="$1"
    pkill -f "$pat" 2>/dev/null || true
    sleep 0.3
}

running_pid() {
    local pat="$1"
    pgrep -f "$pat" 2>/dev/null | grep -v "$$" | head -1
}

# ──────────── Sentinel for non-URL backends ────────────
# TG-bot, MQTT bridges don't have a public URL. They write a "ready" sentinel
# file with their connection info that the supervisor reads; UI displays it as
# text instructions. Returns the path to the sentinel on success.
write_sentinel() {
    local name="$1" info="$2"
    local sentinel="$TUNNELS_DIR/$name.ready"
    cat > "$sentinel" <<EOF
mechanism: $name
url: $info
ready_at: $(ts)
EOF
    echo "$sentinel"
}

# ──────────── Reachability probe ────────────
# Quick check whether a domain actually answers (DNS resolves + TLS handshake).
# Used to short-circuit URL-discovery wait when target is unreachable.
probe_target() {
    local url="$1" timeout="${2:-5}"
    curl -s -o /dev/null -w "%{http_code}" --max-time "$timeout" "$url" 2>/dev/null
    # 000 = unreachable, 2xx/3xx = reachable, 4xx = reachable but blocked
}

# ──────────── One-shot dispatcher for new mechanisms ────────────
# Each backend gets a `try_<name>` function returning 0 on success (URL/sentinel
# written), 1 otherwise. Used by start_remote.sh and start_tunnel_only.sh.
#
# Conventions for new functions:
#   - on success: write_url() or write_sentinel()
#   - on success: set TUNNEL_PID (the process to kill on stop / rotate)
#   - on failure: return 1, don't fill TUNNEL_PID

# helper: read user's persistent bridge URLs
relay_urls_file() {
    local f="$HOME/.config/tuixue/relays.json"
    [[ -f "$f" ]] && echo "$f" || true
}
