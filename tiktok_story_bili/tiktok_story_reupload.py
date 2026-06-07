#!/usr/bin/env python3
"""
TikTok故事性AI视频 → B站 + 抖音 自动搬运脚本
每天10:00和20:00自动运行

流程: TikTok英文搜索 → 下载 → B站上传 → 抖音上传 → 记录历史
"""
from __future__ import annotations

import os
os.environ["PATH"] = "/Users/kaikai/bin:" + os.environ.get("PATH", "")

import asyncio
import fcntl
import json
import random
import shutil
import subprocess
import sys
import time
import urllib.parse
import re
from datetime import datetime, timedelta
from pathlib import Path

# 添加config路径用于LLM
sys.path.insert(0, "/Users/kaikai/scripts")
sys.path.insert(1, "/Users/kaikai/scripts/config")
sys.path.insert(2, "/Users/kaikai/scripts/llm_utils")

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════
SEARCH_KEYWORDS = [
    "cute pets viral",
    "satisfying video",
    "cozy vlog aesthetic",
    "funny cat compilation",
    "satisfying ASMR building",
    "room transformation",
    "pet reaction hilarious",
    "satisfying cleaning",
]
SEARCH_PAGES = 3
RECENT_DAYS = 7
MAX_DOWNLOADS_PER_RUN = 2
TOP_CANDIDATES = 20

WORK_DIR = Path(__file__).parent / "downloads"
HISTORY_FILE = Path(__file__).parent / "history.json"
UPLOAD_HISTORY_FILE = Path(__file__).parent / "upload_history.json"
LOCK_FILE = Path("/tmp/tiktok_story_pipeline.lock")
LOG_FILE = Path(__file__).parent / "run.log"

BILI_COOKIE_FILE = Path(__file__).parent / "那那天下雨了_cookies.txt"
TIKTOK_COOKIE_FILE = Path(__file__).parent / "ticktoks_cookies.txt"
DOUYIN_COOKIE_FILE = Path(__file__).parent / "风走了叶落_cookies.txt"
DOUYIN_UPLOAD_SCRIPT = Path("/Users/kaikai/ai_video_upload/douyin_upload.py")

WEIGHT_LIKE = 1
WEIGHT_REPOST = 3
WEIGHT_COIN = 2

# ═══════════════════════════════════════════════════════
# 字幕烧录
# ═══════════════════════════════════════════════════════
_SUBTITLE_MODEL = None
_SUBTITLE_LOCK = None

def _get_whisper_model():
    global _SUBTITLE_MODEL, _SUBTITLE_LOCK
    if _SUBTITLE_MODEL is None:
        import threading
        _SUBTITLE_LOCK = threading.Lock()
        with _SUBTITLE_LOCK:
            if _SUBTITLE_MODEL is None:
                from faster_whisper import WhisperModel
                _SUBTITLE_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    return _SUBTITLE_MODEL

def _extract_audio_for_subtitle(video_path: Path, audio_path: Path) -> bool:
    result = subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-ab", "128k",
        str(audio_path)
    ], capture_output=True, text=True, timeout=120)
    return result.returncode == 0

def _generate_srt_from_audio(audio_path: Path, srt_path: Path) -> bool:
    try:
        model = _get_whisper_model()
        segments, _ = model.transcribe(str(audio_path), language="en", beam_size=5)
        with open(srt_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments):
                start, end, text = seg.start, seg.end, seg.text.strip()
                if not text:
                    continue
                def fmt(t):
                    h, ms = divmod(t, 3600)
                    m, s = divmod(ms, 60)
                    sec, mil = divmod(s, 1)
                    return f"{int(h):02d}:{int(m):02d}:{int(sec):02d},{int(mil*1000):03d}"
                f.write(f"{i+1}\n{fmt(start)} --> {fmt(end)}\n{text}\n\n")
        return True
    except Exception as e:
        log(f"  [字幕] Whisper: {e}")
        return False

def _translate_srt_to_chinese(srt_path: Path) -> bool:
    """将英文字幕翻译成中文并覆盖原SRT"""
    try:
        from llm_utils import call_ollama

        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 分割SRT逐块翻译，避免内容太长
        blocks = content.strip().split("\n\n")
        translated_blocks = []

        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 2:
                continue

            # 检查是否是有效的字幕块（第一行是数字）
            if lines[0].strip().isdigit():
                idx = lines[0]
                timestamp = lines[1]
                text = "\n".join(lines[2:])

                if text.strip():
                    # 翻译文本 - 更强的prompt
                    prompt = text
                    translated_text = call_ollama(
                        prompt,
                        system="You are a translator. Translate the following English text to Chinese. Output ONLY the Chinese translation, nothing else. Do not explain, do not add quotes."
                    )
                    if translated_text and len(translated_text) > 3:
                        # 清理可能的引号和多余空白
                        translated_text = translated_text.strip().strip('"\'')
                        translated_blocks.append(f"{idx}\n{timestamp}\n{translated_text}")
                    else:
                        translated_blocks.append(f"{idx}\n{timestamp}\n{text}")
            else:
                # 非字幕行保留原样
                translated_blocks.append(block)

        result = "\n\n".join(translated_blocks) + "\n"

        if result and len(result) > 20:
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(result)
            log("[字幕] 翻译完成 (EN→ZH)")
            return True
        else:
            log(f"[字幕] 翻译结果异常，保留英文")
            return False
    except Exception as e:
        log(f"  [字幕] 翻译异常: {e}")
        return False

