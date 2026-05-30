#!/usr/bin/env python3
"""
峰哥视频流水线：搜索 -> 下载 -> 裁剪80% -> 上传B站 -> 发引流评论
每天08:00和20:00自动运行
"""
from __future__ import annotations

import os
os.environ["PATH"] = "/Users/kaikai/bin:" + os.environ.get("PATH", "")

import fcntl
import json
import random
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════
COOKIE_FILE = Path("/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt")
WORK_DIR = Path("/Users/kaikai/tiktok_automation/fengge_downloads")
UPLOAD_DIR = Path("/Users/kaikai/Desktop/峰哥成品待上传B站")
HISTORY_FILE = Path("/Users/kaikai/tiktok_automation/fengge_history.json")
UPLOAD_HISTORY_FILE = Path("/Users/kaikai/tiktok_automation/fengge_upload_history.json")
LOCK_FILE = Path("/tmp/fengge_pipeline.lock")
LOG_FILE = Path("/tmp/fengge_pipeline.log")

SEARCH_KEYWORD = "峰哥直播切片"
SEARCH_PAGES = 10
RECENT_DAYS = 5
TOP_CANDIDATES = 30
WEIGHT_LIKE = 1
WEIGHT_REPOST = 3
WEIGHT_COIN = 2

# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════
def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def acquire_lock() -> bool:
    try:
        lfd = open(LOCK_FILE, "w")
        fcntl.flock(lfd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lfd.write(str(os.getpid()))
        lfd.flush()
        return True
    except (BlockingIOError, OSError):
        return False


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_history(h: dict) -> None:
    atomic_write(HISTORY_FILE, json.dumps(h, ensure_ascii=False, indent=2))

def load_upload_history() -> dict:
    """加载已上传视频历史"""
    try:
        return json.loads(UPLOAD_HISTORY_FILE.read_text(encoding="utf-8"))
    except:
        return {}

def save_upload_history(h: dict) -> None:
    atomic_write(UPLOAD_HISTORY_FILE, json.dumps(h, ensure_ascii=False, indent=2))


def load_cookies() -> dict:
    """加载Cookie（支持JSON和Netscape格式）"""
    try:
        text = COOKIE_FILE.read_text(encoding="utf-8").strip()
        # 尝试JSON格式
        if text.startswith('{') or text.startswith('['):
            return json.loads(text)
        # 解析Netscape格式
        cookies = {}
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                name = parts[5]
                value = parts[6]
                cookies[name] = value
        return cookies
    except Exception as e:
        log(f"加载Cookie失败: {e}")
        return {}


# ═══════════════════════════════════════════════════════
# 网络 Session
# ═══════════════════════════════════════════════════════
_session = requests.Session()
_session.mount(
    "https://",
    HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504}))
)


# ═══════════════════════════════════════════════════════
# 搜索视频
# ═══════════════════════════════════════════════════════
def _clean_title(title: str) -> str:
    """清理标题中的HTML实体和高亮标记"""
    import html
    # 先unescape HTML实体（会转 \u003C → <）
    title = html.unescape(title)
    # 去掉B站搜索高亮标记
    title = title.replace('<em class="keyword">', '')
    title = title.replace('<em class=keyword>', '')
    title = title.replace('<em class=\\"keyword\\">', '')
    title = title.replace('</em>', '')
    title = title.strip()
    # 检测是否解析失败的损坏标题（残留 <em 或空/过短）
    if '<em' in title or not title or len(title) < 5:
        return None  # 上层会跳过
    return title


