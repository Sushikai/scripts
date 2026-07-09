"""
Voice API - 声音克隆 + 文字转语音 接口服务
基于 edge-tts（基础TTS）+ XTTS（声音克隆）架构
"""
import os
import sys
import asyncio
import uuid
import json
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# ──────────────────────────────────────────
# 配置
# ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
MODEL_DIR = BASE_DIR / "models"
CONFIG_FILE = BASE_DIR / "config.json"

OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Voice API", version="1.0.0", description="声音克隆+TTS接口")

# 加载配置
def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {
        "default_voice": "zh-CN-XiaoxiaoNeural",
        "default_rate": "+0%",
        "default_volume": "+0%",
        "default_pitch": "+0Hz"
    }

config = load_config()

# ──────────────────────────────────────────
# 请求模型
# ──────────────────────────────────────────
class TTSRequest(BaseModel):
    text: str
    voice: str = "zh-CN-XiaoxiaoNeural"   # edge-tts 音色
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"
    output_file: Optional[str] = None

class CloneRequest(BaseModel):
    text: str
    reference_audio_path: str   # 参考音频路径
    language: str = "zh"       # 语言代码：zh/en/ja/ko
    output_file: Optional[str] = None

# ──────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────
def get_output_path(prefix: str, ext: str = "mp3") -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{prefix}_{ts}_{uuid.uuid4().hex[:6]}.{ext}"
    return OUTPUT_DIR / name

def cleanup_temp(path: Path):
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass

# ──────────────────────────────────────────
# Edge-TTS 文字转语音
# ──────────────────────────────────────────
EDGE_VOICES = {
    # 中文
    "zh-CN-XiaoxiaoNeural": "晓晓",
    "zh-CN-YunxiNeural": "云希",
    "zh-CN-YunxiaNeural": "云夏",
    "zh-CN-YunyangNeural": "云扬",
    "zh-CN-liaoning-XiaobearNeural": "小北辽宁",
    "zh-CN-shaanxi-XiaoyaoNeural": "小雅陕西",
    # 英文
    "en-US-AriaNeural": "Aria",
    "en-US-JennyNeural": "Jenny",
    "en-US-GuyNeural": "Guy",
    "en-GB-SoniaNeural": "Sonia",
    # 日文
    "ja-JP-NanamiNeural": "Nanami",
    "ja-JP-KeitaNeural": "Keita",
    # 韩文
    "ko-KR-SunhiNeural": "Sunhi",
}

async def tts_edge(text: str, voice: str, rate: str, volume: str, pitch: str, output_path: Path) -> dict:
    """调用 edge-tts 将文字转语音"""
    voice = voice if voice in EDGE_VOICES else "zh-CN-XiaoxiaoNeural"
    cmd = [
        "edge-tts",
        "--text", text,
        "--voice", voice,
        "--rate", rate,
        "--volume", volume,
        "--pitch", pitch,
        "--write-media", str(output_path)
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"edge-tts failed: {stderr.decode()}")
    return {
        "path": str(output_path),
        "voice": voice,
        "size_bytes": output_path.stat().st_size
    }

