import http.cookies, sys, os, json, time, random, requests
from pathlib import Path

HOME = os.path.expanduser("~")
sys.path.insert(0, f'{HOME}/.hermes/scripts')

# ==================== Cookie 加载 ====================
def load_cookies():
    """从 /Users/kaikai/scripts/20岁还没赚够100w_cookies.txt 加载（兼容 list 和 dict 格式）"""
    try:
        with open('/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {c['name']: c['value'] for c in data}
        elif isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        print(f'加载 cookies 失败: {e}')
        return {}

COOKIES = load_cookies()
SESSDATA = COOKIES.get("SESSDATA", "")
BILI_JCT = COOKIES.get("bili_jct", "")
BUVID3 = COOKIES.get("buvid3", "")

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

REPLIED_FILE = Path("/tmp/bili_yinliu_comments.json")
if REPLIED_FILE.exists():
    replied = set(json.loads(REPLIED_FILE.read_text()))
else:
    replied = set()

upload_log = json.load(open(f"{HOME}/.hermes/bilibili_work/upload_log.json"))
videos = upload_log.get("uploaded", [])
new_videos = [v for v in videos if v not in replied]

def send_comment(oid, content):
    try:
        r = requests.post("https://api.bilibili.com/x/v2/reply/add",
            data={"oid": oid, "type": 1, "message": content, "plat": 1, "root": 0, "parent": 0, "csrf": BILI_JCT},
            headers=HEADERS, cookies=COOKIES, timeout=10)
        j = r.json()
        return j.get("code") == 0
    except:
        return False

def get_reply_count(bvid):
    try:
        r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers=HEADERS, cookies=COOKIES, timeout=10)
        j = r.json()
        if j.get("code") == 0:
            return j["data"]["stat"]["reply"]
    except:
        pass
    return -1

success = 0
for bvid in new_videos:
    count = get_reply_count(bvid)
    if count == 0:
        comment = random.choice(COMMENTS)
        if send_comment(bvid, comment):
            replied.add(bvid)
            REPLIED_FILE.write_text(json.dumps(list(replied), ensure_ascii=False))
            success += 1
            time.sleep(random.uniform(5, 8))

print(f"引流评论完成: {success}条")