def get_search_results(keyword: str, pages: int = 2) -> list[dict]:
    """
    从Bilibili搜索页面HTML解析视频数据
    """
    import re

    all_results = []
    cutoff_ts = (datetime.now() - timedelta(days=RECENT_DAYS)).timestamp()

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    cookies = load_cookies()

    keyword_enc = urllib.parse.quote(keyword)

    for page in range(1, pages + 1):
        url = f"https://search.bilibili.com/video?keyword={keyword_enc}&order=pubdate&page={page}"
        try:
            r = _session.get(url, headers=headers, cookies=cookies, timeout=15)
            if r.status_code != 200:
                log(f"  第{page}页: HTTP {r.status_code}")
                break

            content = r.text

            # 从HTML中提取所有BVID及其上下文（原始脚本方法）
            bvid_matches = list(re.finditer(r'bvid:"(BV[\w]+)"', content))

            page_recent = 0
            for m in bvid_matches:
                bv = m.group(1)
                context = content[m.end():m.end() + 2000]

                title_m = re.search(r',title:"([^"]+)"', context)
                author_m = re.search(r',author:"([^"]+)"', context)
                play_m = re.search(r'[,\s]play:(\d+)', context)
                pubdate_m = re.search(r'[,}]pubdate:(\d+)', context)

                if not (title_m and play_m and pubdate_m):
                    continue

                pubdate = int(pubdate_m.group(1))
                if pubdate < cutoff_ts:
                    continue

                raw_title = title_m.group(1)
                title = _clean_title(raw_title)
                play = int(play_m.group(1))
                author = author_m.group(1) if author_m else "未知"

                # 过滤掉解析失败的损坏标题
                if not title or len(title) < 5 or title.startswith('<em'):
                    continue

                all_results.append({
                    "bvid": bv,
                    "title": title,
                    "author": author,
                    "play": play,
                    "pubdate": pubdate,
                })
                page_recent += 1

            log(f"  第{page}页: 找到{page_recent}个近期视频")
            time.sleep(1.0)

        except Exception as e:
            log(f"  第{page}页请求失败: {e}")
            break

    return all_results


# ═══════════════════════════════════════════════════════
# 视频数据
# ═══════════════════════════════════════════════════════
def get_video_info(bvid: str) -> dict:
    """获取视频的正式标题和统计数据"""
    try:
        cookies = load_cookies()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        }
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        r = _session.get(url, cookies=cookies, headers=headers, timeout=10)
        if r.status_code != 200:
            log(f"    [API] HTTP {r.status_code} for {bvid}")
            return {}
        data = r.json()
        if data.get("code") != 0:
            log(f"    [API] code={data.get('code')} for {bvid}")
            return {}

        d = data.get("data", {})
        title = _clean_title(d.get("title", ""))
        stat = d.get("stat", {})

        return {
            "title": title,
            "stats": {
                "like": stat.get("like", 0),
                "repost": stat.get("share", 0),
                "coin": stat.get("coin", 0),
                "view": stat.get("view", 0),
            }
        }
    except Exception as e:
        log(f"    [API] 失败 {bvid}: {e}")
        return {}


def score_video(stat: dict) -> float:
    """计算视频评分：(点赞*1 + 转发*2 + 投币*3 + 收藏*4) / 播放量"""
    view = stat.get("view", 0)
    if view <= 0:
        return 0.0
    score = (stat.get("like", 0) * 1 +
             stat.get("repost", 0) * 2 +
             stat.get("coin", 0) * 3 +
             stat.get("favorite", 0) * 4) / view
    return score


