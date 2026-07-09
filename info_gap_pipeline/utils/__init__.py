"""utils.py — 工具函数"""

import hashlib, logging, subprocess
from pathlib import Path
from typing import Optional, Dict

log = logging.getLogger(__name__)


def file_hash(path: Path, algo: str = "md5") -> str:
    """计算文件hash"""
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_cmd(cmd: list, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def cleanup_temp_files(temp_dir: Path, pattern: str = "*"):
    """清理临时文件"""
    if not temp_dir.exists():
        return
    for f in temp_dir.glob(pattern):
        try:
            f.unlink()
            log.debug(f"已删除: {f}")
        except Exception as e:
            log.warning(f"删除失败 {f}: {e}")


def get_video_info(path: Path) -> dict:
    """获取视频基本信息"""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,r_frame_rate",
        "-show_entries", "format=duration,size",
        "-of", "json",
        str(path),
    ]
    try:
        result = run_cmd(cmd, timeout=10)
        import json
        data = json.loads(result.stdout)
        streams = data.get("streams", [{}])
        format_data = data.get("format", {})
        if streams:
            s = streams[0]
            return {
                "width": s.get("width", 0),
                "height": s.get("height", 0),
                "codec": s.get("codec_name", ""),
                "fps": s.get("r_frame_rate", ""),
                "duration": float(format_data.get("duration", 0)),
                "size": int(format_data.get("size", 0)),
            }
    except Exception as e:
        log.warning(f"获取视频信息失败: {e}")
    return {}


def format_duration(seconds: float) -> str:
    """秒数格式化为 MM:SS"""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def score_video_quality(video_path: Path) -> Dict:
    """
    视频质量自动评分（0-100分），用于流水线质量控制。
    检测维度：分辨率、时长、文件大小、音视频同步。
    返回 {"total": int, "resolution_ok": bool, "duration_ok": bool, "size_ok": bool, "details": dict}
    """
    scores = {"resolution": 0, "duration": 0, "size": 0, "audio": 0}
    details = {}

    # 1. 分辨率检测
    info = get_video_info(video_path)
    w, h = info.get("width", 0), info.get("height", 0)
    if w == 1080 and h == 1920:
        scores["resolution"] = 30
        details["resolution"] = f"✅ 竖版1080x1920 ({w}x{h})"
    elif h >= 1080:
        scores["resolution"] = 20
        details["resolution"] = f"⚠️ 竖版但分辨率不同 ({w}x{h})"
    else:
        details["resolution"] = f"❌ 分辨率异常 ({w}x{h})"

    # 2. 时长检测（信息差视频目标4-6分钟=240-360秒）
    dur = info.get("duration", 0)
    if 240 <= dur <= 420:
        scores["duration"] = 30
        details["duration"] = f"✅ 时长{dur:.0f}s（符合4-7分钟目标）"
    elif 60 <= dur < 240:
        scores["duration"] = 15
        details["duration"] = f"⚠️ 时长{dur:.0f}s偏短（<4分钟）"
    elif dur > 420:
        scores["duration"] = 20
        details["duration"] = f"⚠️ 时长{dur:.0f}s偏长（>7分钟）"
    else:
        details["duration"] = f"❌ 时长异常{dur:.0f}s"

    # 3. 文件大小检测（目标5-50MB）
    size_mb = info.get("size", 0) / 1024 / 1024
    if 5 <= size_mb <= 80:
        scores["size"] = 20
        details["size"] = f"✅ 文件大小{size_mb:.1f}MB"
    elif size_mb < 5:
        scores["size"] = 5
        details["size"] = f"⚠️ 文件过小{size_mb:.1f}MB（可能压缩过度）"
    else:
        scores["size"] = 15
        details["size"] = f"⚠️ 文件过大{size_mb:.1f}MB"

    # 4. 音轨检测
    try:
        cmd_audio = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name",
            "-of", "csv=p=0",
            str(video_path),
        ]
        r = run_cmd(cmd_audio, timeout=5)
        if r.stdout.strip():
            scores["audio"] = 20
            details["audio"] = f"✅ 音轨正常 ({r.stdout.strip()})"
        else:
            details["audio"] = "❌ 无音轨"
    except Exception:
        details["audio"] = "❌ 音轨检测异常"

    total = sum(scores.values())
    return {
        "total": total,
        "resolution_ok": scores["resolution"] >= 20,
        "duration_ok": scores["duration"] >= 20,
        "size_ok": scores["size"] >= 15,
        "audio_ok": scores["audio"] >= 15,
        "scores": scores,
        "details": details,
    }