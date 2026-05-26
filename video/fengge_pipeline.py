#!/usr/bin/env python3
"""
峰哥视频流水线：搜索 -> 下载 -> 裁剪90% -> 上传B站 -> 监控评论
每天08:00和20:00自动运行

优化：支持按时间范围筛选最新热门视频，最高画质优先，重试机制，进程锁
"""
from __future__ import annotations

import os
# 确保 yt-dlp 在 PATH 中（cron 环境）
os.environ["PATH"] = "/Users/kaikai/bin:" + os.environ.get("PATH", "")

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
# Cookie 从 /Users/kaikai/scripts/20岁还没赚够100w_cookies.txt 动态加载
COOKIE_FILE = Path("/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt")

WORK_DIR = Path("/Users/kaikai/tiktok_automation/fengge_downloads")
UPLOAD_DIR = Path("/Users/kaikai/Desktop/峰哥成品待上传B站")
HISTORY_FILE = Path("/Users/kaikai/tiktok_automation/fengge_history.json")
LOCK_FILE = Path("/tmp/fengge_pipeline.lock")

# 搜索配置
SEARCH_KEYWORD = "峰哥直播切片"
SEARCH_PAGES = 10
# 只选最近 N 天内发布的视频（避免下载老视频）
# 注：B站搜索结果视频时间可能较旧，使用365天确保有足够候选
RECENT_DAYS = 5
# 候选视频数量（按上传时间最新取前 N，评分后随机选1个）
TOP_CANDIDATES = 30
# 评分权重
WEIGHT_LIKE = 1
WEIGHT_REPOST = 3
WEIGHT_COIN = 2

# ═══════════════════════════════════════════════════════
# 网络 Session（自动重试）
# ═══════════════════════════════════════════════════════
_session = requests.Session()
_session.mount(
    "https://",
    HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504}))
)

# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════
def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open("/tmp/fengge_pipeline.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def atomic_write(path: Path, data: str) -> None:
    """安全写文件：先写.tmp再rename，防止Crash后文件损坏"""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def acquire_lock() -> bool:
    """进程锁，防止并发运行"""
    try:
        lfd = open(LOCK_FILE, "w")
        fcntl.flock(lfd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lfd.write(str(os.getpid()))
        lfd.flush()
        return True
    except (BlockingIOError, OSError):
        return False


import fcntl, os


def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_history(h: dict) -> None:
    atomic_write(HISTORY_FILE, json.dumps(h, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════
# 搜索视频（HTML解析，无需API认证）
# ═══════════════════════════════════════════════════════════════
def _clean_title(title: str) -> str:
    """清理标题中的HTML实体和高亮标记"""
    return (
        title.replace('\\u003C', '<')
             .replace('\\u003E', '>')
             .replace('<em class="keyword">', '')
             .replace('</em>', '')
             .replace('&lt;', '<')
             .replace('&gt;', '>')
             .replace('&amp;', '&')
             .strip()
    )


def get_search_results(keyword: str, pages: int = 2) -> list[dict]:
    """
    从Bilibili搜索页面HTML解析视频数据（无需API认证）
    返回字段: bvid, title, author, play, pubdate, duration
    """
    import re
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    all_results = []
    cutoff_ts = (datetime.now() - timedelta(days=RECENT_DAYS)).timestamp()

    # HTTP session with retry
    session = requests.Session()
    session.mount("https://", HTTPAdapter(
        max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504})
    ))

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }

    keyword_enc = requests.utils.quote(keyword)

    for page in range(1, pages + 1):
        url = f"https://search.bilibili.com/video?keyword={keyword_enc}&order=click&page={page}"
        try:
            r = session.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                log(f"第{page}页: HTTP {r.status_code}")
                break

            content = r.text

            # 从HTML中提取所有BVID及其上下文
            bvid_matches = list(re.finditer(r'bvid:"(BV[\w]+)"', content))
            page_recent = 0

            for m in bvid_matches:
                bv = m.group(1)
                context = content[m.end():m.end() + 1000]

                # 提取各项数据
                title_m = re.search(r',title:"([^"]+)"', context)
                author_m = re.search(r',author:"([^"]+)"', context)
                play_m = re.search(r'[,\s]play:(\d+)', context)
                pubdate_m = re.search(r'[,}]pubdate:(\d+)', context)
                duration_m = re.search(r',duration:"([^"]+)"', context)

                if not (title_m and play_m and pubdate_m):
                    continue

                pubdate = int(pubdate_m.group(1))
                # 只保留近期视频
                if pubdate < cutoff_ts:
                    continue

                title = _clean_title(title_m.group(1))
                author = author_m.group(1) if author_m else "未知"
                play = int(play_m.group(1))
                duration = duration_m.group(1) if duration_m else ""

                all_results.append({
                    "bvid": bv,
                    "title": title,
                    "author": author,
                    "play": play,
                    "pubdate": pubdate,
                    "duration": duration,
                })
                page_recent += 1

            log(f"第{page}页: 找到{page_recent}个近期视频（共{len(bvid_matches)}个BVID）")
            time.sleep(1.0)  # 避免过快请求

        except Exception as e:
            log(f"第{page}页请求失败: {e}")
            break

    return all_results


def get_video_stats(bvid: str) -> dict:
    """获取视频的点赞、转发、投币、播放量"""
    try:
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        r = _session.get(url, timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json()
        if data.get("code") != 0:
            return {}
        stat = data.get("data", {}).get("stat", {})
        return {
            "like": stat.get("like", 0),
            "repost": stat.get("share", 0),
            "coin": stat.get("coin", 0),
            "view": stat.get("view", 0),
        }
    except Exception as e:
        log(f"获取视频统计失败 {bvid}: {e}")
        return {}


def score_video(stat: dict) -> float:
    """计算视频评分：(点赞*1 + 转发*3 + 投币*2) / 播放量"""
    view = stat.get("view", 0)
    if view <= 0:
        return 0.0
    score = (stat.get("like", 0) * WEIGHT_LIKE +
             stat.get("repost", 0) * WEIGHT_REPOST +
             stat.get("coin", 0) * WEIGHT_COIN) / view
    return score


# ═══════════════════════════════════════════════════════
# 下载视频（最高画质 + 重试）
# ═══════════════════════════════════════════════════════
def download_video(bvid: str, output_dir: Path) -> Path | None:
    """
    用yt-dlp下载视频（最高画质），支持重试
    画质优先级: 1080p60fps > 1080p > 720p
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{bvid}.mp4"

    if output_file.exists():
        log(f"视频已存在，跳过下载: {bvid}")
        return output_file

    # 最高画质策略：优先 1080p60fps，其次 1080p，最低 720p
    format_spec = (
        "bestvideo[height>=720][ext=mp4]/bestvideo[ext=mp4]/best[ext=mp4]/best"
        "+bestaudio[ext=m4a]/bestaudio/best"
    )

    for attempt in range(3):
        cmd = [
            "yt-dlp",
            "--cookies-from-browser", "chrome",
            "-f", format_spec,
            "--format-sort", "height:1080,fps:60",
            "-o", str(output_file),
            f"https://www.bilibili.com/video/{bvid}"
        ]
        log(f"下载 [{attempt+1}/3]: {bvid}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode == 0 and output_file.exists():
            size_mb = output_file.stat().st_size / 1024 / 1024
            log(f"下载完成: {output_file} ({size_mb:.1f}MB)")
            return output_file

        log(f"下载失败 (attempt {attempt+1}): {result.stderr[-300:]}")
        if attempt < 2:
            time.sleep(5 * (attempt + 1))  # 指数退避

    log(f"下载最终失败: {bvid}")
    return None


# ═══════════════════════════════════════════════════════
# 裁剪90%
# ═══════════════════════════════════════════════════════
def crop_to_90(input_file: Path, output_file: Path) -> Path | None:
    """将视频画面缩放到90%，保留中间部分"""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(input_file)],
            capture_output=True, text=True, timeout=30
        )
        info = json.loads(probe.stdout)
        streams = info.get("streams", [])
        if not streams:
            log("无法获取视频尺寸")
            return None
        w = streams[0]["width"]
        h = streams[0]["height"]
    except Exception as e:
        log(f"ffprobe 获取尺寸失败: {e}")
        return None

    log(f"原尺寸: {w}x{h}")
    new_w = int(w * 0.8)
    new_h = int(h * 0.8)
    x_offset = (w - new_w) // 2
    y_offset = (h - new_h) // 2

    crop_filter = f"crop={new_w}:{new_h}:{x_offset}:{y_offset}"
    cmd = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-vf", crop_filter,
        "-c:a", "copy",
        str(output_file)
    ]
    log(f"裁剪: {w}x{h} -> {new_w}x{new_h} (四边各裁10%, 偏移 x={x_offset}, y={y_offset})")

    for attempt in range(3):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and output_file.exists():
            log(f"裁剪完成: {output_file}")
            return output_file
        log(f"裁剪失败 (attempt {attempt+1}): {result.stderr[-200:]}")
        if attempt < 2:
            time.sleep(3 * (attempt + 1))

    return None


# ═══════════════════════════════════════════════════════
# B站上传（biliup）
# ═══════════════════════════════════════════════════════

def generate_desc_and_comment(title: str) -> tuple[str, str]:
    """用LLM根据标题生成简介和引流评论"""
    try:
        import anthropic
        client = anthropic.Anthropic()

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f'''根据这个视频标题，生成一段B站视频简介和一条引流评论。

标题: {title}

要求：
1. 简介：2-3句话，概括视频精彩内容，吸引观众点赞投币关注
2. 评论：1条简短的引流评论，要自然，不能像广告

直接输出，格式如下，不要其他内容：
简介：[简介内容]
引流评论：[评论内容]'''
            }]
        )
        # 兼容 ThinkingBlock（claude-sonnet-4-6 可能返回）
        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text
            elif block.type == "thinking":
                text += block.thinking

        desc = ""
        comment = ""
        for line in text.split("\n"):
            if line.startswith("简介："):
                desc = line[3:].strip()
            elif line.startswith("引流评论："):
                comment = line[5:].strip()

        return desc, comment
    except Exception as e:
        log(f"LLM生成失败: {e}")
        return "", ""


def biliup_upload(video_path: str, title: str = None, desc: str = None, tid: int = 21):
    """
    用bilibili_api上传视频到B站
    tid: 21=生活, 1=动画, 3=音乐等
    返回 bvid 或 None
    """
    video_path = Path(video_path)
    if not video_path.exists():
        log(f"上传文件不存在: {video_path}")
        return None

    if title is None:
        title = f"峰哥精彩片段 {datetime.now().strftime('%m月%d日')} #{random.choice(['搞笑','情感','社会','哲理'])}"

    if desc is None:
        desc = f"自动剪辑上传 · {datetime.now().strftime('%Y-%m-%d')}"

    try:
        import asyncio
        from bilibili_api import Credential
        from bilibili_api.video_uploader import VideoUploader, VideoUploaderPage, VideoMeta
        from PIL import Image

        # 创建1x1透明图作为cover占位
        cover_path = Path("/tmp/fengge_upload_cover.png")
        if not cover_path.exists():
            img = Image.new('RGBA', (1, 1), color=(0, 0, 0, 0))
            img.save(cover_path)

        data = json.loads(open('/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt').read())
        sess = data.get('SESSDATA', '')
        jct = data.get('bili_jct', '')
        buvid3 = data.get('buvid3', '')

        cred = Credential(sessdata=sess, bili_jct=jct, buvid3=buvid3)

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
            page = VideoUploaderPage(path=str(video_path), title=title, description=desc)
            uploader = VideoUploader(
                pages=[page],
                meta=meta,
                credential=cred,
            )
            ret = await uploader.start()
            return ret

        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(do_upload())
        log(f"上传成功: {title}")
        # result 是 dict，包含 bvid
        if isinstance(result, dict) and result.get("bvid"):
            return result["bvid"]
        return True
    except Exception as e:
        log(f"上传失败: {e}")
        return None


def post_video_comment(bvid: str, text: str) -> bool:
    """发布评论到视频评论区"""
    try:
        import asyncio
        from bilibili_api import Credential
        from bilibili_api.comment import send_comment, CommentResourceType
        from bilibili_api.video_uploader import bvid2aid

        data = json.loads(open('/Users/kaikai/scripts/20岁还没赚够100w_cookies.txt').read())
        sess = data.get('SESSDATA', '')
        jct = data.get('bili_jct', '')
        buvid3 = data.get('buvid3', '')

        if not sess or not jct:
            log("缺少SESSDATA或bili_jct，无法发评论")
            return False

        cred = Credential(sessdata=sess, bili_jct=jct, buvid3=buvid3)

        async def do_comment():
            aid = bvid2aid(bvid)
            await send_comment(cred, text, oid=aid, type_=CommentResourceType.VIDEO)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(do_comment())
        log(f"评论发布成功: {text[:50]}...")
        return True
    except Exception as e:
        log(f"评论发布失败: {e}")
        return False


# ═══════════════════════════════════════════════════════
# 流水线主逻辑
# ═══════════════════════════════════════════════════════
def run_pipeline():
    log("=" * 50)
    log("峰哥视频流水线启动")

    if not acquire_lock():
        log("另一个进程正在运行，退出")
        return

    # 清理临时目录中的视频文件
    for f in WORK_DIR.glob("*.mp4"):
        try:
            f.unlink()
            log(f"清理临时文件: {f.name}")
        except Exception:
            pass

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 搜索（近期热门视频）
    log(f"步骤1: 搜索「{SEARCH_KEYWORD}」近{RECENT_DAYS}天热门视频...")
    results = get_search_results(SEARCH_KEYWORD, pages=SEARCH_PAGES)
    if not results:
        log("搜索失败，退出")
        return

    # 2. 排除历史下载
    history = load_history()
    downloaded = set(history.keys())
    candidates = [v for v in results if v["bvid"] not in downloaded]
    log(f"候选视频: {len(candidates)}个（已排除{len(results) - len(candidates)}个历史下载）")

    if not candidates:
        log("没有新视频，退出")
        return

    # 3. 按上传时间排序，取最新30个
    candidates.sort(key=lambda x: x.get("pubdate", 0), reverse=True)
    top_candidates = candidates[:TOP_CANDIDATES]

    # 4. 获取每个视频的统计数据并评分
    log(f"获取 {len(top_candidates)} 个候选视频的统计数据...")
    scored = []
    for v in top_candidates:
        stats = get_video_stats(v["bvid"])
        v["stats"] = stats
        v["score"] = score_video(stats)
        scored.append(v)
        time.sleep(0.3)  # 避免请求过快

    # 按评分排序，选最高分
    scored.sort(key=lambda x: x["score"], reverse=True)
    chosen = scored[0]
    bvid = chosen["bvid"]

    pub_date = datetime.fromtimestamp(chosen.get("pubdate", 0)).strftime("%Y-%m-%d") if chosen.get("pubdate") else "未知"
    stat = chosen.get("stats", {})
    log(f"选中: {bvid} | {chosen['title']} | 评分:{chosen['score']:.4f} | 播:{stat.get('view', 0)} 赞:{stat.get('like', 0)} 转:{stat.get('repost', 0)} 币:{stat.get('coin', 0)} | 发布:{pub_date}")

    # 4. 下载
    raw_file = download_video(bvid, WORK_DIR)
    if not raw_file:
        log("下载失败，退出")
        return

    # 5. 裁剪90%
    cropped_file = WORK_DIR / f"{bvid}_cropped.mp4"
    result = crop_to_90(raw_file, cropped_file)
    if not result:
        log("裁剪失败，退出")
        return

    # 6. 移动到上传目录
    upload_file = UPLOAD_DIR / cropped_file.name
    if upload_file.exists():
        log(f"目标文件已存在，先删除: {upload_file}")
        upload_file.unlink()
    shutil.move(str(cropped_file), str(upload_file))
    log(f"已移动到上传目录: {upload_file}")

    # 7. 记录历史
    history[bvid] = {
        "title": chosen["title"],
        "downloaded_at": time.strftime("%Y-%m-%d %H:%M"),
        "file": str(upload_file),
        "cropped_from": str(raw_file),
        "play": chosen.get("play", 0),
        "pub_date": pub_date,
    }
    save_history(history)

    # 8. 上传到B站
    log("步骤2: 上传到B站...")
    title_text = chosen.get("title", "峰哥精彩片段").replace("<em class=", "").replace("</em>", "").replace("\"", "'")

    # LLM生成简介和引流评论
    desc_text, comment_text = generate_desc_and_comment(title_text)
    if not desc_text:
        desc_text = f"自动剪辑上传 · {datetime.now().strftime('%Y-%m-%d')}"

    upload_result = biliup_upload(str(upload_file), title_text, desc_text)
    upload_bvid = upload_result if isinstance(upload_result, str) else None
    if upload_bvid or upload_result is True:
        log("✅ B站上传成功!")
        if comment_text and upload_bvid:
            post_video_comment(upload_bvid, comment_text)
    else:
        log("⚠️ B站上传失败（文件已移到上传目录，可手动上传）")

    # 9. 完成
    log("=" * 50)
    log(f"✅ 流水线完成!")
    log(f"视频: {title_text}")
    log(f"播放: {chosen.get('play', 0)} | 发布: {pub_date}")
    log(f"文件: {upload_file}")


if __name__ == "__main__":
    run_pipeline()