# ═══════════════════════════════════════════════════════
# 下载
# ═══════════════════════════════════════════════════════
def download_video(bvid: str, output_dir: Path) -> Path | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{bvid}.mp4"

    if output_file.exists():
        log(f"  [下载] 已存在，跳过: {bvid}")
        return output_file

    cmd = [
        "/opt/homebrew/bin/yt-dlp",
        "--cookies", str(COOKIE_FILE),
        "-f", "bestvideo*+bestaudio/best",
        "-o", str(output_file),
        f"https://www.bilibili.com/video/{bvid}"
    ]

    for attempt in range(3):
        log(f"  [下载] [{attempt+1}/3] {bvid}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and output_file.exists():
            size_mb = output_file.stat().st_size / 1024 / 1024
            log(f"  [下载] 完成 {output_file} ({size_mb:.1f}MB)")
            return output_file
        log(f"  [下载] 失败 (attempt {attempt+1}): {result.stderr[-200:]}")
        if attempt < 2:
            time.sleep(5 * (attempt + 1))

    log(f"  [下载] 最终失败: {bvid}")
    return None


# ═══════════════════════════════════════════════════════
# 裁剪80%
# ═══════════════════════════════════════════════════════
def crop_to_80(input_file: Path, output_file: Path) -> Path | None:
    """将视频画面缩放到80%，保留中间部分"""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(input_file)],
            capture_output=True, text=True, timeout=30
        )
        info = json.loads(probe.stdout)
        streams = info.get("streams", [])
        if not streams:
            log("  [裁剪] 无法获取视频尺寸")
            return None
        w = streams[0]["width"]
        h = streams[0]["height"]
    except Exception as e:
        log(f"  [裁剪] ffprobe失败: {e}")
        return None

    log(f"  [裁剪] 原尺寸: {w}x{h}")
    new_w = int(w * 0.8)
    new_h = int(h * 0.8)
    x_offset = (w - new_w) // 2
    y_offset = (h - new_h) // 2

    # 保留音频
    crop_filter = f"crop={new_w}:{new_h}:{x_offset}:{y_offset}"
    cmd = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-vf", crop_filter,
        "-c:a", "copy",
        str(output_file)
    ]
    log(f"  [裁剪] -> {new_w}x{new_h} (偏移 x={x_offset}, y={y_offset})")

    for attempt in range(3):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and output_file.exists():
            size_mb = output_file.stat().st_size / 1024 / 1024
            log(f"  [裁剪] 完成 ({size_mb:.1f}MB)")
            return output_file
        log(f"  [裁剪] 失败 (attempt {attempt+1}): {result.stderr[-200:]}")
        if attempt < 2:
            time.sleep(3 * (attempt + 1))

    return None


# ═══════════════════════════════════════════════════════
# LLM生成简介和引流评论
# ═══════════════════════════════════════════════════════
def generate_desc_and_comment(title: str) -> tuple[str, str]:
    """用LLM根据标题生成简介和引流评论"""
    try:
        import urllib.request
        import json

        payload = {
            "model": "qwen2.5:32b-instruct",
            "messages": [{
                "role": "user",
                "content": f'''根据这个视频标题，生成一段B站视频简介和一条引流评论。

标题: {title}

要求：
1. 简介：2-3句话，概括视频精彩内容，吸引观众点赞投币关注
2. 评论：1条简短的引流评论，要自然，不能像广告

直接输出，格式如下，不要其他内容：
简介：[简介内容]
引流评论：[评论内容]'''
            }],
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 200}
        }

        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        text = result["message"]["content"].strip()
        log(f"  [LLM] 原始输出:\n{text[:300]}")

        desc = ""
        comment = ""
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("简介："):
                desc = line[3:].strip()
            elif line.startswith("引流评论："):
                comment = line[5:].strip()

        return desc, comment
    except Exception as e:
        log(f"  [LLM] 生成失败: {e}")
        return "", ""


# ═══════════════════════════════════════════════════════
# B站上传
# ═══════════════════════════════════════════════════════
def biliup_upload(video_path: str, title: str, desc: str, tid: int = 21):
    """上传视频到B站，返回bvid或None"""
    video_path = Path(video_path)
    if not video_path.exists():
        log(f"  [上传] 文件不存在: {video_path}")
        return None

    try:
        import asyncio
        from bilibili_api import Credential
        from bilibili_api.video_uploader import VideoUploader, VideoUploaderPage, VideoMeta, bvid2aid
        from PIL import Image

        cover_path = Path("/tmp/fengge_upload_cover.png")
        if not cover_path.exists():
            img = Image.new('RGBA', (1, 1), color=(0, 0, 0, 0))
            img.save(cover_path)

        data = load_cookies()
        cred = Credential(
            sessdata=data.get('SESSDATA', ''),
            bili_jct=data.get('bili_jct', ''),
            buvid3=data.get('buvid3', '')
        )

        async def do_upload():
            import warnings
            warnings.filterwarnings('ignore')
            meta = VideoMeta(
                tid=tid,
                title=title,
                desc=desc,
                cover=str(cover_path),
                tags=["峰哥", "剪辑", "自动上传"],
            )
            page = VideoUploaderPage(
                path=str(video_path),
                title=title,
                description=desc
            )
            uploader = VideoUploader(pages=[page], meta=meta, credential=cred)
            ret = await uploader.start()
            return ret

        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(do_upload())
        log(f"  [上传] 成功: {title}")

        if isinstance(result, dict) and result.get("bvid"):
            return result["bvid"]
        return True
    except Exception as e:
        log(f"  [上传] 失败: {e}")
        return None


