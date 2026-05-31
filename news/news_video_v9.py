#!/usr/bin/env python3
"""
news_video_v9.py — 信息差视频生产流水线 v9.0

参考视频 BV1KMVp6VEuv 分析结果：
  - 1567 字文稿 / 4分51秒 = 5.4 字/秒（贴近播音语速）
  - 7 个话题 × 约35秒 = 约4分05秒（加片头片尾 ≈ 5分钟）
  - 叙事散文风格：固定过渡词「第一，」「第二，」「第三，」
  - 数据密集型内容：具体数字、排名、百分比
  - 片头：固定开场白；片尾：固定收尾语

本版本核心改动：
  1. 语速：目标 5.6 chars/sec，每话题 180-220 字文案
  2. 话题：7个话题 × ~35秒，严格精选有数据的话题
  3. 文风：叙事散文体，「第一，」「第二，」过渡，过渡词也算在时间内
  4. 片头+片尾固定文案（不计入话题时间）
  5. TTS：XTTS v2 本地克隆，失败则 EdgeTTS
"""

import os, sys, re, uuid, shutil, subprocess, asyncio, requests, json, hashlib, threading, glob
from pathlib import Path
from datetime import datetime
from datetime import datetime as _dt
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ══════════════════════════════════════════════════════════════════════════════
# 共享 Session
# ══════════════════════════════════════════════════════════════════════════════
_session = requests.Session()
_session.mount(
    'https://',
    HTTPAdapter(
        max_retries=Retry(total=3, backoff_factor=1.5, status_forcelist={429, 500, 502, 503, 504})
    )
)

def _get_session() -> requests.Session:
    return _session

# ══════════════════════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = Path("/Users/kaikai/ai_video_project/news_outputs")
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
CHANNEL_NAME = "20岁还没开始环球旅行"
_TOPIC_SCRIPTS_CACHE = None
_WHISPER_MODEL = None
_WHISPER_MODEL_LOCK = threading.Lock()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}

# 语音克隆参考音频
VOICE_CLONE_REF_AUDIO = "/Users/kaikai/scripts/config/ref_60s_16k.wav"

# v9 固定配置
TARGET_TOPICS = 7           # 话题数量
TOPIC_DURATION = 35         # 每话题目标秒数
TARGET_CHARS = 200         # 每话题目标字数（35秒 × 5.6 字/秒 ≈ 196 字）
TARGET_SPEECH_RATE = 5.6   # 字/秒

TASK_ID = uuid.uuid4().hex[:8].replace("'", "").replace("`", "")

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

# ══════════════════════════════════════════════════════════════════════════════
# B站上传凭证
# ══════════════════════════════════════════════════════════════════════════════

def _load_upload_cookies():
    paths = [
        Path("/Users/kaikai/scripts/20岁还没开始环球旅行_cookies.txt"),
    ]
    for p in paths:
        if p.exists():
            try:
                data = json.loads(p.read_text())
                if isinstance(data, dict) and 'SESSDATA' in data and 'bili_jct' in data:
                    return data
            except Exception:
                pass
    return {}

BILIBILI_COOKIES = _load_upload_cookies()
if not BILIBILI_COOKIES:
    log("警告: 无法加载账号B cookie，使用降级方案")

# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

def check_title_duplicated(new_title: str, channel_uid: str = "1650357577") -> bool:
    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com",
    }
    try:
        url = f"https://api.bilibili.com/x/space/arc/search?mid={channel_uid}&pn=1&jsonp=jsonp&callback=&order=pubdate&keyword=&order_version=&page_size=30"
        r = _get_session().get(url, headers=browser_headers, timeout=10)
        data = r.json()
        vlist = data.get("data", {}).get("list", {}).get("vlist", [])
        date_pattern = r'\d{4}[年.]?\d{1,2}[月.]?\d{1,2}'
        time_pattern = r'[早晚]差'
        new_dates = re.findall(date_pattern, new_title)
        new_times = re.findall(time_pattern, new_title)
        for v in vlist:
            old_title = v.get("title", "")
            old_dates = re.findall(date_pattern, old_title)
            old_times = re.findall(time_pattern, old_title)
            if new_dates and old_dates == new_dates and new_times == old_times:
                log(f"  ⚠️ 检测到同日同时段标题已上传: {old_title}")
                return True
        log(f"  ✅ 标题去重检查通过（检索了 {len(vlist)} 条视频）")
        return False
    except Exception as e:
        log(f"  ⚠️ 去重检查异常（允许上传）: {e}")
        return False

def verify_video_has_frames(path: str) -> bool:
    import numpy as np
    from PIL import Image
    timestamps = [5, 30, 60]
    for t in timestamps:
        frame_path = f"/tmp/vf_{uuid.uuid4().hex[:6]}.jpg"
        r = subprocess.run([
            "ffmpeg", "-y", "-ss", str(t), "-i", path,
            "-vframes", "1", "-q:v", "2", frame_path
        ], capture_output=True, timeout=10)
        if r.returncode != 0 or not os.path.exists(frame_path):
            continue
        img = Image.open(frame_path)
        arr = np.array(img)
        brightness = arr.mean()
        os.remove(frame_path)
        if brightness > 15:
            return True
    return False

# ══════════════════════════════════════════════════════════════════════════════
# 选题（v9：只选有数据含量的话题）
# ══════════════════════════════════════════════════════════════════════════════

