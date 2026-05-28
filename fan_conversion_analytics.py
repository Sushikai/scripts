#!/usr/bin/env python3
"""
B站粉丝转化分析脚本
统计 fan_hunter / dm_monitor / reply_v17 三个脚本的转化效果
每2小时运行一次，输出分析报告和优化建议
"""

import json, os, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── 路径配置 ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
ANALYTICS_DIR = SCRIPT_DIR / "fan_conversion_analytics"
ANALYTICS_DIR.mkdir(exist_ok=True, parents=True)

# 数据来源
FAN_HUNTER_DIR = Path("/Users/kaikai/ai_video_project/fan_hunter")
FAN_HUNTER_ACTIONS = SCRIPT_DIR / "fan_hunter_actions.jsonl"
DM_ACTIONS = SCRIPT_DIR / "dm_actions.jsonl"
REPLY_ACTIONS = SCRIPT_DIR / "reply_actions.jsonl"

# ── Cookie 加载 ────────────────────────────────────────────────────────────
def load_bili_cookies():
    path = Path("/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt")
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return {c['name']: c['value'] for c in data}
        elif isinstance(data, dict):
            return data
    except Exception as e:
        print(f"加载 cookies 失败: {e}")
    return {}

def make_session():
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5)))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com",
    })
    return s

COOKIES = load_bili_cookies()
session = make_session()

# ── 快照管理 ──────────────────────────────────────────────────────────────
SNAPSHOT_FILE = ANALYTICS_DIR / "follower_snapshot.json"

def load_snapshot() -> dict:
    if SNAPSHOT_FILE.exists():
        try:
            return json.loads(SNAPSHOT_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}

def save_snapshot(data: dict):
    with open(SNAPSHOT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, ensure_ascii=False, indent=2, fp=f)

def get_follower_uids() -> set:
    """获取当前所有粉丝UID（不限互关）"""
    uids = set()
    mid = COOKIES.get("DedeUserID", "")
    if not mid:
        return uids
    try:
        for pn in range(1, 10):
            r = session.get(
                f"https://api.bilibili.com/x/relation/followers?mid={mid}&pn={pn}&ps=50",
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Referer": "https://www.bilibili.com",
                },
                cookies=COOKIES,
                timeout=10
            )
            d = r.json()
            if d.get('code') != 0:
                break
            items = d.get('data', {}).get('list', [])
            if not items:
                break
            for item in items:
                uid = str(item.get('mid', ''))
                if uid:
                    uids.add(uid)
            time.sleep(0.3)
    except Exception as e:
        print(f"获取粉丝列表失败: {e}")
    return uids

def get_follow_time(uid: str) -> float:
    """获取某用户关注我的时间戳"""
    try:
        r = session.get(
            f"https://api.bilibili.com/x/space/acc/info?mid={uid}",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://www.bilibili.com",
            },
            cookies=COOKIES,
            timeout=10
        )
        d = r.json()
        if d.get('code') == 0:
            return d.get('data', {}).get('fans', 0)  # 不准确，用别的方法
        return 0
    except:
        return 0

# ── 日志读写 ─────────────────────────────────────────────────────────────
def append_action(file_path: Path, action: dict):
    with open(file_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(action, ensure_ascii=False) + '\n')

def read_actions(file_path: Path) -> list:
    if not file_path.exists():
        return []
    actions = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    actions.append(json.loads(line))
    except Exception as e:
        print(f"读取 {file_path} 失败: {e}")
    return actions

# ── 转化归因 ─────────────────────────────────────────────────────────────
def get_uid_from_like_log(uid: str) -> list:
    """从 like_log.csv 获取某用户的互动时间"""
    csv_path = FAN_HUNTER_DIR / "like_log.csv"
    if not csv_path.exists():
        return []
    records = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            header = f.readline()
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 2:
                    ts = parts[0]
                    uname = parts[1].strip('"')
                    records.append({"ts": ts, "uname": uname})
    except Exception:
        pass
    return records

# ── 报告输出 ─────────────────────────────────────────────────────────────
def write_csv_report(date: str, rows: list):
    csv_path = ANALYTICS_DIR / f"daily_report_{date}.csv"
    file_exists = csv_path.exists()
    try:
        with open(csv_path, 'a', encoding='utf-8') as f:
            if not file_exists:
                f.write("timestamp,script,total_actions,converted,conversion_rate,avg_follow_days\n")
            for row in rows:
                f.write(','.join(str(x) for x in row) + '\n')
    except Exception as e:
        print(f"写CSV失败: {e}")

def write_converted_users(data: list):
    path = ANALYTICS_DIR / "converted_users.json"
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, ensure_ascii=False, indent=2, fp=f)

def write_summary(summary: dict):
    path = ANALYTICS_DIR / "conversion_summary.json"
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(summary, ensure_ascii=False, indent=2, fp=f)

def write_optimization_log(msg: str):
    log_path = ANALYTICS_DIR / "optimization_log.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")