# ═══════════════════════════════════════════════════════
# 发评论
# ═══════════════════════════════════════════════════════
def post_video_comment(bvid: str, text: str) -> bool:
    """发布评论到视频评论区"""
    try:
        import asyncio
        from bilibili_api import Credential
        from bilibili_api.comment import send_comment, CommentResourceType
        from bilibili_api.video_uploader import bvid2aid

        data = load_cookies()
        cred = Credential(
            sessdata=data.get('SESSDATA', ''),
            bili_jct=data.get('bili_jct', ''),
            buvid3=data.get('buvid3', '')
        )

        async def do_comment():
            aid = bvid2aid(bvid)
            log(f"  [评论] aid={aid}, bvid={bvid}")
            await send_comment(text, oid=aid, type_=CommentResourceType.VIDEO, credential=cred)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(do_comment())
        log(f"  [评论] 成功: {text[:50]}...")
        return True
    except Exception as e:
        log(f"  [评论] 失败: {e}")
        return False


# ═══════════════════════════════════════════════════════
# 流水线主逻辑
# ═══════════════════════════════════════════════════════
def run_pipeline():
    # 清空日志
    open(LOG_FILE, "w").close()

    log("=" * 60)
    log("峰哥视频流水线启动")

    if not acquire_lock():
        log("另一个进程正在运行，退出")
        return

    # 1. 清理临时目录中的视频文件
    log("[清理] 清理临时视频文件...")
    for f in WORK_DIR.glob("*.mp4"):
        try:
            f.unlink()
            log(f"  删除: {f.name}")
        except Exception:
            pass

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # 2. 搜索
    log(f"[搜索] 搜索「{SEARCH_KEYWORD}」近{RECENT_DAYS}天视频...")
    results = get_search_results(SEARCH_KEYWORD, pages=SEARCH_PAGES)
    log(f"[搜索] 共找到 {len(results)} 个视频")
    if not results:
        log("[搜索] 失败，退出")
        return

    # 3. 排除历史下载 & 已上传
    history = load_history()
    upload_history = load_upload_history()
    downloaded = set(history.keys())
    uploaded = set(upload_history.keys())
    candidates = [v for v in results if v["bvid"] not in downloaded and v["bvid"] not in uploaded]
    log(f"[候选] {len(candidates)}个（已排除下载过{len(results)-len(candidates)-len(uploaded)}个 + 已上传{len(uploaded)}个）")
    if not candidates:
        log("[候选] 没有新视频，退出")
        return

    # 4. 按上传时间排序，取最新30个
    candidates.sort(key=lambda x: x.get("pubdate", 0), reverse=True)
    top_candidates = candidates[:TOP_CANDIDATES]
    log(f"[评分] 从 {len(top_candidates)} 个候选视频获取正式标题和统计数据...")

    # 5. 通过API获取每个视频的正式标题和统计数据
    scored = []
    for v in top_candidates:
        api_data = get_video_info(v["bvid"])
        if not api_data:
            log(f"  [{v['bvid']}] API获取失败，跳过")
            continue

        title = api_data.get("title", "")
        stats = api_data.get("stats", {})
        # 过滤：标题必须存在且包含峰哥/VOL/【 之一（才是真正的直播切片）
        if not title or not any(kw in title for kw in ["峰哥", "VOL", "【"]):
            skip_title = title or "(空)"
            log(f"  [{v['bvid']}] 非直播切片/标题损坏，跳过: {skip_title}")
            continue
        v["title"] = title  # 用API返回的干净标题替换搜索阶段的损坏标题
        score = score_video(stats)
        v["stats"] = stats
        v["score"] = score
        scored.append(v)

        log(f"  [{v['bvid']}] {title}")
        log(f"    播:{stats.get('view',0)} 赞:{stats.get('like',0)} 转:{stats.get('repost',0)} 币:{stats.get('coin',0)} -> 评分:{score:.4f}")
        time.sleep(0.3)

    if not scored:
        log("[评分] 没有可用的视频，退出")
        return

    # 6. 按评分排序，选最高分
    scored.sort(key=lambda x: x["score"], reverse=True)
    chosen = scored[0]
    bvid = chosen["bvid"]

    pub_date = datetime.fromtimestamp(chosen.get("pubdate", 0)).strftime("%Y-%m-%d") if chosen.get("pubdate") else "未知"
    stat = chosen.get("stats", {})
    log(f"[选中] {bvid}")
    log(f"  标题: {chosen['title']}")
    log(f"  发布: {pub_date} | 评分: {chosen['score']:.4f}")
    log(f"  数据: 播:{stat.get('view',0)} 赞:{stat.get('like',0)} 转:{stat.get('repost',0)} 币:{stat.get('coin',0)}")

    # 7. 下载
    log("[下载] 开始下载...")
    raw_file = download_video(bvid, WORK_DIR)
    if not raw_file:
        log("[下载] 失败，退出")
        return

    # 8. 裁剪
    log("[裁剪] 开始裁剪...")
    cropped_file = WORK_DIR / f"{bvid}_cropped.mp4"
    result = crop_to_80(raw_file, cropped_file)
    if not result:
        log("[裁剪] 失败，退出")
        return

    # 9. 移动到上传目录
    upload_file = UPLOAD_DIR / cropped_file.name
    if upload_file.exists():
        upload_file.unlink()
    shutil.move(str(cropped_file), str(upload_file))
    log(f"[上传] 文件已移动到: {upload_file}")

    # 10. 记录历史
    history[bvid] = {
        "title": chosen["title"],
        "downloaded_at": time.strftime("%Y-%m-%d %H:%M"),
        "file": str(upload_file),
    }
    save_history(history)

    # 11. LLM生成简介和评论
    title_text = chosen["title"]
    log(f"[LLM] 生成简介和引流评论...")
    log(f"  使用标题: {title_text}")
    desc_text, comment_text = generate_desc_and_comment(title_text)
    log(f"  生成简介: {desc_text}")
    log(f"  引流评论: {comment_text}")
    if not desc_text:
        desc_text = f"自动剪辑上传 · {datetime.now().strftime('%Y-%m-%d')}"

    # 12. 上传
    log("[上传] 开始上传...")
    upload_result = biliup_upload(str(upload_file), title_text, desc_text)
    upload_bvid = upload_result if isinstance(upload_result, str) else None
    log(f"[上传] 结果: {upload_result}")

    if upload_result:
        log("✅ B站上传成功!")
        # 记录已上传BV（用于去重）
        upload_history = load_upload_history()
        upload_history[bvid] = {
            "title": chosen["title"],
            "uploaded_at": time.strftime("%Y-%m-%d %H:%M"),
            "bvid": bvid,
        }
        save_upload_history(upload_history)
        log(f"[去重] 已记录上传历史: {bvid}")
        if comment_text and upload_bvid:
            log(f"[评论] 发布引流评论...")
            post_video_comment(upload_bvid, comment_text)
        elif comment_text:
            log(f"[评论] 无bvid，跳过: {comment_text}")
    else:
        log("⚠️ B站上传失败")

    log("=" * 60)
    log(f"✅ 流水线完成!")
    log(f"视频: {title_text}")
    log(f"文件: {upload_file}")


if __name__ == "__main__":
    run_pipeline()