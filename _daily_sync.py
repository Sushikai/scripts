#!/usr/bin/env python3
"""每日同步脚本：自动收集所有定时任务信息，生成 定时脚本汇总.md，git push"""
import json, subprocess, urllib.parse, base64, datetime, os, sys
from pathlib import Path

SCRIPT_DIR = Path("/Users/kaikai/scripts")
OUTPUT_FILE = SCRIPT_DIR / "定时脚本汇总.md"
CRON_TMP = Path("/tmp/cron_snapshot.txt")
HERMES_TMP = Path("/tmp/hermes_cron_snapshot.txt")

TIMESTAMP = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

# ==================== 收集系统 cron ====================
print("收集系统 cron...")
result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
CRON_TMP.write_text(result.stdout if result.returncode == 0 else "")

# ==================== 收集 Cookie 状态 ====================
print("检查 Cookie 状态...")

def check_sessdata(sessdata: str, bili_jct: str, label: str) -> dict:
    try:
        import requests
        r = requests.get("https://api.bilibili.com/x/web-interface/nav",
                        headers={"Cookie": f"SESSDATA={sessdata}; bili_jct={bili_jct}"},
                        timeout=10)
        d = r.json()
        if d.get('code') == 0:
            data = d.get('data', {})
            return {"ok": True, "label": label, "uname": data.get('uname', '?'), "mid": data.get('mid', '?')}
        return {"ok": False, "label": label, "uname": "过期或无效", "mid": str(d.get('code', ''))}
    except:
        return {"ok": False, "label": label, "uname": "检测失败", "mid": ""}

# 账号A（从文件读取，不要硬编码）
_account_a_cookies = Path("/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt")
if _account_a_cookies.exists():
    try:
        _data = json.loads(_account_a_cookies.read_text())
        ACCOUNT_A = check_sessdata(_data.get('SESSDATA', ''), _data.get('bili_jct', ''), "账号A")
    except Exception as e:
        ACCOUNT_A = {"ok": False, "label": "账号A", "uname": f"读取失败: {e}", "mid": ""}
else:
    ACCOUNT_A = {"ok": False, "label": "账号A", "uname": "Cookie文件不存在", "mid": ""}

# 账号B
COOKIE_B_FILE = Path.home() / ".hermes/instances/fengge_b/secrets/bilibili_cookies.txt"
if COOKIE_B_FILE.exists():
    try:
        cb = json.loads(COOKIE_B_FILE.read_text())
        ACCOUNT_B = check_sessdata(cb.get('SESSDATA', ''), cb.get('bili_jct', ''), "账号B")
    except:
        ACCOUNT_B = {"ok": False, "label": "账号B", "uname": "读取失败", "mid": ""}
else:
    ACCOUNT_B = {"ok": False, "label": "账号B", "uname": "Cookie文件不存在", "mid": ""}

# ==================== 解析各实例 Hermes cron ====================
def parse_hermes_jobs(instance: str) -> list:
    json_file = Path.home() / f".hermes/instances/{instance}/cron/jobs.json"
    if not json_file.exists():
        return []
    try:
        d = json.loads(json_file.read_text())
        jobs = d.get('jobs', [])
        result = []
        for j in jobs:
            name = j.get('name', '?')
            sched = j.get('schedule_display', '')
            if not sched:
                sched_obj = j.get('schedule', {})
                if isinstance(sched_obj, dict):
                    sched = sched_obj.get('interval', sched_obj.get('cron', ''))
            prompt = j.get('prompt', '')

            # Extract command path
            cmd = ''
            if '执行命令：' in prompt:
                start = prompt.find('执行命令：') + 5
                end = prompt.find('\n', start)
                cmd = prompt[start:end].strip() if end != -1 else prompt[start:].strip()
            elif 'python3' in prompt:
                import re
                m = re.search(r'python3 ([^\n]+)', prompt)
                if m:
                    cmd = 'python3 ' + m.group(1).strip()
            elif '/Users/kaikai' in prompt:
                import re
                m = re.search(r'/[^\s\n]+', prompt)
                if m:
                    cmd = m.group(0).strip()

            enabled = j.get('enabled', True)
            result.append({"status": "ON" if enabled else "OFF", "name": name,
                          "schedule": sched, "cmd": cmd, "instance": instance})
        return result
    except Exception as e:
        print(f"  解析 {instance} 出错: {e}")
        return []

