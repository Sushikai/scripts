#!/usr/bin/env python3
"""
B站评论清理脚本 - 删除本人发布的多余内容（模型思考过程等异常输出）
通过消息流中的 root_reply_content 识别我之前发的含思考过程的评论并删除
"""
import fcntl, json, os, random, signal, sys, time
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).parent))
from bilibili_utils import make_session, atomic_write

_INSTANCE = os.environ.get('BILIBILI_INSTANCE', '')
if _INSTANCE:
    _BASE = Path.home() / ".hermes" / "instances" / _INSTANCE
    _INSTANCE_SECRETS = _BASE / "secrets"
    _INSTANCE_WORK = _BASE / "work"
    _INSTANCE_WORK.mkdir(parents=True, exist_ok=True)
else:
    _INSTANCE_SECRETS = None
    _INSTANCE_WORK = None

if _INSTANCE_SECRETS:
    _ns = _INSTANCE_SECRETS / "bilibili_cookies.netscape.txt"
    _dict = _INSTANCE_SECRETS / "bilibili_cookies.txt"
    COOKIES_FILE = _ns if _ns.exists() else (_dict if _dict.exists() else _ns)
else:
    COOKIES_FILE = Path.home() / ".hermes/secrets/bilibili_cookies_A.netscape.txt"

def load_cookies():
    json_file = Path("/tmp/bilibili_cookies.json")
    if json_file.exists():
        try:
            data = json.loads(json_file.read_text(encoding='utf-8'))
            if isinstance(data, dict) and 'SESSDATA' in data:
                return data
        except Exception:
            pass
    cookies = {}
    if COOKIES_FILE.exists():
        for line in COOKIES_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
    return cookies

COOKIES = load_cookies()
BILI_JCT = COOKIES.get("bili_jct", "")

HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com"
}

DELETE_STORE = _INSTANCE_WORK / "bili_deleted_cleanup.json" if _INSTANCE_WORK else Path("/tmp/bili_deleted_cleanup.json")
LOG_FILE = _INSTANCE_WORK / "bili_cleanup.log" if _INSTANCE_WORK else Path("/tmp/bili_cleanup.log")
session = make_session()

THINKING_PATTERNS = [
    '让我分析', '根据上文', '首先', '其次', '最后', '总结', '综合来看',
    '【分析】', '【回复】', '好的，用户', '粉丝昵称是', '请直接生成',
    '以B站UP主', '你是B站UP主', '回复粉丝', '结合粉丝'
]

def log(msg):
    ts = time.strftime('%m-%d %H:%M:%S')
    print(f"[{ts}] {msg}")
    sys.stdout.flush()

def load_deleted_store():
    try:
        return json.loads(DELETE_STORE.read_text(encoding='utf-8'))
    except Exception:
        return {}

def save_deleted_store(store):
    atomic_write(DELETE_STORE, json.dumps(store, ensure_ascii=False, indent=2))

def get_my_mid():
    try:
        r = session.get("https://api.bilibili.com/x/web-interface/nav", headers=HEADERS, cookies=COOKIES, timeout=10)
        j = r.json()
        if j.get('code') == 0:
            return j['data']['mid'], j['data']['uname']
        return None, None
    except Exception:
        return None, None

def delete_reply(oid, rpid):
    for attempt in range(2):
        try:
            r = session.post(
                "https://api.bilibili.com/x/v2/reply/del",
                data={"oid": oid, "rpid": rpid, "csrf": BILI_JCT},
                headers=HEADERS, cookies=COOKIES, timeout=10
            )
            j = r.json()
            if j.get('code') == 0:
                return True
            elif j.get('code') == 12002:
                log(f"  ⏳ 风控，等待60秒...")
                time.sleep(60)
                continue
            else:
                log(f"  B站: {j.get('message')} (code={j.get('code')})")
                return False
        except Exception as e:
            if attempt == 0:
                time.sleep(3)
                continue
            return False
    return False

def scan_and_delete():
    """
    消息流结构解析（type=reply通知）：
    - source_content:   对方最新评论（回复我的那条）
    - root_reply_content: 我之前发的那条评论（触发对方回复的根本原因）
    - source_id:        我那条评论的rpid
    - subject_id:        视频avid
    当 root_reply_content 含思考过程关键词时，说明我之前发的评论有问题，需要删除
    """
    deleted_store = load_deleted_store()
    total_deleted = 0
    page = 0
    cursor_id = 0

    log("📡 开始扫描消息流...")

    while True:
        page += 1
        time.sleep(random.uniform(1.0, 2.0))

        url = f"https://api.bilibili.com/x/msgfeed/reply?pn={page}&ps=20"
        if cursor_id:
            url += f"&id_cursor={cursor_id}"

        try:
            r = session.get(url, headers=HEADERS, cookies=COOKIES, timeout=10)
            j = r.json()
            if j.get('code') != 0:
                log(f"  ❌ API: {j.get('message')}")
                break

            items = j.get('data', {}).get('items', [])
            cursor = j.get('data', {}).get('cursor', {})
            cursor_id = cursor.get('id', 0)

            if not items:
                log(f"  ✅ 第{page}页无消息，结束")
                break

            log(f"  📄 第{page}页，{len(items)}条...")

            reply_count = sum(1 for item in items if item.get('item', {}).get('type') == 'reply')
            log(f"     (其中回复类通知 {reply_count} 条)")

            for item in items:
                item_type = item.get('item', {}).get('type', '')
                if item_type != 'reply':
                    continue

                inner = item.get('item', {})
                subject_id = str(inner.get('subject_id', ''))
                source_id = str(inner.get('source_id', ''))  # 我之前发的那条评论的rpid
                target_id = str(inner.get('target_id', ''))
                root_id = str(inner.get('root_id', '0'))
                root_reply_content = inner.get('root_reply_content', '').strip()
                source_content = inner.get('source_content', '').strip()
                uname = item.get('user', {}).get('nickname', '粉丝')

                key = f"{subject_id}_{source_id}"
                if key in deleted_store:
                    continue

                # root_reply_content 是我之前发的那条评论内容
                if not root_reply_content:
                    continue

                has_thinking = any(p in root_reply_content for p in THINKING_PATTERNS)
                if not has_thinking:
                    continue

                log(f"  ⚠️ 发现异常回复: aid={subject_id} rpid={source_id}")
                log(f"     我「{uname}」: {root_reply_content[:60]}...")

                # 删除我的这条评论
                if delete_reply(subject_id, source_id):
                    deleted_store[key] = root_reply_content[:100]
                    save_deleted_store(deleted_store)
                    total_deleted += 1
                    log(f"     ✅ 已删除")
                else:
                    log(f"     ❌ 删除失败")

                time.sleep(random.uniform(3.0, 6.0))

            if cursor.get('is_end', True):
                break

        except Exception as e:
            log(f"  ❌ 第{page}页异常: {e}")
            continue

    log(f"✅ 完成: 本次删除 {total_deleted} 条 | 历史累计 {len(deleted_store)} 条")
    return total_deleted

def main():
    log("🧹 B站评论清理脚本启动")
    my_mid, my_uname = get_my_mid()
    if not my_mid:
        log("❌ 无法获取用户信息")
        return
    log(f"  👤 {my_uname} (mid={my_mid})")

    before = len(load_deleted_store())
    scan_and_delete()
    after = len(load_deleted_store())
    log(f"✅ 清理完成: 新增删除 {after - before} 条 | 共 {after} 条")

if __name__ == "__main__":
    main()