def _is_chinese_srt(srt_path: Path) -> bool:
    """判断SRT文件是否包含中文字符"""
    try:
        import re
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 统计中文字符数量
        chinese_chars = re.findall(r'[一-鿿]', content)
        return len(chinese_chars) > 10
    except Exception:
        return False

def _burn_subtitle(video_path: Path, srt_path: Path, output_path: Path) -> bool:
    """用ffmpeg subtitles滤镜烧录SRT（保留原音频），失败则回退到OpenCV"""
    # 优先用ffmpeg subtitles滤镜（音频自动保留）
    srt_escaped = str(srt_path).replace("'", "'\\''")
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"subtitles='{srt_escaped}':fontsdir=/System/Library/Fonts",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac",
        str(output_path)
    ], capture_output=True, text=True, timeout=600)

    if result.returncode == 0 and output_path.exists():
        return True

    log(f"  [字幕] ffmpeg subtitles失败({result.returncode})，回退到OpenCV: {result.stderr[-100:]}")
    return _burn_subtitle_cv2(video_path, srt_path, output_path)


def _burn_subtitle_cv2(video_path: Path, srt_path: Path, output_path: Path) -> bool:
    """用OpenCV逐帧烧录SRT字幕（无音频，需外部合并）"""
    try:
        import cv2
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()

        def parse_t(t):
            t = t.strip().replace(",", ".")
            hms, ms = t.split(".")
            h, m, s = hms.split(":")
            return float(h)*3600 + float(m)*60 + float(s) + float(ms)/1000

        subs = []
        for block in re.split(r"\n\n+", content.strip()):
            lines = block.strip().split("\n")
            if len(lines) < 3:
                continue
            times = lines[1].split(" --> ")
            if len(times) != 2:
                continue
            start = parse_t(times[0])
            end = parse_t(times[1])
            text = "\n".join(lines[2:])
            if text:
                subs.append((start, end, text))

        if not subs:
            return False

        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        fourcc = cv2.VideoWriter_fourcc(*'H264')
        out = cv2.VideoWriter(str(output_path), fourcc, fps, (w, h))

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = w / 1920 * 0.8
        thickness = max(1, int(w / 1920 * 2))
        margin = int(w * 0.02)
        bg_height = int(h * 0.09)

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            t = frame_idx / fps

            active = None
            for s, e, txt in subs:
                if s <= t < e:
                    active = txt
                    break

            if active is not None:
                bar_y = h - bg_height
                cv2.rectangle(frame, (0, bar_y), (w, h), (0, 0, 0), -1)
                max_chars = max(10, int(w / (font_scale * 20)))
                words = active.split()
                lines_list, line = [], ""
                for word in words:
                    test = (line + " " + word).strip()
                    if len(test) <= max_chars:
                        line = test
                    else:
                        if line:
                            lines_list.append(line)
                        line = word
                if line:
                    lines_list.append(line)
                line_h = int(bg_height / max(len(lines_list), 1))
                for li, line_text in enumerate(lines_list[:5]):
                    y_pos = bar_y + int((li + 0.8) * line_h)
                    x_pos = margin
                    while cv2.getTextSize(line_text[:max_chars], font, font_scale, thickness)[0][0] > w - margin * 2 and len(line_text) > max_chars:
                        line_text = line_text[:len(line_text)//2] + "…"
                    cv2.putText(frame, line_text, (x_pos, y_pos), font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)

            out.write(frame)
            frame_idx += 1

        cap.release()
        out.release()
        return output_path.exists()
    except Exception as e:
        log(f"  [字幕] cv2: {e}")
        return False

def burn_subtitle(video_file: Path, vid: str, downloaded_srt: Path | None = None) -> Path | None:
    """
    烧录字幕到视频，返回新视频路径
    优先级：下载的原始字幕 > Whisper+LLM翻译
    """
    audio_path = WORK_DIR / f"{vid}_audio.mp3"
    srt_path   = WORK_DIR / f"{vid}.srt"
    subbed     = WORK_DIR / f"{vid}_subbed.mp4"

    # 如果已有烧录好的视频，直接返回
    if subbed.exists():
        log("[字幕] 已有烧录版本，跳过")
        return subbed

    # 优先使用下载的原始字幕
    if downloaded_srt and downloaded_srt.exists() and downloaded_srt != srt_path:
        import shutil
        shutil.copy(downloaded_srt, srt_path)
        log("[字幕] 使用下载的原始字幕")
        # 检查是否需要翻译（如果不是中文）
        if not _is_chinese_srt(srt_path):
            log("[字幕] 原始字幕非中文，翻译中...")
            if not _translate_srt_to_chinese(srt_path):
                log("[字幕] 翻译失败，跳过烧录")
                return video_file
    else:
        # 兜底：Whisper识别 + LLM翻译
        log("[字幕] 提取音频...")
        if not _extract_audio_for_subtitle(video_file, audio_path):
            log("[字幕] 音频提取失败")
            return video_file

        log("[字幕] 生成SRT (Whisper EN)...")
        if not _generate_srt_from_audio(audio_path, srt_path):
            audio_path.unlink(missing_ok=True)
            log("[字幕] SRT生成失败")
            return video_file

        log("[字幕] 翻译字幕 (EN→ZH)...")
        if not _translate_srt_to_chinese(srt_path):
            log("[字幕] 翻译失败，跳过烧录")
            audio_path.unlink(missing_ok=True)
            srt_path.unlink(missing_ok=True)
            return video_file

    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()
    srt_lines = srt_content.strip().split("\n")
    subtitle_blocks = [i for i, l in enumerate(srt_lines) if l.strip().isdigit()]
    if len(subtitle_blocks) < 3:
        log(f"[字幕] 字幕块太少({len(subtitle_blocks)}个)，跳过烧录")
        audio_path.unlink(missing_ok=True)
        srt_path.unlink(missing_ok=True)
        return video_file

    log("[字幕] 烧录...")
    # ffmpeg subtitles保留音频；OpenCV回退则无音频，需单独合并
    if not _burn_subtitle(video_file, srt_path, subbed):
        audio_path.unlink(missing_ok=True)
        srt_path.unlink(missing_ok=True)
        log("[字幕] 烧录失败")
        return video_file

    # 检查烧录后视频是否有音频（ffmpeg有，cv2无）
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "json", str(subbed)],
        capture_output=True, text=True, timeout=30
    )
    has_audio = '"codec_type":"audio"' in probe.stdout

    if not has_audio:
        # cv2烧录无音频，用ffmpeg合并原音频
        final_subbed = WORK_DIR / f"{vid}_final.mp4"
        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(subbed),
            "-i", str(video_file),
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            str(final_subbed)
        ], capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and final_subbed.exists():
            subbed.unlink()
            final_subbed.rename(subbed)
            log("[字幕] OpenCV回退音频合并完成")
        else:
            log(f"[字幕] 音频合并失败: {result.stderr[-100:]}")

    audio_path.unlink(missing_ok=True)
    srt_path.unlink(missing_ok=True)
    video_file.unlink(missing_ok=True)
    log("[字幕] 完成")
    return subbed

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
    try:
        return json.loads(UPLOAD_HISTORY_FILE.read_text(encoding="utf-8"))
    except:
        return {}