# ── 主分析逻辑 ───────────────────────────────────────────────────────────
def analyze():
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"📊 B站粉丝转化分析 - {date_str}")
    print(f"{'='*60}")

    # 1. 加载历史快照
    old_snapshot = load_snapshot()
    old_followers = set(old_snapshot.get('uids', []))
    old_ts = old_snapshot.get('ts', '')

    # 2. 获取当前粉丝快照
    print("\n① 获取当前粉丝列表...")
    current_followers = get_follower_uids()
    print(f"  当前粉丝: {len(current_followers)} 人")
    print(f"  上次快照: {old_ts or '无'} ({len(old_followers)} 人)")

    # 保存新快照
    save_snapshot({
        "uids": list(current_followers),
        "ts": now.isoformat()
    })

    # 3. 识别新粉丝（转化候选）
    new_follower_uids = current_followers - old_followers
    print(f"  新增粉丝: {len(new_follower_uids)} 人")

    # 4. 加载各脚本的动作日志
    print("\n② 加载动作日志...")
    fh_actions = read_actions(FAN_HUNTER_ACTIONS)
    dm_actions = read_actions(DM_ACTIONS)
    rp_actions = read_actions(REPLY_ACTIONS)
    print(f"  fan_hunter 点赞: {len(fh_actions)} 条")
    print(f"  DM    发私信: {len(dm_actions)} 条")
    print(f"  reply 回复评论: {len(rp_actions)} 条")

    # 5. 建立用户动作索引
    uid_actions = defaultdict(list)  # uid -> [(timestamp, script, action_data)]
    for a in fh_actions:
        uid_actions[a.get('uid', '')].append((a.get('timestamp', ''), 'fan_hunter', a))
    for a in dm_actions:
        uid_actions[a.get('uid', '')].append((a.get('timestamp', ''), 'dm_monitor', a))
    for a in rp_actions:
        uid_actions[a.get('uid', '')].append((a.get('timestamp', ''), 'reply_v17', a))

    # 6. 归因转化
    converted = []
    script_stats = {
        'fan_hunter': {'total': 0, 'converted': 0, 'follow_days_list': []},
        'dm_monitor': {'total': 0, 'converted': 0, 'follow_days_list': []},
        'reply_v17':  {'total': 0, 'converted': 0, 'follow_days_list': []},
    }

    for uid in new_follower_uids:
        if uid not in uid_actions:
            continue  # 新粉丝但无任何互动记录
        # 取该用户所有动作，按时间排序
        sorted_actions = sorted(uid_actions[uid], key=lambda x: x[0])
        # 最近一次动作归因
        last_ts, script, action = sorted_actions[-1]

        # 计算从动作到转化的时间
        try:
            action_dt = datetime.fromisoformat(last_ts)
            follow_days = (now - action_dt).total_seconds() / 86400
        except Exception:
            follow_days = -1

        converted.append({
            'uid': uid,
            'uname': action.get('uname', uid),
            'script': script,
            'action_ts': last_ts,
            'follow_days': round(follow_days, 1),
            'action_summary': f"{script}: {action.get('action', '?')}"
        })

        if script in script_stats:
            script_stats[script]['converted'] += 1
            if follow_days >= 0:
                script_stats[script]['follow_days_list'].append(follow_days)

    # 7. 统计各脚本触达用户数
    fh_uids = set(a.get('uid') for a in fh_actions)
    dm_uids = set(a.get('uid') for a in dm_actions)
    rp_uids = set(a.get('uid') for a in rp_actions)

    # 合并同uid的去重（一条动作算一个用户）
    # 按脚本分别统计触达用户数（用于计算转化率分母）
    fh_acted_users = len([u for u in fh_uids if u])  # 有 uid 的才统计
    dm_acted_users = len([u for u in dm_uids if u])
    rp_acted_users = len([u for u in rp_uids if u])

    # 8. 输出报告
    print("\n③ 转化统计:")
    for script, stats in script_stats.items():
        if script == 'fan_hunter':
            denom = fh_acted_users or max(len(fh_uids), 1)
        elif script == 'dm_monitor':
            denom = dm_acted_users or max(len(dm_uids), 1)
        else:
            denom = rp_acted_users or max(len(rp_uids), 1)

        conv_rate = stats['converted'] / denom if denom > 0 else 0
        avg_days = sum(stats['follow_days_list']) / len(stats['follow_days_list']) if stats['follow_days_list'] else 0

        print(f"  {script:12s}: 触达 {denom:4d} 用户, 转化 {stats['converted']:3d} 人, 率 {conv_rate:.2%}, 平均 {avg_days:.1f} 天")

    # 9. 写 CSV 报告
    csv_rows = []
    for script, stats in script_stats.items():
        denom = fh_acted_users if script == 'fan_hunter' else (dm_acted_users if script == 'dm_monitor' else rp_acted_users)
        denom = denom or max(len(fh_uids if script == 'fan_hunter' else (dm_uids if script == 'dm_monitor' else rp_uids)), 1)
        conv_rate = stats['converted'] / denom if denom > 0 else 0
        avg_days = sum(stats['follow_days_list']) / len(stats['follow_days_list']) if stats['follow_days_list'] else 0
        csv_rows.append([
            now.isoformat(),
            script,
            denom,
            stats['converted'],
            f"{conv_rate:.4f}",
            f"{avg_days:.1f}"
        ])
    write_csv_report(date_str, csv_rows)

    # 10. 写归因用户列表
    write_converted_users(converted)
    print(f"\n  归因转化用户: {len(converted)} 人")

    # 11. 写汇总统计
    total_actions = len(fh_actions) + len(dm_actions) + len(rp_actions)
    total_conv = sum(s['converted'] for s in script_stats.values())
    overall_rate = total_conv / max(total_actions, 1)
    write_summary({
        'date': date_str,
        'timestamp': now.isoformat(),
        'current_followers': len(current_followers),
        'new_followers': len(new_follower_uids),
        'total_actions': total_actions,
        'total_converted': total_conv,
        'overall_conversion_rate': round(overall_rate, 4),
        'per_script': {
            s: {
                'actions': len(fh_actions if s == 'fan_hunter' else (dm_actions if s == 'dm_monitor' else rp_actions)),
                'acted_users': fh_acted_users if s == 'fan_hunter' else (dm_acted_users if s == 'dm_monitor' else rp_acted_users),
                'converted': script_stats[s]['converted'],
                'conversion_rate': round(script_stats[s]['converted'] / max(fh_acted_users if s == 'fan_hunter' else (dm_acted_users if s == 'dm_monitor' else rp_acted_users), 1), 4),
                'avg_follow_days': round(sum(script_stats[s]['follow_days_list']) / len(script_stats[s]['follow_days_list']) if script_stats[s]['follow_days_list'] else 0, 1)
            }
            for s in script_stats
        }
    })

    # 12. 优化建议
    print("\n④ 优化建议:")
    suggestions = []
    best_script = max(script_stats, key=lambda s: script_stats[s]['converted'] / max(fh_acted_users if s == 'fan_hunter' else (dm_acted_users if s == 'dm_monitor' else rp_acted_users), 1))
    worst_script = min(script_stats, key=lambda s: script_stats[s]['converted'] / max(fh_acted_users if s == 'fan_hunter' else (dm_acted_users if s == 'dm_monitor' else rp_acted_users), 1))

    suggestions.append(f"最有效: {best_script} (转化 {script_stats[best_script]['converted']} 人)")
    suggestions.append(f"最弱: {worst_script}")

    # 检查各脚本动作频率
    for script in script_stats:
        denom = fh_acted_users if script == 'fan_hunter' else (dm_acted_users if script == 'dm_monitor' else rp_acted_users)
        rate = script_stats[script]['converted'] / max(denom, 1)
        if rate < 0.01 and script_stats[script]['converted'] == 0:
            suggestions.append(f"{script}: 转化率极低，考虑调整策略（目标人群/内容类型）")
        elif rate > 0.05:
            suggestions.append(f"{script}: 转化良好，可适当增加动作量")

    for sug in suggestions:
        print(f"  - {sug}")

    write_optimization_log('\n'.join(suggestions))

    # 13. 关键词分析（从 fan_hunter 的 like_log 提取）
    print("\n⑤ 关键词效果（从点赞视频标题）:")
    keyword_conv = defaultdict(lambda: {'total': 0, 'converted': 0})
    csv_path = FAN_HUNTER_DIR / "like_log.csv"
    if csv_path.exists():
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                f.readline()  # skip header
                for line in f:
                    parts = line.strip().split(',')
                    if len(parts) >= 4:
                        ts, uname, content, video_title = parts[0], parts[1], parts[2], parts[3]
                        # 提取关键词
                        for kw in ['环球旅行', '出境游', '海外', '峰哥', '信息差', '移民', '留学', '签证', '旅游', '定居']:
                            if kw in video_title:
                                keyword_conv[kw]['total'] += 1
                                break
        except Exception:
            pass

    for uid_conv in converted:
        if uid_conv['script'] == 'fan_hunter':
            for kw in keyword_conv:
                if kw in uid_conv.get('action_summary', ''):
                    keyword_conv[kw]['converted'] += 1

    for kw, data in sorted(keyword_conv.items(), key=lambda x: x[1]['converted'], reverse=True)[:5]:
        print(f"  {kw}: 触达 {data['total']}, 转化 {data['converted']}")

    print(f"\n{'='*60}")
    print(f"✅ 分析完成 | 总动作 {total_actions} | 总转化 {total_conv} | 整体转化率 {overall_rate:.2%}")
    print(f"{'='*60}")

if __name__ == "__main__":
    analyze()