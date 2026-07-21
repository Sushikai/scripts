#!/usr/bin/env bash
# ngrok_watch.sh — ngrok 守护,挂了就重启 + 推新 URL 到 TG
# 用法: nohup bash ngrok_watch.sh &
URL_FILE="/Users/kaikai/scripts/tunnel_url.txt"
LOG=/tmp/ngrok_remote.log
PORT=7799

restart_ngrok() {
    pkill -f "ngrok http $PORT" 2>/dev/null
    sleep 1
    ngrok http "$PORT" --log "$LOG" --log-level=info > /dev/null 2>&1 &
    NG_PID=$!
    for i in $(seq 1 30); do
        sleep 1
        URL=$(curl -s --max-time 2 http://127.0.0.1:4040/api/tunnels 2>/dev/null \
            | python3 -c "import json,sys;d=json.load(sys.stdin);t=d.get('tunnels',[]);print(t[0]['public_url'] if t else '')" 2>/dev/null)
        if [ -n "$URL" ]; then
            echo "$URL" > "$URL_FILE"
            cd /Users/kaikai/scripts
            PYTHONPATH=/Users/kaikai/scripts python3 -c "
from tuixue_v3.lib_common import send_telegram
send_telegram('''🔁 ngrok 已重启,新 URL: $URL

📡 http://localhost:$PORT  |  🌐 http://192.168.5.101:$PORT''', parse_mode='', silent=True)
"
            return 0
        fi
    done
    return 1
}

while true; do
    if ! pgrep -f "ngrok http $PORT" > /dev/null; then
        echo "[$(date '+%H:%M:%S')] ngrok 挂了,重启中"
        restart_ngrok || echo "[$(date '+%H:%M:%S')] 重启失败,60s 后重试"
    fi
    sleep 30
done
