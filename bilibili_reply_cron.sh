#!/bin/bash
# B站自动回复 - crontab wrapper
# 清空 stale lock，准备 cookie，用 Hermes venv Python 运行

LOCK_FILE="/tmp/bili_reply_v17.lock"
LOG_FILE="/Users/kaikai/.hermes/logs/bilibili_reply_cron.log"
SCRIPT="/Users/kaikai/.hermes/instances/video_processor/scripts/bilibili_reply_v17.py"
PYTHON="/Users/kaikai/.hermes/hermes-agent/venv/bin/python3"
COOKIE_SRC="/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt"

# 清理陈旧的 lock 文件
if [ -f "$LOCK_FILE" ]; then
    OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ]; then
        if ! kill -0 "$OLD_PID" 2>/dev/null; then
            rm -f "$LOCK_FILE"
            echo "[$(date '+%m-%d %H:%M:%S')] 清除 stale lock (PID $OLD_PID 已不存在)" >> "$LOG_FILE"
        fi
    else
        rm -f "$LOCK_FILE"
    fi
fi

# 准备 Account A 的 cookie 到 /tmp（脚本第一顺位加载路径）
cp "$COOKIE_SRC" /tmp/bilibili_cookies.json

# 执行回复脚本
cd /Users/kaikai/scripts
$PYTHON "$SCRIPT" >> "$LOG_FILE" 2>&1