# ──────────────────────────────────────────
# XTTS 声音克隆（预留）
# ──────────────────────────────────────────
async def tts_clone(text: str, reference_audio: Path, output_path: Path, language: str = "zh") -> dict:
    """调用 XTTS v2 声音克隆 TTS（Coqui/TTS）"""
    import os
    os.environ["COQUI_TOS_AGREED"] = "1"
    lang_arg = repr(language)  # escape for f-string

    cmd = [
        sys.executable, "-c",
        f"""
import sys, os, time
os.environ["COQUI_TOS_AGREED"] = "1"
sys.path.insert(0, '{BASE_DIR}')
from TTS.api import TTS

print("加载XTTS v2...")
t0 = time.time()
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
print(f"加载: {{time.time()-t0:.1f}}s")

print("开始克隆合成...")
t0 = time.time()
tts.tts_to_file(
    text={repr(text)},
    speaker_wav={str(reference_audio)!r},
    file_path={str(output_path)!r},
    language={lang_arg}
)
print(f"合成完成: {{time.time()-t0:.1f}}s")
"""
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    out = stdout.decode()
    err = stderr.decode()
    if proc.returncode != 0:
        raise RuntimeError(f"XTTS clone failed:\n{err[-500:]}")
    if not output_path.exists():
        raise RuntimeError(f"XTTS output not created:\n{out[-300:]}")
    return {
        "path": str(output_path),
        "size_bytes": output_path.stat().st_size
    }

# ──────────────────────────────────────────
# API 路由
# ──────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service": "Voice API",
        "version": "1.0.0",
        "endpoints": ["/tts", "/clone", "/clone_upload", "/voices", "/health"]
    }

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}

@app.get("/voices")
async def list_voices():
    """列出所有可用音色"""
    return {"voices": EDGE_VOICES, "count": len(EDGE_VOICES)}

@app.post("/tts")
async def api_tts(req: TTSRequest):
    """文字转语音（edge-tts）"""
    if not req.text.strip():
        raise HTTPException(400, "text cannot be empty")
    if len(req.text) > 5000:
        raise HTTPException(400, "text too long (max 5000 chars)")
    
    output_file = req.output_file or get_output_path("tts", "mp3")
    output_path = OUTPUT_DIR / output_file if req.output_file else get_output_path("tts", "mp3")
    
    try:
        result = await tts_edge(req.text, req.voice, req.rate, req.volume, req.pitch, output_path)
        return JSONResponse({
            "success": True,
            "output_path": result["path"],
            "voice": result["voice"],
            "text_len": len(req.text),
            "download_url": f"/download/{output_path.name}"
        })
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/clone")
async def api_clone(req: CloneRequest):
    """声音克隆（XTTS）— 已有参考音频文件"""
    if not Path(req.reference_audio_path).exists():
        raise HTTPException(400, f"reference audio not found: {req.reference_audio_path}")
    if not req.text.strip():
        raise HTTPException(400, "text cannot be empty")
    
    output_path = get_output_path("clone", "wav")
    try:
        result = await tts_clone(req.text, Path(req.reference_audio_path), output_path, req.language)
        return JSONResponse({
            "success": True,
            "output_path": result["path"],
            "language": req.language,
            "download_url": f"/download/{output_path.name}"
        })
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/clone_upload")
async def api_clone_upload(
    text: str = Form(...),
    language: str = Form("zh"),
    file: UploadFile = File(...)
):
    """上传参考音频 + 文字 → 声音克隆合成"""
    if not text.strip():
        raise HTTPException(400, "text cannot be empty")
    
    # 保存上传的参考音频
    suffix = Path(file.filename).suffix or ".wav"
    ref_path = TEMP_DIR / f"ref_{uuid.uuid4().hex[:8]}{suffix}"
    ref_path.write_bytes(await file.read())
    
    output_path = get_output_path("clone", "wav")
    try:
        result = await tts_clone(text, ref_path, output_path, language)
        return JSONResponse({
            "success": True,
            "output_path": result["path"],
            "download_url": f"/download/{output_path.name}"
        })
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        cleanup_temp(ref_path)

@app.get("/download/{filename}")
async def download(filename: str):
    """下载生成的音频文件"""
    safe_name = filename.replace("..", "").replace("/", "")
    file_path = OUTPUT_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path=file_path, filename=safe_name, media_type="audio/mpeg")

# ──────────────────────────────────────────
# 启动
# ──────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Voice API 启动中...")
    print(f"   输出目录: {OUTPUT_DIR}")
    print(f"   可用音色: {len(EDGE_VOICES)} 种")
    uvicorn.run(app, host="0.0.0.0", port=8899, log_level="info")