def save_upload_history(h: dict) -> None:
    atomic_write(UPLOAD_HISTORY_FILE, json.dumps(h, ensure_ascii=False, indent=2))


def load_cookies(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text.startswith('{') or text.startswith('['):
            return json.loads(text)
        cookies = {}
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
        return cookies
    except Exception as e:
        log(f"加载Cookie失败 {path}: {e}")
        return {}


def get_netscape_cookie_file(cookies: dict, name: str = "tiktok") -> Path:
    lines = ["# Netscape HTTP Cookie File", f"# Generated by {name}"]
    for n, v in cookies.items():
        domain = ".tiktok.com" if name == "tiktok" else ".bilibili.com"
        flag = "TRUE"
        path = "/"
        secure = "TRUE"
        expires = "9999999999"
        lines.append(f"{domain}\tTRUE\t{path}\t{secure}\t{expires}\t{n}\t{v}")
    tmp = Path(f"/tmp/{name}_cookies.txt")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    return tmp


# ═══════════════════════════════════════════════════════
# 网络 Session
# ═══════════════════════════════════════════════════════
_session = requests.Session()
_session.mount(
    "https://",
    HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504}))
)
_session.mount(
    "http://",
    HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504}))
)


# ═══════════════════════════════════════════════════════
# TikTok 搜索
# ═══════════════════════════════════════════════════════
def search_tiktok(keyword: str, count: int = 10) -> list[dict]:
    """
    通过 Playwright 访问 TikTok 搜索页，提取视频数字 ID
    """
    results = []
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("  [搜索] playwright 未安装，跳过")
        return results

    async def _search():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            encoded_kw = urllib.parse.quote(keyword)
            search_url = f"https://www.tiktok.com/search?q={encoded_kw}&t=0"
            try:
                await page.goto(search_url, timeout=60000)
                await page.wait_for_timeout(5000)
                # 滚动触发加载
                for _ in range(5):
                    await page.evaluate("window.scrollBy(0, 1200)")
                    await asyncio.sleep(2)
                content = await page.content()
            finally:
                await browser.close()

            found = re.findall(r'"aweme_id":"(\d{15,})"', content)
            found = list(dict.fromkeys(found))  # 去重保持顺序
            return found[:count]

    try:
        video_ids = asyncio.run(_search())
        for vid in video_ids:
            results.append({
                "id": vid,
                "title": f"AI Story {vid}",
                "keyword": keyword
            })
        if results:
            log(f"  [搜索] Playwright找到 {len(results)} 个视频")
    except Exception as e:
        log(f"  [搜索] Playwright异常: {e}")

    # 备选：用 yt-dlp ytsearch 但只取 TikTok 相关结果
    if not results:
        try:
            search_query = f"ytsearch{count}:tiktok satisfying cute pet viral"
            cmd = [
                sys.executable,
                "-m", "yt_dlp",
                "--flat-playlist",
                "--print", "%(id)s %(title)s %(view_count)s",
                "--no-warnings",
                search_query
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(" ", 2)
                    if len(parts) >= 2:
                        vid = parts[0].strip()
                        title = parts[1].strip() if len(parts) > 1 else ""
                        view_count = int(parts[2].strip()) if len(parts) > 2 and parts[2].strip().isdigit() else 0
                        if re.match(r"^[a-zA-Z0-9_-]{10,30}$", vid):
                            results.append({
                                "id": vid,
                                "title": title,
                                "view": view_count,
                                "like": view_count // 50,  # 估算
                                "keyword": keyword
                            })
                log(f"  [搜索] yt-dlp fallback 找到 {len(results)} 个")
        except Exception as e:
            log(f"  [搜索] yt-dlp fallback 异常: {e}")

    return results


def search_tiktok_via_api(keyword: str, count: int = 10) -> list[dict]:
    """
    通过TikTok官方搜索页面解析视频数据（无API key方案）
    """
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    encoded_kw = urllib.parse.quote(keyword)
    url = f"https://www.tiktok.com/search?q={encoded_kw}&t=0"

    try:
        r = _session.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            log(f"  [TikTok搜索] HTTP {r.status_code}")
            return []

        text = r.text
        # 提取视频ID和标题
        video_ids = re.findall(r'"videoId":"(\d+)"', text)
        titles = re.findall(r'"title":"([^"]{10,200})"', text)
        descs = re.findall(r'"description":"([^"]{10,200})"', text)
        likes = re.findall(r'"diggCount":(\d+)', text)
        views = re.findall(r'"playCount":(\d+)', text)

        for i, vid in enumerate(video_ids[:count]):
            title = titles[i] if i < len(titles) else ""
            desc = descs[i] if i < len(descs) else ""
            like = int(likes[i]) if i < len(likes) else 0
            view = int(views[i]) if i < len(views) else 0

            if not title and not desc:
                title = f"AI Story Video {vid}"
                desc = keyword

            results.append({
                "id": vid,
                "title": title or desc,
                "desc": desc,
                "like": like,
                "view": view,
                "keyword": keyword,
            })
            time.sleep(0.3)
    except Exception as e:
        log(f"  [TikTok搜索] 异常: {e}")

    return results


def get_tiktok_video_info(video_id: str) -> dict:
    """通过TikTok视频页面获取详细元数据"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    url = f"https://www.tiktok.com/video/{video_id}"
    try:
        r = _session.get(url, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code != 200:
            return {}

        text = r.text
        # 提取数据
        like_match = re.search(r'"diggCount":(\d+)', text)
        share_match = re.search(r'"shareCount":(\d+)', text)
        comment_match = re.search(r'"commentCount":(\d+)', text)
        view_match = re.search(r'"playCount":(\d+)', text)
        title_match = re.search(r'"description":"([^"]{1,500})"', text)
        duration_match = re.search(r'"duration":(\d+)', text)

        like = int(like_match.group(1)) if like_match else 0
        share = int(share_match.group(1)) if share_match else 0
        comment = int(comment_match.group(1)) if comment_match else 0
        view = int(view_match.group(1)) if view_match else 0
        title = title_match.group(1) if title_match else f"AI Story {video_id}"
        duration = int(duration_match.group(1)) if duration_match else 0

        return {
            "id": video_id,
            "title": title,
            "like": like,
            "share": share,
            "comment": comment,
            "view": view,
            "duration": duration,
        }
    except Exception as e:
        log(f"  [TikTok详情] {video_id}: {e}")
        return {}


def score_tiktok_video(info: dict) -> float:
    """
    爆款视频评分算法 — 综合热度、互动率、时长价值
    权重：绝对互动量(40%) + 互动率(30%) + 时长加权(20%) + 趋势加成(10%)
    """
    view = max(info.get("view", 0), 1)
    like = info.get("like", 0)
    share = info.get("share", 0)
    comment = info.get("comment", 0)
    duration = info.get("duration", 0)

    # 绝对互动量得分 (0-100) — 基础热度
    total_engagement = like + share * 2 + comment * 1.5
    # 用对数刻度，避免极端值主导，同时保留差异
    engagement_score = min(100, (total_engagement ** 0.4) * 5)

    # 互动率得分 (0-100) — 质量指标
    engagement_rate = (like + share + comment) / view
    # 百万播放1%互动率约100分，线性刻度
    rate_score = min(100, engagement_rate * 10000)

    # 时长加权 (0-100) — 完播率代理
    # 30-90秒视频为最优区间，低于15秒或超3分钟价值下降
    if duration > 0:
        if 30 <= duration <= 90:
            duration_score = 100
        elif 15 <= duration < 30:
            duration_score = 60
        elif 90 < duration <= 180:
            duration_score = 80 - (duration - 90) * 0.2
        else:
            duration_score = max(20, 60 - abs(duration - 60) * 0.3)
    else:
        duration_score = 50  # 未知时长

    # 趋势加成 — 用互动数作为代理指标
    # 高互动(赞>10k或分享>1k)视频有爆款潜质
    trending_bonus = 0.0
    if like > 10000 or share > 1000:
        trending_bonus = 15.0
    elif like > 5000 or share > 500:
        trending_bonus = 8.0
    elif like > 1000:
        trending_bonus = 3.0

    # 综合得分 (0-100)
    score = (
        engagement_score * 0.40 +
        rate_score * 0.30 +
        duration_score * 0.20 +
        trending_bonus * 0.10
    )

    log(f"  评分明细 [{info['id']}] eng={engagement_score:.1f} rate={rate_score:.1f} dur={duration_score:.1f} trend={trending_bonus:.1f} → {score:.2f}")
    return score


# ═══════════════════════════════════════════════════════
# LLM关键词生成
# ═══════════════════════════════════════════════════════
def generate_search_keywords() -> list[str]:
    """用LLM根据当前热点生成AI视频搜索关键词，返回5个不重复的英文关键词"""
    try:
        from llm_utils import call_ollama

        prompt = """You are a TikTok content trends analyst.

Current date: 2026-06-01

Generate exactly 5 diverse trending TikTok search keywords that are currently viral and popular.

Requirements:
1. Focus on trending categories: pets, satisfying ASMR, room makeovers, funny cat compilations, cleaning transformation, cozy vlog, food ASMR
2. Each keyword must be distinct
3. Use popular terms like: satisfying, viral, compilation, transformation, reaction, aesthetic, cozy
4. Target high-view content categories
5. Keywords should be 2-5 words each

Output format (just the keywords, one per line, no numbers or bullets):
[keyword1]
[keyword2]
[keyword3]
[keyword4]
[keyword5]"""

        result = call_ollama(prompt, system="You are a TikTok content analyst. Output only the keywords, one per line, no numbers or bullets.")
        if not result:
            raise ValueError("Empty result")
        text = result.strip()
        import re as re_module
        raw_lines = text.split("\n")
        keywords = []
        for line in raw_lines:
            line = line.strip()
            if not line or len(line) < 4:
                continue
            skip_words = ['i need', 'each ', 'focus on', 'keywords should', 'output format', 'your task', 'generate exactly', 'must be', 'use terms', 'mix of']
            lower_line = line.lower()
            if any(sw in lower_line for sw in skip_words):
                continue
            quoted = re_module.findall(r'"([^"]+)"', line)
            if quoted:
                keywords.extend(quoted)
            else:
                cleaned = re_module.sub(r'^[\-\*\d\.\)\s]+', '', line).strip()
                cleaned = re_module.sub(r'\s*[-–]\s*covers?\s*.*$', '', cleaned).strip()
                if len(cleaned.split()) >= 2 and len(cleaned.split()) <= 7:
                    keywords.append(cleaned)

        seen = set()
        unique_keywords = []
        for k in keywords:
            k_lower = k.lower()
            if k_lower not in seen and len(k) > 3:
                seen.add(k_lower)
                unique_keywords.append(k)

        if len(unique_keywords) >= 3:
            log(f"[LLM] 生成关键词: {unique_keywords[:5]}")
            return unique_keywords[:5]
    except Exception as e:
        log(f"[LLM] 关键词生成失败: {e}")

    return [
        "AI story short film emotional",
        "AI generated narrative drama",
        "artificial intelligence fiction",
        "AI cinematic story viral",
        "machine learning short movie",
    ]


def crop_video(input_file: Path, output_file: Path) -> Path | None:
    """上下左右各裁剪1%，用于去重处理"""
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

    crop_pct = 0.01
    new_w = int(w * (1 - crop_pct * 2))
    new_h = int(h * (1 - crop_pct * 2))
    x_offset = int(w * crop_pct)
    y_offset = int(h * crop_pct)

    crop_filter = f"crop={new_w}:{new_h}:{x_offset}:{y_offset}"
    cmd = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-vf", crop_filter,
        "-c:a", "copy",
        str(output_file)
    ]
    log(f"  [裁剪] {w}x{h} -> {new_w}x{new_h}")

    for attempt in range(3):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0 and output_file.exists():
            size_mb = output_file.stat().st_size / 1024 / 1024
            log(f"  [裁剪] 完成 ({size_mb:.1f}MB)")
            return output_file
        log(f"  [裁剪] 失败 (attempt {attempt+1}): {result.stderr[-200:]}")
        if attempt < 2:
            time.sleep(3 * (attempt + 1))

    return None


# ═══════════════════════════════════════════════════════
# 下载
# ═══════════════════════════════════════════════════════
def download_tiktok_video(video_id: str, output_dir: Path) -> tuple[Path | None, Path | None]:
    """
    下载视频，返回 (视频路径, 字幕路径)
    字幕优先下载中文/英文，兜底用Whisper+LLM翻译
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{video_id}.mp4"
    subtitle_file = output_dir / f"{video_id}.srt"

    if output_file.exists():
        log(f"  [下载] 已存在，跳过: {video_id}")
        # 检查是否有字幕
        if subtitle_file.exists():
            return output_file, subtitle_file
        return output_file, None

    # 判断是TikTok纯数字ID还是YouTube ID，构造对应URL和cookies
    is_tiktok = video_id.isdigit()
    if is_tiktok:
        url = f"https://www.tiktok.com/video/{video_id}"
        cookies_args = ["--cookies", str(TIKTOK_COOKIE_FILE)]
    else:
        url = f"https://www.youtube.com/watch?v={video_id}"
        # YouTube公开视频不需要cookies
        cookies_args = []

    # 先尝试下载字幕（中文字幕优先）
    subs_cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--cookies-from-browser", "chrome",
        "--extractor-args", "youtube:player_client=android",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "zh-Hans,zh-Hant,en",
        "--skip-download",
        "--convert-subs", "srt",
        "-o", str(output_dir / video_id),
        url
    ]

    for attempt in range(2):
        result = subprocess.run(subs_cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            # 找生成的字幕文件
            for ext in [".zh-Hans.srt", ".zh-Hant.srt", ".en.srt", ".srt"]:
                potential = output_dir / f"{video_id}{ext}"
                if potential.exists() and potential != subtitle_file:
                    # 复制到标准名
                    import shutil
                    shutil.copy(potential, subtitle_file)
                    log(f"  [字幕] 下载到原始字幕: {ext}")
                    break
            else:
                log(f"  [字幕] 未找到字幕文件，将使用Whisper生成")

    cmd = [
        sys.executable,
        "-m", "yt_dlp",
        "--no-playlist",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "--no-warnings",
        "-o", str(output_file),
        url
    ]
    # YouTube需要特殊处理：使用Chrome cookies + android客户端
    if not is_tiktok:
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "--cookies-from-browser", "chrome",
            "--extractor-args", "youtube:player_client=android",
            "-f", "18/best",
            "--no-warnings",
            "-o", str(output_file),
            url
        ]

    for attempt in range(3):
        log(f"  [下载] [{attempt+1}/3] {video_id}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0 and output_file.exists():
            size_mb = output_file.stat().st_size / 1024 / 1024
            log(f"  [下载] 完成 {output_file} ({size_mb:.1f}MB)")
            # 检查音频轨道，没有则重试
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_streams", str(output_file)],
                capture_output=True, text=True
            )
            if probe.returncode == 0 and '"codec_type":"audio"' not in probe.stdout:
                log(f"  [下载] 音频轨道缺失，重试: {video_id}")
                output_file.unlink(missing_ok=True)
                continue
            return output_file, subtitle_file if subtitle_file.exists() else None
        log(f"  [下载] 失败 (attempt {attempt+1}): {result.stderr[-200:]}")
        if attempt < 2:
            time.sleep(5 * (attempt + 1))

    log(f"  [下载] 最终失败: {video_id}")
    return None, None