hermes_kaikai_jobs = parse_hermes_jobs("hermes_kaikai")
video_processor_jobs = parse_hermes_jobs("video_processor")
hermes_searching_jobs = parse_hermes_jobs("hermes_searching")

# ==================== 解析系统 cron ====================
system_cron = []
for line in CRON_TMP.read_text().split('\n'):
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    parts = line.split()
    if len(parts) < 6:
        continue

    time_parts = parts[:5]
    rest = ' '.join(parts[5:])

    script_map = {
        'fengge_pipeline.py':    ('/Users/kaikai/scripts/video/fengge_pipeline.py',    '~/.hermes/logs/fengge_pipeline.log'),
        'news_video_v8.py':      ('/Users/kaikai/scripts/news/news_video_v8.py',       'ai_video_project/'),
        'bilibili_reply_v17.py': ('/Users/kaikai/.hermes/instances/video_processor/scripts/bilibili_reply_v17.py', '~/.hermes/logs/bilibili_reply.log'),
        'bilibili_yinliu.py':    ('/Users/kaikai/scripts/yinliu/bilibili_yinliu.py',    '~/.hermes/logs/刷引流评论.log'),
        'watchdog.sh':           ('/Users/kaikai/.hermes/watchdog.sh',                 '~/.hermes/logs/watchdog.log'),
        'resource_guard.sh':    ('/Users/kaikai/.hermes/resource_guard.sh',           '~/.hermes/logs/resource_guard.log'),
        'fan_hunter.py':         ('/Users/kaikai/scripts/fan_hunter.py',               '~/.hermes/logs/fan_hunter/cron.log'),
        'monitor.sh':            ('/Users/kaikai/.openclaw/monitor.sh',               '~/.hermes/logs/monitor.log'),
        'daily_sync.sh':         ('/Users/kaikai/scripts/daily_sync.sh',             '~/.hermes/logs/daily_sync.log'),
    }

    script_name = None
    for name, (path, log_path) in script_map.items():
        if name in rest:
            script_name = name
            break

    if not script_name:
        continue

    # Parse frequency
    freq = ' '.join(time_parts)
    if time_parts[0] == '0' and time_parts[1] == '8':
        freq = '08:00'
    elif time_parts[0] == '0' and time_parts[1] == '20':
        freq = '20:00'
    elif time_parts[0] == '5' and time_parts[1] == '0':
        freq = '00:05'
    elif time_parts[0].startswith('*/'):
        freq = f'每{time_parts[0][2:]}分钟'
    elif time_parts[0] == '0' and time_parts[1].startswith('*/'):
        freq = f'每{time_parts[1][2:]}小时'

    system_cron.append({
        "name": script_name,
        "path": script_map[script_name][0],
        "freq": freq,
        "log": script_map[script_name][1],
    })

# ==================== 生成 Markdown ====================
print("生成 Markdown 文档...")

lines = []
lines.append("# 定时脚本总汇")
lines.append("")
lines.append(f"> 自动生成：{TIMESTAMP}")
lines.append("> 账号状态通过 B站 `/x/web-interface/nav` API 实时验证")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 一、系统 Cron（所有账号通用）")
lines.append("")
lines.append("| 开关 | 定时任务名 | 脚本路径 | 定时文件位置 | 频率 | 日志 |")
lines.append("|---|---|---|---|---|---|")
for job in system_cron:
    lines.append(f"| ON | {job['name']} | {job['path']} | 系统 cron | {job['freq']} | {job['log']} |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 二、账号状态")
