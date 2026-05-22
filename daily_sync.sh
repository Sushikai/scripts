#!/bin/bash
# 每日同步脚本：自动收集所有定时任务信息，生成 定时脚本汇总.md，git push
# 运行时间：每日 00:05 (crontab: 5 0 * * *)


SCRIPT_DIR="/Users/kaikai/scripts"
OUTPUT_FILE="$SCRIPT_DIR/定时脚本汇总.md"
CRON_TMP="/tmp/cron_snapshot.txt"
HERMES_TMP="/tmp/hermes_cron_snapshot.txt"

echo "=== [daily_sync] 开始生成定时脚本汇总 ==="
cd "$SCRIPT_DIR"

# ==================== 收集系统 cron ====================
echo "收集系统 cron..."
crontab -l > "$CRON_TMP" 2>/dev/null

# ==================== 收集 Hermes cron 各实例 ====================
echo "收集 Hermes cron..."
> "$HERMES_TMP"
for instance in hermes_kaikai video_processor hermes_searching; do
    f=~/.hermes/instances/$instance/cron/jobs.json
    if [ -f "$f" ]; then
        echo "=== $instance ===" >> "$HERMES_TMP"
        cat "$f" >> "$HERMES_TMP" 2>/dev/null
        echo "" >> "$HERMES_TMP"
    fi
done

# ==================== 验证并收集 Cookie 状态 ====================
echo "检查 Cookie 状态..."

check_cookie() {
    local label="$1"
    local sessdata="$2"
    local result=$(curl -s "https://api.bilibili.com/x/web-interface/nav" \
        -H "Cookie: SESSDATA=$sessdata; bili_jct=fcd844961a4de0c0e1ebbbe05b183fc6" 2>/dev/null)
    echo "$result" | python3 -c "
import json,sys
d=json.load(sys.stdin)
code=d.get('code',-1)
if code==0:
    data=d.get('data',{})
    uname=data.get('uname','?')
    mid=data.get('mid','?')
    print(f'✅|$label|{uname}|{mid}')
else:
    print(f'⚠️|$label|过期或无效|{code}')
" 2>/dev/null
}

# 账号A
COOKIE_A_SESSDATA="577e3116%2C1794848457%2C1661e%2A52CjD5ybsVR6H9X4F9cCN74F9w2gNdoVnSxOWky3IWFkRL5NUuT3I5aQVNAp6MpijkaN4SVjA5d2E5UGtfaXdoLVN5YTF0VEZMbU1jd0hCajNWYkpxam5OdW9QZXVLaHh3aUdjakg4czFyRDBqbXFBMExhMllvTDdtU0ZZVFZ4eV9QUG5NcWlIOWp3IIEC"
ACCOUNT_A_STATUS=$(check_cookie "账号A" "$COOKIE_A_SESSDATA")