def get_hot_topics_v9(num: int = 20) -> list:
    """
    v9 精选话题策略：数据型话题优先（排名、数字、百分比），
    避免空洞话题，确保每个话题都有料可说
    """
    topics = []
    seen_keys = set()

    # ── 抖音热搜 ─────────────────────────────────
    try:
        r = _get_session().get(
            "https://www.iesdouyin.com/aweme/v1/hot/search/list/",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)"},
            timeout=8
        )
        if r.status_code == 200:
            word_list = r.json().get("data", {}).get("word_list", [])
            for item in word_list[:num]:
                word = item.get("word", "")
                hot_val = item.get("hot_value", "")
                if word and len(word) >= 3:
                    key = word
                    if key not in seen_keys:
                        seen_keys.add(key)
                        topics.append({"topic": word, "source": "抖音热搜", "hot": hot_val})
            log(f"  抖音热搜: {len(word_list)}条")
    except Exception as e:
        log(f"  ⚠️ 抖音热搜: {e}")

    # ── 微博热搜 ─────────────────────────────────
    try:
        r = _get_session().get(
            "https://weibo.com/ajax/side/hotSearch",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8
        )
        if r.status_code == 200:
            band_list = r.json().get("data", {}).get("band_list", [])
            for item in band_list[:num]:
                word = item.get("word", "")
                if word and len(word) >= 3:
                    key = word
                    if key not in seen_keys:
                        seen_keys.add(key)
                        topics.append({"topic": word, "source": "微博热搜"})
    except Exception as e:
        log(f"  ⚠️ 微博热搜: {e}")

    # ── 百度实时 ─────────────────────────────────
    try:
        r = _get_session().get(
            "https://top.baidu.com/api?get=news&flag=1",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8
        )
        if r.status_code == 200:
            news_list = r.json().get("data", {}).get("newsList", [])
            for item in news_list[:num//2]:
                word = item.get("word", "")
                if word and len(word) >= 3:
                    key = word
                    if key not in seen_keys:
                        seen_keys.add(key)
                        topics.append({"topic": word, "source": "百度实时"})
    except Exception as e:
        log(f"  ⚠️ 百度实时: {e}")

    # 数据型话题优先排序（话题含数字/排名/百分号的优先）
    def data_priority(t):
        topic = t["topic"]
        score = 0
        if re.search(r'^\d+', topic): score += 3
        if re.search(r'[百千万亿%]', topic): score += 2
        if re.search(r'[第名排]', topic): score += 2
        return score

    topics.sort(key=data_priority, reverse=True)
    douyin = [t for t in topics if t.get("source") == "抖音热搜"][:12]
    baidu = [t for t in topics if t.get("source") == "百度实时"][:8]
    others = [t for t in topics if t.get("source") not in ("抖音热搜", "百度实时")][:5]
    diversified = douyin + baidu + others

    if len(diversified) < 10:
        log(f"  ⚠️ 话题不足10条，仅 {len(diversified)} 条")

    log(f"  选题去重后: {len(diversified)}条")
    return diversified[:num]


def _fetch_bing_news(topic: str) -> str:
    try:
        import urllib.parse
        q = urllib.parse.quote(topic)
        r = _get_session().get(
            f"https://cn.bing.com/news/search?q={q}&format=RSS",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
            timeout=10
        )
        if r.status_code == 200:
            import re
            titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', r.text)
            descs = re.findall(r'<description><!\[CDATA\[(.*?)\]\]></description>', r.text)
            if titles:
                main_title = titles[0]
                desc = re.sub(r'<[^>]+>', '', descs[0]) if descs else ""
                desc = desc.strip()[:200]
                script = f"{main_title}。{desc}" if desc else main_title
                script = re.sub(r'\s+', ' ', script).strip()
                if len(script) > 30:
                    return script
    except Exception:
        pass
    return ""


def _call_minimax_script(topic: str, index: int) -> str:
    """v9 专用脚本生成：叙事散文风格，180-220字，5.6字/秒"""
    prompt = f"""你是一个有10年经验的B站口播博主，专做热点新闻信息差，风格诙谐幽默。

请围绕话题「{topic}」写一段180-220字的口播文案。

要求（极其重要）：
1. 开头必须有强钩子！用意外、震惊、反差的方式切入
2. 像叙事散文一样娓娓道来，用「第一，」「第二，」「第三，」过渡（数字也算时间）
3. 内容要有具体数字、排名、百分比，数据越精确越好
4. 用口语短句，每句不超过12字，节奏快
5. 可以用"诶"、"卧槽"、"真的假的"增加幽默感
6. 避免：据悉、数据显示、首先其次最后、郑重声明、官话套话
7. 内容要有信息增量，你知道的别人不知道，或者别人知道但理解错了
8. 直接输出文案，一气呵成，不要前缀不要注释不要空行

口播文案："""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from config.minimax_client import MiniMaxClient
        script = MiniMaxClient().generate_script(topic)
        if script and len(script) >= 10:
            return script
    except Exception:
        pass
    return ""


def generate_script_v9(topic: str, index: int) -> str:
    """
    v9 文案生成：
    强制调用 MiniMax API 生成 180-220 字叙事散文风格脚本
    失败则 Bing 新闻搜索兜底
    """
    global _TOPIC_SCRIPTS_CACHE

    log(f"  🤖 MiniMax脚本[{index+1}]: {topic[:20]}...")
    script = _call_minimax_script(topic, index)
    if script and len(script) >= 10:
        _TOPIC_SCRIPTS_CACHE[topic] = script
        return script

    log(f"  🔍 Bing搜索兜底: {topic[:20]}...")
    script = _fetch_bing_news(topic)
    if script:
        _TOPIC_SCRIPTS_CACHE[topic] = script
        return script

    log(f"  ⚠️ 兜底使用话题本身: {topic[:20]}...")
    fallback = f"{topic}。"
    _TOPIC_SCRIPTS_CACHE[topic] = fallback
    return fallback


# ══════════════════════════════════════════════════════════════════════════════
# 视频搜索下载
# ══════════════════════════════════════════════════════════════════════════════

def search_bilibili_video(topic: str) -> str:
    import time
    for attempt in range(3):
        try:
            import urllib.parse
            q = urllib.parse.quote(topic)
            r = _get_session().get(
                f"https://api.bilibili.com/x/web-interface/search/all/v2?keyword={q}&page=1&page_size=8",
                headers=HEADERS, timeout=15
            )
            d = r.json()
            if d.get("code") != 0:
                log(f"  ⚠️ B站搜索[{attempt+1}]失败: code={d.get('code')}，{2**(attempt+1)}秒后重试")
                time.sleep(2 ** (attempt + 1))
                continue
            for item in d.get("data", {}).get("result", []):
                if isinstance(item, dict) and item.get("result_type") == "video":
                    for v in item.get("data", [])[:3]:
                        bv = v.get("bvid", "")
                        dur = v.get("duration", "0:00")
                        try:
                            parts = dur.split(":")
                            secs = int(parts[0]) * 60 + int(parts[1])
                            if 10 <= secs <= 300:
                                log(f"  🎬 B站: [{bv}] {v.get('title','')[:30]} ({dur})")
                                return bv
                        except Exception:
                            pass
            log(f"  ⚠️ B站搜索[{attempt+1}]未找到合适视频，{2**(attempt+1)}秒后重试")
            time.sleep(2 ** (attempt + 1))
        except Exception as e:
            log(f"  ⚠️ B站搜索[{attempt+1}]异常: {e}，{2**(attempt+1)}秒后重试")
            time.sleep(2 ** (attempt + 1))
    log(f"  ⚠️ B站搜索最终失败: {topic[:15]}")
    return None

def download_bilibili_video(bvid: str, output_path: str, clip_dur: float = None) -> bool:
    try:
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "-f", "bestvideo[height>=1080]+bestaudio/best[height>=1080]/best[height>=720]/best",
            "--merge-output-format", "mp4",
            "--cookies-from-browser", "chrome",
            "--no-warnings",
            "-o", output_path,
            f"https://www.bilibili.com/video/{bvid}"
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 5000:
            log(f"  ✅ BV={bvid} {os.path.getsize(output_path)//1024}KB (yt-dlp)")
            return True
    except subprocess.TimeoutExpired:
        log(f"  ⚠️ BV={bvid} 下载超时")
    except Exception as e:
        log(f"  ⚠️ BV={bvid} 下载异常: {e}")
    return False

# ══════════════════════════════════════════════════════════════════════════════
# TTS（XTTS v2 + Edge TTS）
# ══════════════════════════════════════════════════════════════════════════════

_XTTS_MODEL = None
_XTTS_DEVICE = None

def _get_xtts_model():
    global _XTTS_MODEL, _XTTS_DEVICE
    if _XTTS_MODEL is not None:
        return _XTTS_MODEL
    import torch
    _XTTS_DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
    os.environ["COQUI_TOS_AGREED"] = "1"
    from TTS.api import TTS
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
    tts.to(_XTTS_DEVICE)
    _XTTS_MODEL = tts
    log(f"  🤖 XTTS模型已加载，设备: {_XTTS_DEVICE}")
    return tts

def generate_tts_clone(script: str, output_path: str, index: int) -> bool:
    ref_audio = VOICE_CLONE_REF_AUDIO
    if not ref_audio or not os.path.exists(ref_audio):
        log(f"  ⚠️ 参考音频不存在: {ref_audio}")
        return False

    work_dir = f"/tmp/xtts_clone_{TASK_ID}"
    os.makedirs(work_dir, exist_ok=True)

    ref_wav = f"{work_dir}/ref.wav"
    subprocess.run([
        "ffmpeg", "-i", ref_audio, "-ar", "22050", "-ac", "1",
        "-ss", "0", "-t", "30", "-y", ref_wav
    ], capture_output=True, timeout=30)

    if not os.path.exists(ref_wav):
        log(f"  ⚠️ 参考音频预处理失败")
        return False

    log(f"  🎙️ 第{index+1}条XTTS克隆音色...")
    try:
        import numpy as np
        import threading

        result = [None]
        error = [None]

        def _tts_worker():
            try:
                tts = _get_xtts_model()
                result[0] = tts.tts(text=script, speaker_wav=ref_wav, language='zh-cn')
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=_tts_worker)
        t.daemon = True
        t.start()
        t.join(timeout=120)
        if t.is_alive():
            log(f"  ⚠️ XTTS第{index+1}条超时，跳过")
            shutil.rmtree(work_dir, ignore_errors=True)
            return False
        if error[0]:
            raise error[0]
        wav = result[0]
        if isinstance(wav, list):
            wav = np.array(wav, dtype=np.float32)
        import scipy.io.wavfile as wavfile
        wavfile.write(f"{work_dir}/output.wav", 24000, (wav * 32767).astype(np.int16))
        if os.path.exists(f"{work_dir}/output.wav") and os.path.getsize(f"{work_dir}/output.wav") > 2000:
            subprocess.run([
                "ffmpeg", "-i", f"{work_dir}/output.wav",
                "-ar", "44100", "-ab", "192k", output_path, "-y"
            ], capture_output=True, timeout=30)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 2000:
                dur = float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", output_path],
                    capture_output=True, text=True, timeout=5
                ).stdout.strip() or 0)
                log(f"  ✅ 第{index+1}条音频(XTTS克隆): {dur:.0f}秒")
                shutil.rmtree(work_dir, ignore_errors=True)
                return True
        log(f"  ⚠️ XTTS第{index+1}条生成失败")
    except TimeoutError:
        log(f"  ⚠️ XTTS第{index+1}条超时，跳过")
    except Exception as e:
        log(f"  ⚠️ XTTS第{index+1}条异常: {e}")
    shutil.rmtree(work_dir, ignore_errors=True)
    return False