# ═══════════════════════════════════════════════════════
# B站上传
# ═══════════════════════════════════════════════════════
def biliup_upload(video_path: str, title: str, desc: str, tid: int = 21) -> str | None:
    """上传视频到B站，返回bvid或None"""
    video_path = Path(video_path)
    if not video_path.exists():
        log(f"  [B站上传] 文件不存在: {video_path}")
        return None

    try:
        import asyncio
        from bilibili_api import Credential
        from bilibili_api.video_uploader import VideoUploader, VideoUploaderPage, VideoMeta, Lines
        from PIL import Image

        cover_path = Path("/tmp/tiktok_story_cover.png")
        if not cover_path.exists():
            img = Image.new('RGB', (160, 90), color=(30, 30, 80))
            img.save(cover_path)

        cookies = load_cookies(BILI_COOKIE_FILE)
        cred = Credential(
            sessdata=cookies.get('SESSDATA', ''),
            bili_jct=cookies.get('bili_jct', ''),
            buvid3=cookies.get('buvid3', '')
        )

        async def do_upload():
            import warnings
            warnings.filterwarnings('ignore')
            meta = VideoMeta(
                tid=tid,
                title=title,
                desc=desc,
                cover=str(cover_path),
                tags=["AI故事", "TikTok搬运", "故事生成", "AI创作"],
                source="https://www.tiktok.com",
            )
            page = VideoUploaderPage(
                path=str(video_path),
                title=title,
                description=desc
            )
            uploader = VideoUploader(pages=[page], meta=meta, credential=cred, line=Lines.BDA2)
            ret = await uploader.start()
            return ret

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(do_upload())
        loop.close()

        if isinstance(result, dict) and result.get("bvid"):
            log(f"  [B站上传] 成功: {result['bvid']}")
            return result["bvid"]
        log(f"  [B站上传] 完成，无bvid返回")
        return result if result else True
    except Exception as e:
        log(f"  [B站上传] 失败: {e}")
        return None