# 账号B
COOKIE_B_FILE="$HOME/.hermes/instances/fengge_b/secrets/bilibili_cookies.txt"
if [ -f "$COOKIE_B_FILE" ]; then
    COOKIE_B_SESSDATA=$(python3 -c "import json; d=json.load(open('$COOKIE_B_FILE')); print(d.get('SESSDATA',''))" 2>/dev/null)
    if [ -n "$COOKIE_B_SESSDATA" ]; then
        ACCOUNT_B_STATUS=$(curl -s "https://api.bilibili.com/x/web-interface/nav" \
            -H "Cookie: SESSDATA=$COOKIE_B_SESSDATA" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
code=d.get('code',-1)
if code==0:
    data=d.get('data',{})
    uname=data.get('uname','?')
    mid=data.get('mid','?')
    print(f'✅|账号B|{uname}|{mid}')
else:
    print(f'⚠️|账号B|过期或无效|{code}')
" 2>/dev/null)
    else
        ACCOUNT_B_STATUS="⚠️|账号B|SESSDATA为空|"
    fi
else
    ACCOUNT_B_STATUS="⚠️|账号B|Cookie文件不存在|"
fi

# ==================== 解析 hermes_kaikai cron jobs ====================
parse_hermes_jobs() {
    local instance="$1"
    local json_file="$HOME/.hermes/instances/$instance/cron/jobs.json"
    if [ ! -f "$json_file" ]; then
        echo "# $instance: 无 cron 文件"
        return
    fi

    python3 -c "
import json, sys

def extract_cmd(prompt):
    if '执行命令：' in prompt:
        start = prompt.find('执行命令：') + 5
        end = prompt.find('\n', start)
        if end == -1:
            end = len(prompt)
        return prompt[start:end].strip()
    elif 'python3' in prompt:
        import re
        m = re.search(r'python3 ([^\n]+)', prompt)
        if m:
            return 'python3 ' + m.group(1).strip()
        m = re.search(r'/(?:[^\s\n]+)', prompt)
        if m:
            return m.group(0).strip()
    elif '/Users/kaikai' in prompt:
        import re
        m = re.search(r'/[^\s\n]+', prompt)
        if m:
            return m.group(0).strip()
    return '(prompt未识别)'

try:
    with open('$json_file') as f:
        d = json.load(f)
    jobs = d.get('jobs', [])
    print(f'# {instance} ({len(jobs)} 个任务)')
    for j in jobs:
        name = j.get('name', '?')
        sched = j.get('schedule_display', j.get('schedule', {}).get('interval', '?'), '')
        cmd = extract_cmd(j.get('prompt', ''))
        enabled = j.get('enabled', True)
        status = 'ON' if enabled else 'OFF'
        print(f'{status}|{name}|{sched}|{cmd}')
except Exception as e:
    print(f'# 解析错误: {e}')
"
}

HERMES_JOBS=$(parse_hermes_jobs "hermes_kaikai")

# ==================== 解析 video_processor cron jobs ====================
parse_video_processor_jobs() {
    local json_file="$HOME/.hermes/instances/video_processor/cron/jobs.json"
    if [ ! -f "$json_file" ]; then
        echo "# video_processor: 无 cron 文件"
        return
    fi

    python3 -c "
import json
try:
    with open('$json_file') as f:
        d = json.load(f)
    jobs = d.get('jobs', [])
    print(f'# video_processor ({len(jobs)} 个任务)')
    for j in jobs:
        name = j.get('name', '?')
        sched = j.get('schedule_display', j.get('schedule', {}).get('interval', '?'), '')
        enabled = j.get('enabled', True)
        status = 'ON' if enabled else 'OFF'
        print(f'{status}|{name}|{sched}')
except Exception as e:
    print(f'# 解析错误: {e}')
"
}

VIDEO_PROCESSOR_JOBS=$(parse_video_processor_jobs)

# ==================== 解析 hermes_searching cron jobs ====================
parse_searching_jobs() {
    local json_file="$HOME/.hermes/instances/hermes_searching/cron/jobs.json"
    if [ ! -f "$json_file" ]; then
        echo "# hermes_searching: 无 cron 文件"
        return
    fi

    python3 -c "
import json
try:
    with open('$json_file') as f:
        d = json.load(f)
    jobs = d.get('jobs', [])
    print(f'# hermes_searching ({len(jobs)} 个任务)')
    for j in jobs:
        name = j.get('name', '?')
        sched = j.get('schedule_display', '')
        enabled = j.get('enabled', True)
        status = 'ON' if enabled else 'OFF'
        prompt = j.get('prompt', '')
        # Extract script path
        import re
        m = re.search(r'python3 ([^\n]+)', prompt)
        cmd = ('python3 ' + m.group(1).strip()) if m else ''
        print(f'{status}|{name}|{sched}|{cmd}')
except Exception as e:
    print(f'# 解析错误: {e}')
"
}

SEARCHING_JOBS=$(parse_searching_jobs)

# ==================== 生成 Markdown 文档 ====================
echo "生成 Markdown 文档..."

cat > "$OUTPUT_FILE" << 'MARKDOWN_EOF'
# 定时脚本总汇

> 自动生成：%(TIMESTAMP)s
> 账号状态通过 B站 `/x/web-interface/nav` API 实时验证

---

## 一、视频制作管道

MARKDOWN_EOF

# 动态插入时间戳
sed -i '' "s/%(TIMESTAMP)s/$(date '+%Y-%m-%d %H:%M')/" "$OUTPUT_FILE"

# 系统 cron 表格
cat >> "$OUTPUT_FILE" << 'MARKDOWN_EOF'
| 开关 | 定时任务名 | 脚本路径 | 定时文件位置 | 频率 | 日志 |
|---|---|---|---|---|---|
MARKDOWN_EOF

# 解析系统 cron 并写入
python3 -c "
import subprocess, re

cron_text = open('$CRON_TMP').read()
lines = [l.strip() for l in cron_text.split('\n') if l.strip() and not l.startswith('#')]

script_map = {
    'fengge_pipeline.py':   ('/Users/kaikai/scripts/video/fengge_pipeline.py',    '系统 cron',  '~/.hermes/logs/fengge_pipeline.log'),
    'news_video_v8.py':     ('/Users/kaikai/scripts/news/news_video_v8.py',       'Hermes cron (hermes_searching)', 'ai_video_project/'),
    'bilibili_reply_v17.py':('/Users/kaikai/.hermes/instances/video_processor/scripts/bilibili_reply_v17.py', '系统 cron', '~/.hermes/logs/bilibili_reply.log'),
    'bilibili_yinliu.py':    ('/Users/kaikai/scripts/yinliu/bilibili_yinliu.py',    '系统 cron', '~/.hermes/logs/刷引流评论.log'),
    'watchdog.sh':           ('/Users/kaikai/.hermes/watchdog.sh',                 '系统 cron', '~/.hermes/logs/watchdog.log'),
    'resource_guard.sh':     ('/Users/kaikai/.hermes/resource_guard.sh',           '系统 cron', '~/.hermes/logs/resource_guard.log'),
    'fan_hunter.py':         ('/Users/kaikai/scripts/fan_hunter.py',               '系统 cron', '~/.hermes/logs/fan_hunter/cron.log'),
    'monitor.sh':            ('/Users/kaikai/.openclaw/monitor.sh',               '系统 cron', '~/.hermes/logs/monitor.log'),
    'daily_sync.sh':         ('/Users/kaikai/scripts/daily_sync.sh',             '系统 cron', '~/.hermes/logs/daily_sync.log'),
}

# 解析 crontab lines
for line in lines:
    # skip comments and empty
    if line.startswith('#') or not line:
        continue

    parts = line.split()
    if len(parts) < 6:
        continue

    cron_time = ' '.join(parts[:5])
    rest = ' '.join(parts[5:])

    # Extract script name
    script_name = ''
    path = ''
    cron_file = '系统 cron'
    log_path = ''
    freq = ''

    for name, (p, cf, lg) in script_map.items():
        if name in rest:
            script_name = name
            path = p
            cron_file = cf
            log_path = lg
            break

    if not script_name:
        continue

    # Parse frequency
    if parts[0] == '0' and parts[1] == '8':
        freq = '08:00'
    elif parts[0] == '0' and parts[1] == '20':
        freq = '20:00'
    elif parts[0] == '5' and parts[1] == '0':
        freq = '00:05'
    elif parts[0].startswith('*/'):
        freq = f'每{parts[0][2:]}分钟'
    elif parts[0] == '0' and parts[1].startswith('*/'):
        freq = f'每{parts[1][2:]}小时'
    else:
        freq = cron_time

    print(f'| ON | {script_name} | {path} | {cron_file} | {freq} | {log_path} |')
" >> "$OUTPUT_FILE"

# 账号状态
cat >> "$OUTPUT_FILE" << 'MARKDOWN_EOF'

---

## 二、账号状态

| 账号 | 昵称 | DedeUserID | Cookie状态 |
|---|---|---|---|
MARKDOWN_EOF

echo "$ACCOUNT_A_STATUS" | tr '|' ' ' | awk '{print "| "$1" | "$3" | "$4" | "$2" |"}' >> "$OUTPUT_FILE"
echo "$ACCOUNT_B_STATUS" | tr '|' ' ' | awk '{print "| "$1" | "$3" | "$4" | "$2" |"}' >> "$OUTPUT_FILE"

# Cookie 文件位置说明
cat >> "$OUTPUT_FILE" << 'MARKDOWN_EOF'

**Cookie 文件位置：**
- 账号A：`~/.hermes/secrets/bilibili_cookies_A.netscape.txt`（也存在于 `/tmp/bilibili_cookies.json`）
- 账号B：`~/.hermes/instances/fengge_b/secrets/bilibili_cookies.txt` + `bilibili_cookies.netscape.txt`

MARKDOWN_EOF

# Hermes Agent Cron
cat >> "$OUTPUT_FILE" << 'MARKDOWN_EOF'

---

## 三、Hermes Agent Cron（多账号汇总）

### hermes_kaikai
MARKDOWN_EOF

echo "$HERMES_JOBS" | grep '^ON\|^OFF' | while IFS='|' read -r status name sched cmd; do
    echo "| $status | $name | $sched | $cmd |" >> "$OUTPUT_FILE"
done

cat >> "$OUTPUT_FILE" << 'MARKDOWN_EOF'

### video_processor
MARKDOWN_EOF

echo "$VIDEO_PROCESSOR_JOBS" | grep '^ON\|^OFF' | while IFS='|' read -r status name sched; do
    echo "| $status | $name | $sched | - |" >> "$OUTPUT_FILE"
done

cat >> "$OUTPUT_FILE" << 'MARKDOWN_EOF'

### hermes_searching
MARKDOWN_EOF

echo "$SEARCHING_JOBS" | grep '^ON\|^OFF' | while IFS='|' read -r status name sched cmd; do
    echo "| $status | $name | $sched | $cmd |" >> "$OUTPUT_FILE"
done

# 重要提示
cat >> "$OUTPUT_FILE" << 'MARKDOWN_EOF'

---

## 四、重要提示

- **fengge_pipeline.py COOKIES**：硬编码在脚本顶部，cookie 过期需改源码（当前账号A有效）
- **账号B COOKIES**：存储在 `~/.hermes/instances/fengge_b/secrets/`，当前有效（SESSDATA 到期 1794931778）
- **每日同步**：`/Users/kaikai/scripts/daily_sync.sh` 每日 00:05 运行，git push 到 github.com/Sushikai/scripts

---

## 五、查看命令

```bash
# 系统 cron
crontab -l

# Hermes cron（各实例）
cat ~/.hermes/instances/{instance}/cron/jobs.json | python3 -c "
import json,sys
d=json.load(sys.stdin)
for j in d.get('jobs',[]):
    print(j['name'], '|', j['schedule_display'])
"

# 日志
tail -f ~/.hermes/logs/{bilibili_reply,刷引流评论,watchdog,resource_guard,fengge_pipeline}.log
```
MARKDOWN_EOF

echo "=== [daily_sync] Markdown 生成完成 ==="

# ==================== Git push ====================
echo "=== [daily_sync] Git push ==="
git add -A
if [ -n "$(git status --porcelain)" ]; then
    git commit -m "auto sync: $(date '+%Y-%m-%d %H:%M')"
    git push
    echo "=== [daily_sync] Git push 完成 ==="
else
    echo "=== [daily_sync] 无变化，跳过 commit ==="
fi

echo "=== [daily_sync] 完成 ==="