#!/bin/bash
# 每日同步脚本：自动收集所有定时任务信息，生成 定时脚本汇总.md，git push
# 运行时间：每日 00:05 (crontab: 5 0 * * *)
# 调用 Python 版本实现自动生成

cd /Users/kaikai/scripts
exec python3 /Users/kaikai/scripts/_daily_sync.py "$@"
