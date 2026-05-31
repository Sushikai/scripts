#!/usr/bin/env python3
"""
B站引流评论Bot - 哲学风格评论（多账号版）
合并自 bilibili_yinliu.py + yinliu_comments.py
"""
import http.cookies, sys, os, json, time, random, requests
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 复用统一 cookie 加载
sys.path.insert(0, str(Path(__file__).parent.parent))
from bilibili_utils.cookies import load_all_accounts

HEADERS = {"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36", "Referer": "https://www.bilibili.com"}

# 30字以上哲学引流句子
COMMENTS = [
    "人生最大的遗憾，不是失败，而是你从未为自己真正活过一次。那些错过的机会，有时候不是因为不够努力，而是缺少迈出第一步的勇气。",
    "你以为的极限，不过是被困在舒适区里的幻觉，而真正的强者一直在打破它。成长从来不是舒适的过程，而是在挣扎中寻找新的可能。",
    "真正的成熟，不是变得越来越现实，而是能够在现实里保留一点理想主义的火种。失去做梦的能力，才是真正的衰老。",
    "所有的离开都是蓄谋已久，但所有的相遇也都是命中注定的安排。珍惜每一次相遇，因为下辈子不一定能再遇见。",
    "人最难看透的，从来不是别人，而是那个躲在镜子里的自己。我们总是善于伪装，却骗不了自己的内心。",
    "孤独是人生的常态，但学会独处才是走向强大的第一步，也是通往内心平静的必经之路。耐得住寂寞，才守得住繁华。",
    "世界上最远的距离，不是生与死，而是我在你面前，你却从未真正看见过我。人与人之间最深的隔阂，是心的距离。",
    "时间不会治愈一切，它只是让你在一次次的失落中学会了放下。真正放下，不是忘记，而是释然。",
    "人生没有白走的路，每一步都在塑造着现在的你，哪怕当时看起来毫无意义。那些至暗时刻，往往是成长的转折点。",
    "有时候选择放下，不是软弱，而是另一种形式的强大，因为懂得放手才是真正的智慧。放下执念，才能拥抱新生。",
    "你所以为的真相，往往只是整个真相的一个碎片而已。我们都活在自己编织的信息茧房里，看不见全貌。",
    "爱情不是找到一个完美的人，而是学会用完美的眼光去看待一个不完美的人。真正的爱，是接纳对方的全部。",
    "人最怕的不是失败，而是失败之后再也没有重新开始的勇气。只要活着，就还有翻盘的机会。",
    "真正的自由不是想做什么就做什么，而是能够选择不去做什么。自律，才是最高级的自由。",
    "生活从来不会按照你的计划进行，但它总会在某个转角给你意想不到的答案。接受无常，才是人生的必修课。",
]

PRIMARY_ACCOUNT = "20岁还没赚够100"
COOLDOWN_SECONDS = 3600

ACCOUNTS = load_all_accounts()
if not ACCOUNTS:
    print("没有可用账号，退出")
    sys.exit(0)

for acc in ACCOUNTS:
    acc["replied_file"] = Path(f"/tmp/bili_yinliu_{acc['name'][:8]}.json")
    if acc["replied_file"].exists():
        try:
            acc["replied"] = set(json.loads(acc["replied_file"].read_text()))
        except Exception:
            acc["replied"] = set()
    else:
        acc["replied"] = set()

for acc in ACCOUNTS:
    acc["cooldown_file"] = Path(f"/tmp/bili_yinliu_cd_{acc['name'][:8]}.json")

def _load_cooldown(acc: dict):
    try:
        if acc["cooldown_file"].exists():
            return json.loads(acc["cooldown_file"].read_text()).get("last_run_ts", 0)
    except:
        pass
    return 0

def _save_cooldown(acc: dict):
    try:
        acc["cooldown_file"].write_text(json.dumps({"last_run_ts": time.time()}, ensure_ascii=False))
    except:
        pass

def _check_cooldown(acc: dict) -> bool:
    if PRIMARY_ACCOUNT in acc["name"]:
        return True
    last = _load_cooldown(acc)
    if time.time() - last < COOLDOWN_SECONDS:
        print(f"  ⏳ [{acc['name']}] 冷却中（距上次运行不足1小时），跳过")
        return False
    return True

upload_log_path = Path(f"{Path.home()}/.hermes/bilibili_work/upload_log.json")
videos = []
if upload_log_path.exists():
    try:
        with open(upload_log_path) as f:
            upload_log = json.load(f)
        videos = upload_log.get("uploaded", [])
    except Exception as e:
        print(f"加载上传记录失败: {e}")

def send_comment(session, oid, content, bili_jct, cookies):
    try:
        r = session.post("https://api.bilibili.com/x/v2/reply/add",
            data={"oid": oid, "type": 1, "message": content, "plat": 1, "root": 0, "parent": 0, "csrf": bili_jct},
            headers=HEADERS, cookies=cookies, timeout=10)
        j = r.json()
        return j.get("code") == 0
    except Exception as e:
        print(f"发送评论失败: {e}")
        return False

def get_reply_count(session, bvid, cookies):
    try:
        r = session.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers=HEADERS, cookies=cookies, timeout=10)
        j = r.json()
        if j.get("code") == 0:
            return j["data"]["stat"]["reply"]
    except Exception:
        pass
    return -1

total_success = 0
for acc in ACCOUNTS:
    if not _check_cooldown(acc):
        continue
    new_videos = [v for v in videos if v not in acc["replied"]]
    success = 0
    print(f"\n[{acc['name']}] 开始处理，还剩 {len(new_videos)} 个视频待发评论")
    for bvid in new_videos:
        count = get_reply_count(acc["session"], bvid, acc["cookies"])
        if count == 0:
            comment = random.choice(COMMENTS)
            if send_comment(acc["session"], bvid, comment, acc["bili_jct"], acc["cookies"]):
                acc["replied"].add(bvid)
                acc["replied_file"].write_text(json.dumps(list(acc["replied"]), ensure_ascii=False))
                success += 1
                time.sleep(random.uniform(5, 8))
    if success > 0:
        _save_cooldown(acc)
    print(f"[{acc['name']}] 引流评论完成: {success}条 / {len(new_videos)}个视频")
    total_success += success

print(f"\n总计: {total_success}条 / {len(videos)}个视频")