def generate_tts(script: str, output_path: str, index: int) -> bool:
    """优先 XTTS 克隆，失败则 Edge TTS"""
    if os.path.exists(output_path) and os.path.getsize(output_path) > 2000:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", output_path],
            capture_output=True, text=True, timeout=5
        ).stdout.strip() or 0)
        if dur > 5:
            log(f"  ✅ 第{index+1}条音频(缓存): {dur:.0f}秒")
            return True

    if os.path.exists(VOICE_CLONE_REF_AUDIO):
        if generate_tts_clone(script, output_path, index):
            return True
        log(f"  ⚠️ XTTS克隆失败，尝试Edge TTS...")

    import time
    for attempt in range(3):
        try:
            async def _run():
                import edge_tts
                communicate = edge_tts.Communicate(
                    script,
                    voice="zh-CN-YunxiNeural",
                    rate="+10%",
                    pitch="-2Hz",
                )
                await communicate.save(output_path)

            asyncio.run(_run())
            if os.path.exists(output_path) and os.path.getsize(output_path) > 2000:
                dur = float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", output_path],
                    capture_output=True, text=True, timeout=5
                ).stdout.strip() or 0)
                log(f"  ✅ 第{index+1}条音频(YunxiNeural): {dur:.0f}秒")
                return True
        except Exception as e:
            wait = 2 ** attempt
            log(f"  ⚠️ TTS第{attempt+1}次失败: {e}，{wait}秒后重试")
            time.sleep(wait)
    log(f"  ⚠️ TTS失败")
    return False