# ═══════════════════════════════════════════════════════
# 抖音上传
# ═══════════════════════════════════════════════════════
def douyin_upload(video_path: str, title: str, desc: str) -> bool:
    """通过复用ai_video_upload/douyin_upload.py上传到抖音"""
    try:
        import asyncio
        sys.path.insert(0, str(DOUYIN_UPLOAD_SCRIPT.parent))
        from douyin_upload import upload_to_douyin

        result = asyncio.get_event_loop().run_until_complete(
            upload_to_douyin(video_path, title, desc)
        )
        if result:
            log(f"  [抖音上传] 成功")
            return True
        log(f"  [抖音上传] 返回失败")
        return False
    except Exception as e:
        log(f"  [抖音上传] 异常: {e}")
        return False


def douyin_upload_sync(video_path: str, title: str, desc: str) -> bool:
    """同步版本的抖音上传（启动独立进程执行douyin_upload.py）"""
    try:
        cmd = [
            sys.executable, str(DOUYIN_UPLOAD_SCRIPT),
            "--video", video_path,
            "--title", title,
            "--desc", desc,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0:
            log(f"  [抖音上传] 成功")
            return True
        log(f"  [抖音上传] 失败: {result.stderr[-200:]}")
        return False
    except Exception as e:
        log(f"  [抖音上传] 异常: {e}")
        return False


# ═══════════════════════════════════════════════════════
# LLM生成简介
# ═══════════════════════════════════════════════════════
def generate_desc_and_title(title: str, keyword: str) -> tuple[str, str]:
    """用LLM根据标题生成B站标题和简介"""
    try:
        import urllib.request

        payload = {
            "model": "qwen2.5:32b-instruct",
            "messages": [{
                "role": "user",
                "content": f'''根据这个TikTok视频的标题，生成B站风格的标题和简介。

原标题: {title}
搜索关键词: {keyword}

要求：
1. B站标题：吸引眼球，可以有emoji，15-40字，包含AI、故事等关键词
2. 简介：2-3句，概括视频精彩内容，引导点赞投币关注，带2-3个相关标签

格式：
标题：[标题]
简介：[简介]'''
            }],
            "stream": False,
            "max_tokens": 150,
            "temperature": 0.7
        }

        req = urllib.request.Request(
            "http://localhost:11434/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        text = result["choices"][0]["message"]["content"].strip()
        log(f"  [LLM] 原始输出:\n{text[:300]}")

        new_title = title
        desc = f"AI故事 · {datetime.now().strftime('%Y-%m-%d')}"

        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("标题："):
                new_title = line[3:].strip()
            elif line.startswith("简介："):
                desc = line[3:].strip()

        if len(new_title) > 50:
            new_title = title[:45] + "..."

        return new_title, desc
    except Exception as e:
        log(f"  [LLM] 生成失败: {e}")
        return title, f"AI故事视频 · {datetime.now().strftime('%Y-%m-%d')}"


# ═══════════════════════════════════════════════════════
# 流水线主逻辑
# ═══════════════════════════════════════════════════════
def run_pipeline():
    open(LOG_FILE, "w").close()

    log("=" * 60)
    log("TikTok故事AI视频搬运流水线启动")

    if not acquire_lock():
        log("另一个进程正在运行，退出")
        return

    WORK_DIR.mkdir(parents=True, exist_ok=True)

    # 1. LLM生成搜索关键词
    keywords = generate_search_keywords()

    # 2. 搜索
    all_results = []
    for kw in keywords:
        log(f"[搜索] 关键词: {kw}")
        results = search_tiktok_via_api(kw, count=10)
        log(f"  找到 {len(results)} 个视频")
        all_results.extend(results)
        time.sleep(2)

    if not all_results:
        log("[搜索] 无结果，尝试yt-dlp搜索...")
        for kw in keywords:
            results = search_tiktok(kw, count=10)
            for r in results:
                r["title"] = f"AI Story {r['id']}"
                r["keyword"] = kw
            all_results.extend(results)
            time.sleep(1)

    log(f"[搜索] 共找到 {len(all_results)} 个视频")
    if not all_results:
        log("[搜索] 失败，退出")
        return

    # 2. 排除历史
    history = load_history()
    upload_history = load_upload_history()
    downloaded = set(history.keys())
    uploaded = set(upload_history.keys())
    candidates = [v for v in all_results if v["id"] not in downloaded and v["id"] not in uploaded]
    log(f"[候选] {len(candidates)}个（已排除下载过{len(all_results)-len(candidates)-len(uploaded)}个 + 已上传{len(uploaded)}个）")

    if not candidates:
        log("[候选] 没有新视频，退出")
        return

    # 3. 获取详细信息并打分
    scored = []
    for v in candidates[:TOP_CANDIDATES]:
        info = get_tiktok_video_info(v["id"])
        if not info or not info.get("title"):
            info = v  # fallback

        info["keyword"] = v.get("keyword", "")
        score = score_tiktok_video(info)
        info["score"] = score
        scored.append(info)
        log(f"  [{info['id']}] 赞:{info.get('like',0)} 播:{info.get('view',0)} → 评分:{score:.4f}")
        time.sleep(0.5)

    if not scored:
        log("[评分] 无可用视频，退出")
        return

    scored.sort(key=lambda x: x["score"], reverse=True)
    top_videos = scored[:MAX_DOWNLOADS_PER_RUN]

    log(f"[选中] 将处理 {len(top_videos)} 个视频")

    for video in top_videos:
        vid = video["id"]
        title = video.get("title", f"AI Story {vid}")
        keyword = video.get("keyword", "")
        log(f"\n{'='*40}")
        log(f"[处理] {vid}: {title}")

        # 4. 下载
        log("[下载] 开始下载...")
        video_file, subtitle_file = download_tiktok_video(vid, WORK_DIR)
        if not video_file:
            log("[下载] 失败，跳过")
            continue

        size_mb = video_file.stat().st_size / 1024 / 1024
        if size_mb < 0.5:
            log(f"[下载] 文件太小({size_mb:.1f}MB)，跳过")
            continue

        # 5. 裁剪视频（上下左右各裁剪1%）
        log("[裁剪] 开始裁剪1%...")
        cropped_file = WORK_DIR / f"{vid}_cropped.mp4"
        crop_result = crop_video(video_file, cropped_file)
        if crop_result:
            video_file.unlink()
            video_file = cropped_file
            log("[裁剪] 完成")
        else:
            log("[裁剪] 失败，使用原文件")

        # 5b. 生成并烧录字幕（优先下载字幕，兜底Whisper+LLM）
        log("[字幕] 生成并烧录字幕...")
        video_file = burn_subtitle(video_file, vid, subtitle_file)

        # 6. 生成标题描述
        log("[LLM] 生成标题和简介...")
        new_title, desc = generate_desc_and_title(title, keyword)
        log(f"  标题: {new_title}")
        log(f"  简介: {desc[:50]}...")

        # 7. B站上传
        log("[B站] 开始上传...")
        bili_result = biliup_upload(str(video_file), new_title, desc)
        if bili_result:
            log("✅ B站上传成功!")
        else:
            log("⚠️ B站上传失败")

        # 8. 抖音上传
        log("[抖音] 开始上传...")
        dy_result = douyin_upload_sync(str(video_file), new_title, desc)
        if dy_result:
            log("✅ 抖音上传成功!")
        else:
            log("⚠️ 抖音上传失败")

        # 9. 记录历史
        history[vid] = {
            "title": title,
            "new_title": new_title,
            "downloaded_at": time.strftime("%Y-%m-%d %H:%M"),
            "file": str(video_file),
            "bili_result": str(bili_result),
            "dy_result": str(dy_result),
        }
        save_history(history)

        if bili_result or dy_result:
            upload_history[vid] = {
                "title": new_title,
                "uploaded_at": time.strftime("%Y-%m-%d %H:%M"),
            }
            save_upload_history(upload_history)

        log(f"[完成] {vid} 处理完毕")

    log("=" * 60)
    log("✅ 流水线完成!")


if __name__ == "__main__":
    run_pipeline()