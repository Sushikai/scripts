#!/bin/bash
# 每日刷新 /Users/kaikai/scripts 目录，定时脚本汇总.md + git push
# 运行时间：每日 00:05

cd /Users/kaikai/scripts

# 收集系统 cron
crontab -l > /tmp/cron_snapshot.txt 2>/dev/null

# 收集 Hermes cron (各实例)
for instance in hermes_kaikai video_processor hermes_searching; do
    f=~/.hermes/instances/$instance/cron/jobs.json
    if [ -f "$f" ]; then
        echo "=== $instance ===" >> /tmp/hermes_cron_snapshot.txt
        cat "$f" >> /tmp/hermes_cron_snapshot.txt 2>/dev/null
    fi
done

# git add + commit + push
git add -A
if [ -n "$(git status --porcelain)" ]; then
    git commit -m "auto sync: $(date '+%Y-%m-%d %H:%M')"
    git push
fi