# ══════════════════════════════════════════════════════════════════════════════
# 字幕生成
# ══════════════════════════════════════════════════════════════════════════════

def _get_whisper_model():
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        with _WHISPER_MODEL_LOCK:
            if _WHISPER_MODEL is None:
                from faster_whisper import WhisperModel
                _WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    return _WHISPER_MODEL

def generate_srt_from_audio(audio_path: str, srt_path: str, index: int, script: str = "") -> bool:
    import warnings
    warnings.filterwarnings("ignore", message="divide by zero", category=RuntimeWarning)
    warnings.filterwarnings("ignore", message="overflow encountered", category=RuntimeWarning)
    warnings.filterwarnings("ignore", message="invalid value encountered", category=RuntimeWarning)
    import time
    for attempt in range(3):
        try:
            model = _get_whisper_model()
            segments, _ = model.transcribe(audio_path, language="zh", word_timestamps=True)

            lines = []
            for seg_idx, seg in enumerate(segments):
                words = seg.words
                if not words:
                    continue
                start = words[0].start
                end = words[-1].end
                text = "".join(w.word.strip() for w in words)
                if not text:
                    continue
                def fmt(t):
                    h = int(t // 3600)
                    m = int((t % 3600) // 60)
                    s = int(t % 60)
                    ms = int((t % 1) * 1000)
                    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
                lines.append(f"{seg_idx + 1}\n{fmt(start)} --> {fmt(end)}\n{text}\n")

            if lines:
                with open(srt_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                log(f"  ✅ 第{index+1}条字幕: {len(lines)}句")
                return True
            else:
                log(f"  ⚠️ 第{index+1}条字幕: Whisper未识别，使用等长fallback")
        except Exception as e:
            wait = 2 ** attempt
            log(f"  ⚠️ 第{index+1}条字幕第{attempt+1}次失败: {e}，{wait}秒后重试")
            time.sleep(wait)

    # Fallback: 等时长分割
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=5
        ).stdout.strip() or 10)

        if dur <= 0:
            return False

        sentences = script.split("。") if script else [""]
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            sentences = [""]

        seg_dur = dur / len(sentences)
        lines = []
        for i, sent in enumerate(sentences):
            start = i * seg_dur
            end = (i + 1) * seg_dur
            def fmt(t):
                h = int(t // 3600)
                m = int((t % 3600) // 60)
                s = int(t % 60)
                ms = int((t % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            lines.append(f"{i+1}\n{fmt(start)} --> {fmt(end)}\n{sent}\n")

        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log(f"  ⚡ 第{index+1}条字幕(等长fallback): {len(sentences)}句")
        return True
    except Exception as e:
        log(f"  ⚠️ 第{index+1}条字幕fallback也失败: {e}")
    return False

# ══════════════════════════════════════════════════════════════════════════════
# 字幕烧录（PIL）
# ══════════════════════════════════════════════════════════════════════════════

def burn_subtitle_pil(video_path: str, srt_path: str, output_path: str, clip_dur: float,
                      tts_audio_path: str = "", topic_title: str = "", segment_index: int = 1,
                      total_segments: int = 1, all_topics: list = None, video_offset: float = 0.0,
                      video_total_dur: float = 0.0) -> bool:
    """
    字幕烧录布局（与 v8 相同）：
    1920x1080 三层：
    - 字幕区 y=815-950：半透明黑条底，白色话题标题+白色字幕正文
    - 章节栏 y=950-1080：深灰底，话题标题沿时间轴分布
    """
    frame_dir = None
    cap = None
    try:
        import pysrt
        from PIL import Image, ImageDraw, ImageFont

        if not os.path.exists(srt_path):
            return False
        subs = pysrt.open(srt_path)

        target_w, target_h = 1920, 1080
        topic_show_until = clip_dur * 0.30
        subtitle_bg_top = 815
        subtitle_bg_bottom = 950
        topic_text_y = 822
        topic_font_size = 35
        subtitle_text_y = 880
        subtitle_font_size = 34
        chapter_bar_top = 950
        chapter_bar_height = target_h - chapter_bar_top

        topic_color = (255, 255, 255)
        subtitle_color = (255, 255, 255)
        stroke_color = (0, 0, 0)

        font_candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
        fnt_path = None
        for fp in font_candidates:
            if os.path.exists(fp):
                try:
                    fnt_path = fp
                    break
                except Exception:
                    continue

        def make_font(size):
            if fnt_path:
                try:
                    return ImageFont.truetype(fnt_path, size)
                except Exception:
                    pass
            return ImageFont.load_default()

        fnt_topic = make_font(topic_font_size)
        fnt_sub = make_font(subtitle_font_size)

        frame_dir = f"/tmp/frames_{uuid.uuid4().hex[:6]}"
        os.makedirs(frame_dir, exist_ok=True)

        import cv2
        cap = cv2.VideoCapture(video_path)
        fps_in = cap.get(cv2.CAP_PROP_FPS) or 30
        src_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280
        src_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720
        max_frames = int(clip_dur * fps_in) + 30
        rendered = set()
        frame_idx = 0

        def stroke_text(draw, pos, text, font, fill, stroke_fill, width=2):
            x, y = pos
            for dx in range(-width, width + 1):
                for dy in range(-width, width + 1):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
            draw.text(pos, text, font=font, fill=fill)

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx >= max_frames:
                log(f"  ⚠️ 帧数已达上限 {max_frames}，截断")
                break
            timestamp = frame_idx / fps_in

            current_sub = None
            for sub in subs:
                start_s = sub.start.ordinal / 1000.0
                end_s = sub.end.ordinal / 1000.0
                if start_s <= timestamp <= end_s:
                    current_sub = sub.text
                    break

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)

            src_w, src_h = pil_img.size
            src_aspect = src_w / src_h
            target_aspect = target_w / target_h
            if src_aspect > target_aspect:
                new_h = target_h
                new_w = int(new_h * src_aspect)
                scaled = pil_img.resize((new_w, new_h), Image.LANCZOS)
                canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
                paste_x = (target_w - new_w) // 2
                canvas.paste(scaled, (paste_x, 0))
            else:
                new_w = target_w
                new_h = int(new_w / src_aspect)
                scaled = pil_img.resize((new_w, new_h), Image.LANCZOS)
                canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
                paste_y = (target_h - new_h) // 2
                canvas.paste(scaled, (0, paste_y))
            pil_img = canvas
            draw = ImageDraw.Draw(pil_img)

            draw.rectangle([0, subtitle_bg_top, target_w, subtitle_bg_bottom], fill=(0, 0, 0, 180))

            if topic_title and timestamp < topic_show_until:
                stroke_text(draw, (30, topic_text_y), topic_title, fnt_topic, topic_color, stroke_color, width=2)

            if current_sub:
                bbox = draw.textbbox((0, 0), current_sub, font=fnt_sub)
                text_w = bbox[2] - bbox[0]
                text_x = (target_w - text_w) // 2
                sub_y = subtitle_text_y if timestamp < topic_show_until else (subtitle_bg_top + (subtitle_bg_bottom - subtitle_bg_top - subtitle_font_size) // 2)
                stroke_text(draw, (text_x, sub_y), current_sub, fnt_sub, subtitle_color, stroke_color, width=2)
                rendered.add(frame_idx)

            # 章节栏
            bar_width = target_w // 3
            bar_left = (target_w - bar_width) // 2
            bar_right = bar_left + bar_width
            draw.rectangle([0, chapter_bar_top, target_w, target_h], fill=(32, 32, 32))

            if all_topics and video_total_dur > 0:
                axis_y = chapter_bar_top + chapter_bar_height // 2
                line_top = axis_y - 2
                line_bottom = axis_y + 2

                axis_left = bar_left + 50
                axis_right = bar_right - 50
                axis_width = axis_right - axis_left

                draw.rectangle([axis_left, line_top, axis_right, line_bottom], fill=(80, 80, 80))

                topic_positions = []
                for (t_name, t_start, t_end) in all_topics:
                    frac_start = t_start / video_total_dur if video_total_dur > 0 else 0
                    frac_end = t_end / video_total_dur if video_total_dur > 0 else 0
                    x_start = axis_left + int(frac_start * axis_width)
                    x_end = axis_left + int(frac_end * axis_width)
                    topic_positions.append((t_name, x_start, x_end))

                elapsed_global = (video_offset + timestamp) / video_total_dur if video_total_dur > 0 else 0

                for idx, (t_name, x_start, x_end) in enumerate(topic_positions):
                    seg_frac = (idx + 1) / len(topic_positions)
                    is_past = elapsed_global >= seg_frac - (1 / len(topic_positions) / 2)
                    seg_color = (180, 180, 180) if is_past else (60, 60, 60)
                    draw.rectangle([x_start, line_top, x_end, line_bottom], fill=seg_color)

                    node_color = (220, 220, 220) if is_past else (100, 100, 100)
                    mid_x = (x_start + x_end) // 2
                    draw.ellipse([mid_x - 4, axis_y - 4, mid_x + 4, axis_y + 4], fill=node_color)

                    seg_width = x_end - x_start
                    best_font_size = 8
                    for fs in range(16, 5, -1):
                        fnt_test = make_font(fs)
                        bbox = draw.textbbox((0, 0), t_name, font=fnt_test)
                        text_w = bbox[2] - bbox[0]
                        if text_w <= seg_width - 6:
                            best_font_size = fs
                            break

                    fnt_seg = make_font(best_font_size)
                    bbox = draw.textbbox((0, 0), t_name, font=fnt_seg)
                    text_w = bbox[2] - bbox[0]
                    text_h = bbox[3] - bbox[1]
                    text_x = x_start + (seg_width - text_w) // 2
                    text_y = axis_y - text_h - 6

                    draw.rectangle([text_x - 2, text_y - 2, text_x + text_w + 2, text_y + text_h + 2], fill=(40, 40, 40))
                    draw.text((text_x, text_y), t_name, font=fnt_seg, fill=(160, 160, 160))

                progress_frac = elapsed_global
                progress_x = axis_left + int(progress_frac * axis_width)
                draw.rectangle([progress_x - 1, line_top - 3, progress_x + 1, line_bottom + 3], fill=(255, 200, 80))

            pil_img.save(f"{frame_dir}/frame_{frame_idx:06d}.jpg", quality=90)
            frame_idx += 1

        cap.release()

        if frame_idx == 0:
            log(f"  ⚠️ 无帧可处理")
            return False

        log(f"  PIL烧录: {frame_idx}帧, {len(rendered)}帧有字幕")

        frame_files = sorted(glob.glob(f"{frame_dir}/*.jpg"))
        if not frame_files:
            log(f"  ⚠️ 无帧文件可处理")
            return False

        has_audio = tts_audio_path and os.path.exists(tts_audio_path)

        cmd = [
            "ffmpeg", "-y",
            "-framerate", "30",
            "-i", f"{frame_dir}/frame_%06d.jpg",
        ]
        if has_audio:
            cmd += ["-i", tts_audio_path, "-map", "0:v", "-map", "1:a"]
        else:
            cmd += ["-map", "0:v"]
        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-profile:v", "high", "-level", "3.1",
            "-vf", "scale=1920:1080,setsar=1",
            "-c:a", "aac", "-b:a", "128k" if has_audio else "192k",
            "-t", str(clip_dur),
            "-pix_fmt", "yuv420p",
            "-r", "30",
            output_path
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=int(clip_dur * 1.5 + 60))

        if r.returncode == 0:
            if frame_dir is not None and os.path.exists(frame_dir):
                shutil.rmtree(frame_dir, ignore_errors=True)
            return os.path.exists(output_path) and os.path.getsize(output_path) > 5000
        else:
            log(f"  ⚠️ PIL烧录重编码失败: {r.stderr[-200:]}")
            return False

    except Exception as e:
        log(f"  ⚠️ PIL烧录异常: {e}")
        if cap is not None:
            cap.release()
        if frame_dir is not None and os.path.exists(frame_dir):
            shutil.rmtree(frame_dir, ignore_errors=True)
        return False


def verify_subtitles_burned(video_path: str) -> bool:
    import numpy as np
    from PIL import Image
    for t in [5, 15, 25]:
        frame_path = f"/tmp/sub_check_{uuid.uuid4().hex[:6]}.jpg"
        r = subprocess.run([
            "ffmpeg", "-y", "-ss", str(t), "-i", video_path,
            "-vframes", "1", "-q:v", "2", frame_path
        ], capture_output=True, timeout=10)
        if r.returncode != 0 or not os.path.exists(frame_path):
            continue
        img = Image.open(frame_path)
        arr = np.array(img)
        h, w = arr.shape[:2]
        bottom = arr[int(h * 0.75):, :, :]
        brightness = bottom.mean()
        os.remove(frame_path)
        if brightness > 20:
            return True
    return False

def verify_video_quality(video_path: str) -> dict:
    import numpy as np
    from PIL import Image
    reasons = []
    checks = {
        "文件存在": os.path.exists(video_path),
        "文件>800KB": os.path.getsize(video_path) > 800 * 1024,
    }

    if not checks["文件存在"]:
        reasons.append("文件不存在")
        return {"pass": False, "reasons": reasons}

    if not checks["文件>800KB"]:
        reasons.append(f"文件太小: {os.path.getsize(video_path)//1024}KB")
        return {"pass": False, "reasons": reasons}

    info = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_name,codec_type,width,height",
         "-of", "json", video_path],
        capture_output=True, text=True, timeout=15
    )
    try:
        data = json.loads(info.stdout)
    except Exception:
        reasons.append("ffprobe解析失败")
        return {"pass": False, "reasons": reasons}

    fmt = data.get("format", {})
    dur = float(fmt.get("duration", 0))
    checks["时长>10s"] = dur > 10
    if not checks["时长>10s"]:
        reasons.append(f"时长不足: {dur:.0f}s")

    streams = data.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    checks["有视频流"] = has_video
    checks["有音频流"] = has_audio
    if not has_video:
        reasons.append("无视频流")
    if not has_audio:
        reasons.append("无音频流")

    has_content = verify_video_has_frames(video_path)
    checks["有画面"] = has_content
    if not has_content:
        reasons.append("画面全黑/无帧")

    has_subs = verify_subtitles_burned(video_path)
    checks["有字幕"] = has_subs
    if not has_subs:
        reasons.append("字幕未烧录")

    for s in streams:
        if s.get("codec_type") == "video":
            w, h = s.get("width", 0), s.get("height", 0)
            if w < 640 or h < 360:
                reasons.append(f"分辨率过低: {w}x{h}")
                break

    all_pass = all(checks.values())
    log(f"  🔍 质量检查: {'全部通过' if all_pass else '失败'}")
    for k, v in checks.items():
        log(f"     {'✅' if v else '❌'} {k}")
    if reasons:
        for r in reasons:
            log(f"     → {r}")
    return {"pass": all_pass, "reasons": reasons}

# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════

def main(date_str: str = "today"):
    log(f"\n{'='*60}")
    log(f"📺 v9 启动 | 任务ID: {TASK_ID} | 日期: {date_str}")
    log(f"   参考: BV1KMVp6VEuv (5.6字/秒, 7话题×35秒, 叙事散文风格)")
    log(f"{'='*60}")

    global _TOPIC_SCRIPTS_CACHE
    _TOPIC_SCRIPTS_CACHE = {}

    # ── Step 1: 选题（v9：精选7个有数据含量的话题）────────────────
    log(f"\n① 选题（v9: {TARGET_TOPICS}个精选话题）...")

    all_hot_topics = get_hot_topics_v9(num=20)
    hot_topics = [t["topic"] for t in all_hot_topics[:20]]

    if not hot_topics:
        log("  ❌ 话题为空，退出")
        return

    # v9: 固定7个话题
    topics = [{"topic": t, "bvid": None, "hot": ""} for t in hot_topics[:TARGET_TOPICS]]

    if len(topics) < 5:
        log("  ❌ 话题不足5条，退出")
        return

    global _TODAY_TOPICS
    _TODAY_TOPICS = topics
    log(f"  共 {len(topics)} 个话题:")
    for i, t in enumerate(topics):
        log(f"    {i+1}. {t['topic']}")

    # ── Step 2: 并行处理所有话题 ─────────────────────────────────────────
    from concurrent.futures import ThreadPoolExecutor, as_completed
    log(f"\n② 并行处理 {len(topics)} 条话题（4线程）...")

    def process_topic(args):
        import traceback as _tb
        i, item = args
        topic = item["topic"]
        sid = f"{TASK_ID}_{i}"
        bv_id = item.get("bvid")
        if bv_id is not None:
            bv_id = str(bv_id)

        try:
            script = generate_script_v9(topic, i)
        except Exception as e:
            log(f"  ⚠️ 第{i+1}条 generate_script_v9 异常: {e}\n{_tb.format_exc()}")
            return None

        audio_path = str(OUTPUT_DIR / f"v9_audio_{sid}.m4a")

        script_hash = hashlib.md5(script.encode()).hexdigest()[:12]
        cached_audio = str(OUTPUT_DIR / f"v9_audio_{script_hash}.m4a")
        if not generate_tts(script, cached_audio, i):
            return None

        audio_dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", cached_audio],
            capture_output=True, text=True, timeout=5
        ).stdout.strip() or 20)
        if audio_dur < 5:
            log(f"  ⚠️ 第{i+1}条音频{audio_dur:.1f}秒太短，跳过")
            return None

        bg_video_path = str(OUTPUT_DIR / f"v9_bgvideo_{sid}.mp4")
        bg_download_ok = False
        if bv_id:
            bg_download_ok = download_bilibili_video(bv_id, bg_video_path, clip_dur=audio_dur)
        else:
            searched_bv = search_bilibili_video(topic)
            if searched_bv:
                bg_download_ok = download_bilibili_video(searched_bv, bg_video_path, clip_dur=audio_dur)

        if not bg_download_ok or not os.path.exists(bg_video_path) or os.path.getsize(bg_video_path) <= 5000:
            log(f"  ⚠️ 第{i+1}条 bg下载失败，使用纯黑背景")
            try:
                subprocess.run([
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", f"color=c=black:s=1280x720:d={audio_dur}",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k", "-shortest",
                    bg_video_path
                ], capture_output=True, timeout=30)
            except Exception:
                pass

        srt_path = str(OUTPUT_DIR / f"v9_sub_{sid}.srt")
        generate_srt_from_audio(cached_audio, srt_path, i, script)

        return (i, topic, cached_audio, srt_path, bg_video_path, bv_id, audio_dur)

    segments = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(process_topic, (i, item)): i for i, item in enumerate(topics)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                if result:
                    segments.append(result)
                    log(f"  ✅ 第{idx+1}条 [{result[1][:15]}] 完成")
                else:
                    log(f"  ⚠️ 第{idx+1}条失败")
            except Exception as e:
                import traceback as _tb
                log(f"  ⚠️ 第{idx+1}条异常: {e}\n{_tb.format_exc()}")

    if not segments:
        log("❌ 没有可用片段")
        return

    log(f"\n  有效片段: {len(segments)}")

    # ── Step 3: 烧录字幕 ─────────────────────────────────────────────
    def _process_single_clip(args):
        i, topic, audio, srt, bg, dur, seg_idx, video_offset = args
        sid = f"{TASK_ID}_{i}"
        clip_path = str(OUTPUT_DIR / f"v9_clip_{sid}.mp4")
        cropped_bg = str(OUTPUT_DIR / f"v9_crop_{sid}.mp4")

        crop_r = subprocess.run([
            "ffmpeg", "-y", "-i", bg, "-an",
            "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-t", str(dur), "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            cropped_bg
        ], capture_output=True, timeout=int(dur * 2 + 30))

        if crop_r.returncode != 0 or not os.path.exists(cropped_bg):
            return None

        try:
            success = burn_subtitle_pil(
                cropped_bg,
                srt if os.path.exists(srt) else "",
                clip_path,
                dur,
                tts_audio_path=audio,
                topic_title=topic[:30],
                segment_index=seg_idx,
                total_segments=total_segs,
                all_topics=all_topics,
                video_offset=video_offset,
                video_total_dur=video_total_dur
            )
        except Exception as e:
            success = False

        if os.path.exists(cropped_bg):
            os.remove(cropped_bg)

        if not success or not os.path.exists(clip_path):
            return None

        if not verify_video_has_frames(clip_path):
            os.remove(clip_path)
            return None

        has_subs = verify_subtitles_burned(clip_path)
        size_kb = os.path.getsize(clip_path) // 1024
        return (clip_path, topic, dur, has_subs, size_kb)

    log(f"\n③ 烧录字幕（{len(segments)}条并行{min(2, len(segments))}线程）...")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    clips = []
    clip_durations = []

    total_segs = len(segments)
    video_total_dur = sum(dur for _, _, _, _, _, _, dur in segments)

    all_topics = []
    cur_ts = 0.0
    for (_, topic, _, _, _, _, dur) in segments:
        all_topics.append((topic, cur_ts, cur_ts + dur))
        cur_ts += dur

    tasks = []
    for seg_i, (orig_idx, topic, audio, srt, bg, bv, dur) in enumerate(segments):
        if bg and os.path.exists(bg) and os.path.getsize(bg) > 5000:
            offset = 0.0
            for (t_topic, t_start, t_end) in all_topics:
                if t_topic == topic:
                    offset = t_start
                    break
            display_num = len(tasks) + 1
            tasks.append((orig_idx, topic, audio, srt, bg, dur, display_num, offset))

    if tasks:
        burn_workers = min(2, len(tasks))
        with ThreadPoolExecutor(max_workers=burn_workers) as pool:
            futures = {pool.submit(_process_single_clip, t): t[0] for t in tasks}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    result = future.result()
                    if result:
                        clip_path, topic, dur, has_subs, size_kb = result
                        log(f"  ✅ 第{idx+1}条 [{topic[:15]}] {size_kb}KB | 画面✓ | 字幕{'✓' if has_subs else '✗'}")
                        clips.append(clip_path)
                        clip_durations.append((topic, dur))
                    else:
                        log(f"  ⚠️ 第{idx+1}条烧录失败，跳过")
                except Exception as e:
                    log(f"  ⚠️ 第{idx+1}条异常: {e}")

    if not clips:
        log("❌ 所有片段失败")
        return

    # ── Step 4: 拼接 ─────────────────────────────────────────────
    log(f"\n④ 拼接 {len(clips)} 个片段...")

    final_mp4 = str(OUTPUT_DIR / f"【{CHANNEL_NAME}】{date_str}信息差_{TASK_ID}.mp4")

    inputs = []
    for clip in clips:
        inputs.extend(["-i", clip])

    n = len(clips)
    filter_parts = ''.join([f"[{i}:v][{i}:a]" for i in range(n)])
    filter_complex = f"{filter_parts}concat=n={n}:v=1:a=1[outv][outa]"

    cmd = [
        "ffmpeg", "-y"
    ] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-profile:v", "high", "-level", "3.1",
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        final_mp4
    ]

    r = subprocess.run(cmd, capture_output=True, timeout=600)

    global _CHAPTER_JSON
    _CHAPTER_JSON = None

    if os.path.exists(final_mp4):
        size_mb = os.path.getsize(final_mp4) / 1024 / 1024
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", final_mp4],
            capture_output=True, text=True, timeout=5
        ).stdout.strip() or 0)

        if clip_durations:
            log(f"\n④ 嵌入B站章节...")
            chapters_for_upload = []
            current_ts = 0.0
            for (topic_name, seg_dur) in clip_durations:
                chapters_for_upload.append({
                    "title": topic_name,
                    "start": int(current_ts)
                })
                current_ts += seg_dur

            chapter_json = json.dumps(chapters_for_upload, ensure_ascii=False)
            log(f"  章节数: {len(chapters_for_upload)}")
            for ch in chapters_for_upload:
                log(f"    {ch['start']}s: {ch['title'][:20]}")
            _CHAPTER_JSON = chapter_json

        ok = verify_video_has_frames(final_mp4)
        log(f"\n{'='*60}")
        log(f"{'✅ v9 完成！' if ok else '⚠️ v9 完成（画面待验证）'}")
        log(f"⏱ 耗时: {dur:.0f}秒 ({dur/60:.1f}分钟)")
        log(f"📐 大小: {size_mb:.1f}MB")
        log(f"📁 {final_mp4}")
        log(f"实际片段: {len(clips)}条")
        log(f"{'='*60}")
        return final_mp4, clip_durations
    else:
        log("❌ 拼接失败")
        return None, []

