#!/usr/bin/env python3
"""
峰哥视频流水线：搜索 -> 下载 -> 裁剪90% -> 上传B站 -> 监控评论
每天08:00和20:00自动运行

优化：支持按时间范围筛选最新热门视频，最高画质优先，重试机制，进程锁
"""
from __future__ import annotations

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
COOKIES = {
    "SESSDATA": "577e3116%2C1794848457%2C1661e%2A52CjD5ybsVR6H9X4F9cCN74F9w2gNdoVnSxOWky3IWFkRL5NUuT3I5aQVNAp6MpijkaN4SVjA5d2E5UGtfaXdoLVN5YTF0VEZMbU1jd0hCajNWYkpxam5OdW9QZXVLaHh3aUdjakg4czFyRDBqbXFBMExhMllvTDdtU0ZZVFZ4eV9QUG5NcWlIOWp3IIEC",
    "bili_jct": "fcd844961a4de0c0e1ebbbe05b183fc6",
    "buvid3": "3169493F-D668-AC48-4C96-6FB6DEFFF40E15104infoc",
    "buvid4": "9A4E1CC4-30BF-751C-0075-910E6C46849G47286-026051823-74Mos/+u6OM9VVAgAws/WQ%3D%3D",
    "buvid_fp": "4927fafa58d41d1530891c14ea4ea757",
    "CURRENT_FNVAL": "4048",
    "CURRENT_QUALITY": "120",
    "DedeUserID": "140289989",
    "DedeUserID__ckMd5": "d62d826182d027e2",
    "fingerprint": "4927fafa58d41d1530891c14ea4ea757",
    "sid": "drfqj8r1",
    "rpdid": "|(umuY~)Y~um0J'u~~YlYlRRk",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}

WORK_DIR = Path("/Users/kaikai/tiktok_automation/fengge_downloads")
UPLOAD_DIR = Path("/Users/kaikai/Desktop/峰哥成品待上传B站")
HISTORY_FILE = Path("/Users/kaikai/tiktok_automation/fengge_history.json")
LOCK_FILE = Path("/tmp/fengge_pipeline.lock")

# 搜索配置
SEARCH_KEYWORD = "峰哥"
SEARCH_PAGES = 3
# 只选最近 N 天内发布的视频（避免下载老视频）
RECENT_DAYS = 60
# 候选视频数量（按播放量排序后取前 N，随机选1个）
TOP_CANDIDATES = 10

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
    ts = time.strftime("%m-%d %H:%M:%S")
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


# ═══════════════════════════════════════════════════════
# 搜索视频
# ═══════════════════════════════════════════════════════
def get_search_results(keyword: str, pages: int = 3) -> list[dict]:
    """
    搜索B站视频，返回列表（按播放量+时间综合排序）
    API返回字段: bvid, title, author, play_number, pubdate
    """
    all_results = []
    keyword_enc = urllib.parse.quote(keyword)
    cutoff_ts = (datetime.now() - timedelta(days=RECENT_DAYS)).timestamp()

    for page in range(1, pages + 1):
        url = (
            f"https://api.bilibili.com/x/web-interface/search/type"
            f"?search_type=video&keyword={keyword_enc}&order=click&page={page}&pagesize=20"
        )
        try:
            r = _session.get(url, headers=HEADERS, cookies=COOKIES, timeout=15)
            d = r.json()
        except Exception as e:
            log(f"第{page}页请求失败: {e}")
            break

        if d.get("code") != 0:
            log(f"搜索失败: {d.get('message', '未知错误')}")
            break

        results = d.get("data", {}).get("result", [])
        for v in results:
            pubdate = v.get("pubdate", 0)
            # 只保留近期视频
            if pubdate < cutoff_ts:
                continue
            title = v["title"].replace('<em class="keyword">', "").replace("</em>", "")
            all_results.append({
                "bvid": v["bvid"],
                "title": title,
                "author": v.get("author", "未知"),
                "play": v.get("play_number", v.get("play", 0)),
                "pubdate": pubdate,
                "duration": v.get("duration", ""),
            })
        log(f"第{page}页: 找到{len(results)}个（近期{len([r for r in results if r.get('pubdate',0) >= cutoff_ts])}个）")
        time.sleep(0.8)  # 避免过快请求

    return all_results


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
    new_w = int(w * 0.9)
    new_h = int(h * 0.9)
    x_offset = (w - new_w) // 2
    y_offset = (h - new_h) // 2

    crop_filter = f"crop={new_w}:{new_h}:{x_offset}:{y_offset}"
    cmd = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-vf", crop_filter,
        "-c:a", "copy",
        str(output_file)
    ]
    log(f"裁剪: {w}x{h} -> {new_w}x{new_h} (偏移 x={x_offset}, y={y_offset})")

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
# 流水线主逻辑
# ═══════════════════════════════════════════════════════
def run_pipeline():
    log("=" * 50)
    log("峰哥视频流水线启动")

    if not acquire_lock():
        log("另一个进程正在运行，退出")
        return

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

    # 3. 按播放量排序，从top候选中随机选1个
    candidates.sort(key=lambda x: x.get("play", 0), reverse=True)
    top_candidates = candidates[:TOP_CANDIDATES]
    chosen = random.choice(top_candidates)
    bvid = chosen["bvid"]

    pub_date = datetime.fromtimestamp(chosen.get("pubdate", 0)).strftime("%Y-%m-%d") if chosen.get("pubdate") else "未知"
    log(f"选中: {bvid} | {chosen['title']} | 播放:{chosen.get('play', 0)} | 发布:{pub_date}")

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

    # 8. 完成
    log("=" * 50)
    log(f"✅ 流水线完成!")
    log(f"视频: {chosen['title']}")
    log(f"播放: {chosen.get('play', 0)} | 发布: {pub_date}")
    log(f"文件: {upload_file}")
    log(f"下一步: 用biliup上传到B站")


if __name__ == "__main__":
    run_pipeline()