lines.append("")
lines.append("| 账号 | 昵称 | DedeUserID | Cookie状态 |")
lines.append("|---|---|---|---|")
for acc in [ACCOUNT_A, ACCOUNT_B]:
    status_icon = "✅" if acc['ok'] else "⚠️"
    lines.append(f"| {status_icon} | {acc['uname']} | {acc['mid']} | {acc['label']} |")

lines.append("")
lines.append("**Cookie 文件位置：**")
lines.append("- 账号A：`~/.hermes/secrets/bilibili_cookies_A.netscape.txt`（也存在于 `/tmp/bilibili_cookies.json`）")
lines.append("- 账号B：`~/.hermes/instances/fengge_b/secrets/bilibili_cookies.txt` + `bilibili_cookies.netscape.txt`")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 三、Hermes Agent Cron")
lines.append("")
lines.append("### hermes_kaikai")
lines.append("")
lines.append("| 开关 | 定时任务名 | 频率 | 脚本路径 |")
lines.append("|---|---|---|---|")
for j in hermes_kaikai_jobs:
    lines.append(f"| {j['status']} | {j['name']} | {j['schedule']} | {j['cmd']} |")

lines.append("")
lines.append("### video_processor")
lines.append("")
lines.append("| 开关 | 定时任务名 | 频率 | 脚本路径 |")
lines.append("|---|---|---|---|")
for j in video_processor_jobs:
    lines.append(f"| {j['status']} | {j['name']} | {j['schedule']} | - |")

lines.append("")
lines.append("### hermes_searching")
lines.append("")
lines.append("| 开关 | 定时任务名 | 频率 | 脚本路径 |")
lines.append("|---|---|---|---|")
for j in hermes_searching_jobs:
    lines.append(f"| {j['status']} | {j['name']} | {j['schedule']} | {j['cmd']} |")

lines.append("")
lines.append("---")
lines.append("")
lines.append("## 四、重要提示")
lines.append("")
lines.append("- **fengge_pipeline.py COOKIES**：硬编码在脚本顶部，cookie 过期需改源码（当前账号A有效）")
lines.append("- **账号B COOKIES**：存储在 `~/.hermes/instances/fengge_b/secrets/`，当前有效（SESSDATA 到期 1794931778）")
lines.append("- **每日同步**：`/Users/kaikai/scripts/daily_sync.sh` 每日 00:05 运行，git push 到 github.com/Sushikai/scripts")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 五、查看命令")
lines.append("")
lines.append("```bash")
lines.append("# 系统 cron")
lines.append("crontab -l")
lines.append("")
lines.append("# Hermes cron（各实例）")
lines.append("cat ~/.hermes/instances/{instance}/cron/jobs.json | python3 -c \"")
lines.append("import json,sys")
lines.append("d=json.load(sys.stdin)")
lines.append("for j in d.get('jobs',[]):")
lines.append("    print(j['name'], '|', j['schedule_display'])")
lines.append("\"")
lines.append("")
lines.append("# 日志")
lines.append("tail -f ~/.hermes/logs/{bilibili_reply,刷引流评论,watchdog,resource_guard,fengge_pipeline}.log")
lines.append("```")

OUTPUT_FILE.write_text('\n'.join(lines), encoding='utf-8')
print(f"文档已生成: {OUTPUT_FILE}")

# ==================== Git push ====================
print("Git push...")
os.chdir(SCRIPT_DIR)
subprocess.run(['git', 'add', '-A'], capture_output=True)
status = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
if status.stdout.strip():
    subprocess.run(['git', 'commit', '-m', f'auto sync: {TIMESTAMP}'], capture_output=True)
    result = subprocess.run(['git', 'push'], capture_output=True, text=True)
    if result.returncode == 0:
        print("Git push 完成")
    else:
        print(f"Git push 失败: {result.stderr}")
else:
    print("无变化，跳过 commit")

print("=== 完成 ===")