if __name__ == "__main__":
    from datetime import date
    today = date.today()
    date_str = f"{today.year}年{today.month}月{today.day}日"

    log("\n" + "="*60)
    log("📺 v9 主流程开始")
    log("="*60)

    _final_mp4, _clip_durations = main(date_str=date_str)

    if not _final_mp4 or not os.path.exists(_final_mp4):
        log("❌ 视频生成失败，跳过上传")
        sys.exit(1)

    _actual_count = len(_clip_durations)
    log(f"  视频实际包含 {_actual_count} 条新闻")

    global _CHAPTER_JSON
    _chapters_for_upload = json.loads(_CHAPTER_JSON) if _CHAPTER_JSON else []

    log("\n🔍 质量检查中...")
    qc = verify_video_quality(_final_mp4)
    if not qc["pass"]:
        log(f"\n❌ 质量检查未通过，删除劣质视频，不上传")
        log(f"   失败原因: {', '.join(qc['reasons'])}")
        os.remove(_final_mp4)
        sys.exit(1)

    log(f"\n✅ 质量检查通过，视频已生成")
    log(f"   视频路径: {_final_mp4}")
    log(f"   包含 {len(_clip_durations)} 条新闻")
    print(f"\n[V9_GENERATED] {_final_mp4}")

    log("\n🧹 清理临时文件...")
    _task_id_pattern = TASK_ID
    _cleaned = 0
    for _f in OUTPUT_DIR.glob("v9_*"):
        if _f.is_file():
            try:
                _f.unlink()
                _cleaned += 1
            except Exception:
                pass
    log(f"   清理了 {_cleaned} 个临时文件")

    log("\n📤 上传B站...")
    import json as _json, asyncio as _asyncio
    try:
        import bilibili_api as _bapi
        from bilibili_api.clients.HTTPXClient import HTTPXClient as _HTTPXClient
        _bapi.register_client('httpx', _HTTPXClient)
        _bapi.select_client('httpx')
        from bilibili_api import video_uploader, Credential as _Credential
    except ImportError as e:
        log(f"❌ bilibili_api 未安装或导入失败: {e}")
        log("   请运行: pip install bilibili-api-python")
        sys.exit(1)

    _cookies = BILIBILI_COOKIES
    _cred = _Credential(
        sessdata=_cookies['SESSDATA'],
        bili_jct=_cookies['bili_jct'],
        buvid3=_cookies['buvid3'],
    )

    _top_topic = _clip_durations[0][0] if _clip_durations else "今日热点速递"
    _time_mark = "早差" if _dt.now().hour < 12 else "晚差"
    _title = f"【{_time_mark}信息差】{date_str}：{_top_topic}…今日热点速递"

    if check_title_duplicated(_title):
        log(f"\n⚠️ 今日已上传相似标题，跳过上传")
        log(f"   标题: {_title}")
        sys.exit(0)

    _topics_preview = ", ".join([t[0] for t in _clip_durations[:10]])
    _desc = f"""📰 今日信息差日报 | {date_str} | {_actual_count}条热点

{_topics_preview}
…（更多热点见视频）

#信息差 #新闻汇总 #每日热点 #{today.year}"""

    _tags = ["信息差", "新闻汇总", "每日热点", str(today.year)]

    async def _upload():
        _page = video_uploader.VideoUploaderPage(
            path=_final_mp4,
            title=_title,
            description=_desc,
        )
        _cover_path = "/tmp/cover.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-i", _final_mp4, "-ss", "00:00:01", "-vframes", "1", "-q:v", "2", _cover_path],
            capture_output=True, timeout=15
        )
        from bilibili_api import Picture as _Picture
        _cover = _Picture.from_file(_cover_path)

        _meta = video_uploader.VideoMeta(
            tid=201,
            title=_title,
            desc=_desc,
            cover=_cover,
            tags=_tags,
            original=True,
            source='网络',
            no_reprint=True,
            up_close_danmu=False,
            up_close_reply=False,
        )
        _uploader = video_uploader.VideoUploader(
            pages=[_page],
            meta=_meta,
            credential=_cred,
        )
        print(f"\n开始上传 {_final_mp4}...", flush=True)
        _ret = await _uploader.start()
        print(f"上传结果: {_ret}", flush=True)

        _bv = _ret.get('bvid', _ret) if isinstance(_ret, dict) else _ret
        if _bv and _bv.startswith('BV'):
            print(f"设置章节: bvid={_bv}", flush=True)
            try:
                from bilibili_api import bvid2aid
                _aid = bvid2aid(_bv)
                _sess = _get_session()

                for ch in _chapters_for_upload:
                    ch['start'] = int(ch['start'])
                _chapi = f"https://api.bilibili.com/x/vanessa/video/{_aid}/setChapter"
                for ch in _chapters_for_upload:
                    _sess.post(_chapi, data={
                        "aid": _aid,
                        "chapter": json.dumps([ch], ensure_ascii=False),
                        "csrf": _cookies.get('bili_jct', ''),
                    }, timeout=10)
                print(f"章节设置完成", flush=True)
            except Exception as e:
                print(f"章节设置异常: {e}", flush=True)

    _asyncio.run(_upload())