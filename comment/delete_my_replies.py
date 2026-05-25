#!/usr/bin/env python3
"""
B站评论清理 - 找到自己发过的回复并删除（browser方案兜底）
"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bilibili_utils import make_session

# ========== Cookie 加载（和 bilibili_reply_v17.py 完全一样）==========
def load_cookies():
    cookies = {}
    json_file = Path("/tmp/bilibili_cookies.json")
    if json_file.exists():
        try:
            data = json.loads(json_file.read_text(encoding='utf-8'))
            if isinstance(data, dict) and 'SESSDATA' in data:
                return data
        except Exception:
            pass
    json_file2 = Path.home() / ".bilibili_cookies.json"
    if json_file2.exists():
        try:
            data = json.loads(json_file2.read_text(encoding='utf-8'))
            if isinstance(data, list):
                return {item['name']: item['value'] for item in data if 'name' in item and 'value' in item}
        except Exception:
            pass
    netscape_file = Path.home() / ".hermes/secrets/bilibili_cookies_A.netscape.txt"
    if not netscape_file.exists():
        netscape_file = Path.home() / ".hermes/secrets/bilibili_cookies_netscape.txt"
    try:
        for line in netscape_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
    except Exception:
        pass
    return cookies

COOKIES = load_cookies()
SESSDATA = COOKIES.get("SESSDATA", "")
BILI_JCT = COOKIES.get("bili_jct", "")

HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com"
}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)

# Arthur 的视频列表（已知）
VIDEO_AIDS = [
    116624015235928,  # BV1ccGB6gEPo 峰哥读大冰乖摸摸头怒喷作者脑袋被驴踢了
    116623327367108,  # BV1Q3Gi69Eht 峰哥每天都有商单羡慕了
    116623327430326,  # BV1G3Gi6REYB 我俩要是黄了肯定跟峰哥有关系
    116623327430301,  # BV1G3Gi6REaZ 感谢峰哥送来的祝福
    116621179882482,  # BV14dGa6ME1u 峰哥当面表白吴艳妮吓坏吴艳妮
    116621179882188,  # BV14dGa6ME6x 感谢峰哥送来的祝福
    116619149838217,  # BV1rxGa6ZEkZ 人的生命中点是18岁，后面会越过越快
    116619099506788,  # BV1rsGa6bEdi 一句就是穷哥们给我送进去的，大半夜给我听乐了
    116617656666665,  # BV1YvGt6KESv 峰哥疯狂输出卖弄知识羞辱粉丝只为博旁边佳人一笑
    116613747574297,  # BV1RxLY6tEdg 峰哥复播狂赚几十万徐静雨嫉妒了嘲讽峰哥不是男人
]

MY_MID = "140289989"

# 思考过程关键字
THINK_KEYWORDS = [
    '让我分析', '根据上文', '首先', '其次', '最后', '总结', '综合来看',
    '【分析】', '【回复】', '好的，', '好的，让我', '我来帮',
    '你是在说', '你的意思是', '从你的描述',
    '**', '##', '```', '一步步', '整体来看',
]

def delete_reply(aid: int, rpid: int, session) -> bool:
    """删除评论"""
    payload = {
        "oid": aid,
        "rpid": rpid,
        "type": 1,
        "csrf": BILI_JCT,
    }
    try:
        r = session.post(
            "https://api.bilibili.com/x/v2/reply/delete",
            data=payload,
            headers=HEADERS,
            cookies=COOKIES,
            timeout=10
        )
        j = r.json()
        code = j.get('code', -1)
        if code == 0:
            log(f"  ✅ 删除成功 rpid={rpid}")
            return True
        elif code == -3 or code == 12022:
            log(f"  ⚠️ 删除权限不足 rpid={rpid} code={code}")
            return False
        else:
            log(f"  ❌ 删除失败 rpid={rpid} code={code} msg={j.get('message','')}")
            return False
    except Exception as e:
        log(f"  ❌ 请求异常 rpid={rpid}: {e}")
        return False


def scan_video(aid: int, session, dry_run=True) -> list:
    """扫描一个视频的评论区，找自己的回复"""
    found = []
    page = 1
    while True:
        r = session.get(
            f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=2&ps=20&pn={page}&sort=2",
            headers=HEADERS, cookies=COOKIES, timeout=10
        )
        try:
            j = r.json()
        except:
            break
        if j.get('code') != 0:
            break
        replies = j.get('data', {}).get('replies', []) or []
        if not replies:
            break

        for rep in replies:
            rep_mid = rep.get('mid', '')
            content = rep.get('content', {})
            message = content.get('message', '')
            rpid = rep.get('rpid')
            parent_rpid = rep.get('parent_rpid', 0)

            # 只看自己的回复（root != 0 说明是回复别人的评论）
            is_my_reply = (str(rep_mid) == MY_MID and parent_rpid != 0)

            if is_my_reply:
                has_think = any(kw in message for kw in THINK_KEYWORDS)
                found.append({
                    'aid': aid,
                    'rpid': rpid,
                    'message': message[:100],
                    'parent_rpid': parent_rpid,
                    'has_think': has_think,
                })

            # 检查子回复（楼中楼）
            all_children = content.get('replies', []) or []
            for child in all_children:
                child_mid = child.get('mid', '')
                child_message = child.get('content', {}).get('message', '')
                child_rpid = child.get('rpid')
                child_parent = child.get('parent_rpid', 0)
                is_my_child = (str(child_mid) == MY_MID and child_parent != 0)
                if is_my_child:
                    has_think = any(kw in child_message for kw in THINK_KEYWORDS)
                    found.append({
                        'aid': aid,
                        'rpid': child_rpid,
                        'message': child_message[:100],
                        'parent_rpid': child_parent,
                        'has_think': has_think,
                    })

        # 检查是否有下一页
        cursor = j.get('data', {}).get('cursor', {})
        has_more = cursor.get('has_more', 0)
        if not has_more or len(replies) < 20:
            break
        page += 1
        time.sleep(0.5)
    return found


def main():
    session = make_session()
    log(f"=== 开始扫描，dry_run={True} ===")

    total_found = 0
    total_deleted = 0

    for i, aid in enumerate(VIDEO_AIDS):
        log(f"[{i+1}/{len(VIDEO_AIDS)}] 扫描 aid={aid} ...")
        try:
            found = scan_video(aid, session)
        except Exception as e:
            log(f"  ❌ 扫描异常: {e}")
            time.sleep(3)
            continue

        if not found:
            log(f"  无自己回复")
        else:
            log(f"  找到 {len(found)} 条回复:")
            for item in found:
                action = "[DRY] " if True else ""
                think_mark = "⚠️思考" if item['has_think'] else " 正常"
                log(f"    {action}rp={item['rpid']} {think_mark} msg={item['message'][:60]}")

                if not True:  # dry run
                    ok = delete_reply(item['aid'], item['rpid'], session)
                    if ok:
                        total_deleted += 1
                    time.sleep(2)
            total_found += len(found)
        time.sleep(2)

    log(f"=== 完成：共找到 {total_found} 条，删除 {total_deleted} 条 ===")
    if total_found > 0:
        print("\n⚠️ 以上全是 DRY RUN，没实际删除！确认无误后运行：")
        print("  python3 -c \"exec(open('/Users/kaikai/scripts/comment/delete_my_replies.py').read().replace('dry_run=True, # dry run', 'dry_run=False, # REAL').replace('not True, # dry run', 'not False, # REAL'))\"")


if __name__ == "__main__":
    main()