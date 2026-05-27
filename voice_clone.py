#!/usr/bin/env python3
"""
VoiceClone — 参考视频音色克隆工具
支持：XTTS v2（Docker）、Bark（本地CPU）、Edge TTS（云端备选）
用法：
    python3 voice_clone.py "待转文字" /tmp/ref_audio.wav output.mp3

依赖：
    - Docker（XTTS，推荐）
    - Bark（本地TTS）
    - Edge TTS（云端备选）
"""

import subprocess, sys, os, argparse, warnings
warnings.filterwarnings('ignore')

# ── 工具检测 ──────────────────────────────────────────────────
def check_docker():
    """检查Docker是否可用"""
    r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    return r.returncode == 0

def check_bark():
    """检查Bark是否可用"""
    try:
        from bark import generate_audio
        return True
    except:
        return False

def check_edge_tts():
    """检查Edge TTS是否可用"""
    try:
        import edge_tts
        return True
    except:
        return False

# ── XTTS via Docker（推荐，质量最好） ──────────────────────────
def clone_xtts(text: str, ref_audio_wav: str, output_path: str) -> bool:
    """
    使用 XTTS v2 通过 Docker 克隆音色
    XTTS 支持参考音频几分钟就能克隆音色
    """
    if not check_docker():
        print("⚠️ Docker不可用，跳过XTTS")
        return False

    work_dir = "/tmp/xtts_clone"
    os.makedirs(work_dir, exist_ok=True)

    # 复制参考音频到工作目录
    ref_copy = f"{work_dir}/reference.wav"
    subprocess.run([
        "ffmpeg", "-i", ref_audio_wav, "-ar", "22050", "-ac", "1",
        "-ss", "0", "-t", "30",  # 取前30秒
        "-y", ref_copy
    ], capture_output=True, timeout=30)

    # 写TTS推理脚本
    tts_script = f"{work_dir}/tts_infer.py"
    with open(tts_script, "w") as f:
        f.write("""
import sys
sys.path.insert(0, "/opt/venv/lib/python3.11/site-packages")

from TTS.api import TTS
import warnings
warnings.filterwarnings('ignore')
import os

tts = TTS("xtts_v2").to("cuda")
tts.tts(
    text=open("/input/text.txt").read().strip(),
    speaker_wav="/input/ref.wav",
    file_path="/output/output.wav"
)
print("XTTS done:", os.path.exists("/output/output.wav"))
""")

    # 写text输入
    with open(f"{work_dir}/text.txt", "w") as f:
        f.write(text)

    # 构建Docker命令
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{work_dir}:/input",
        "-v", f"{work_dir}:/output",
        "ghcr.io/coqui-ai/tts-cpu",
        "python3", tts_script
    ]

    print("🎙️ 使用XTTS克隆音色（首次需要下载模型，首次约需2-3分钟）...")
    try:
        r = subprocess.run(docker_cmd, capture_output=True, timeout=600)
        if r.returncode == 0 and os.path.exists(f"{work_dir}/output.wav"):
            # 转换格式
            subprocess.run([
                "ffmpeg", "-i", f"{work_dir}/output.wav",
                "-ar", "44100", "-ab", "192k", output_path, "-y"
            ], capture_output=True, timeout=30)
            return os.path.exists(output_path)
    except Exception as e:
        print(f"⚠️ XTTS失败: {e}")
    return False


# ── Bark本地TTS（备选，CPU慢，中文支持弱） ───────────────────
def clone_bark(text: str, ref_audio_wav: str, output_path: str) -> bool:
    """使用Bark克隆音色（中文支持有限，仅英文音色克隆推荐）"""
    if not check_bark():
        return False

    from bark import generate_audio, SAMPLE_RATE
    import numpy as np
    import scipy.io.wavfile as wavfile

    # Bark的history_prompt需要特殊格式
    # 重采样到24kHz单声道
    ref_24k = output_path.replace(".mp3", "_bark_prompt.wav")
    r = subprocess.run([
        "ffmpeg", "-i", ref_audio_wav, "-ar", "24000", "-ac", "1",
        "-ss", "0", "-t", "12", "-y", ref_24k
    ], capture_output=True, timeout=30)

    if r.returncode != 0:
        return False

    try:
        audio = generate_audio(
            text,
            history_prompt=ref_24k,
            text_temp=0.7,
            waveform_temp=0.5,
            silent=True
        )
        wavfile.write(output_path.replace(".mp3", "_bark.wav"), SAMPLE_RATE, (audio * 32767).astype(np.int16))
        return os.path.exists(output_path.replace(".mp3", "_bark.wav"))
    except Exception as e:
        print(f"⚠️ Bark失败: {e}")
        return False


# ── Edge TTS（保底，音色可选） ────────────────────────────────
def generate_edge_tts(text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural",
                      rate: str = "+15%", pitch: str = "+0Hz") -> bool:
    """Edge TTS云端语音合成（质量尚可但不是克隆音色）"""
    if not check_edge_tts():
        return False

    import edge_tts, asyncio

    async def _run():
        c = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
        await c.save(output_path)

    try:
        asyncio.run(_run())
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        print(f"⚠️ Edge TTS失败: {e}")
        return False


# ── 主函数 ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="语音克隆工具")
    parser.add_argument("text", help="要转换的文字")
    parser.add_argument("ref_audio", help="参考音频路径（.wav/.m4a）")
    parser.add_argument("output", help="输出路径（.mp3/.wav）")
    parser.add_argument("--method", choices=["xtts", "bark", "edge", "auto"], default="auto",
                        help="使用的TTS方法，auto=自动选择")
    parser.add_argument("--voice", default="zh-CN-XiaoxiaoNeural",
                        help="Edge TTS音色（method=edge时使用）")
    parser.add_argument("--rate", default="+15%",
                        help="Edge TTS语速")
    parser.add_argument("--pitch", default="+0Hz",
                        help="Edge TTS音调")

    args = parser.parse_args()

    print(f"=== 语音克隆工具 ===")
    print(f"文字: {args.text[:50]}...")
    print(f"参考音频: {args.ref_audio}")
    print(f"输出: {args.output}")
    print(f"方法: {args.method}")
    print()

    # 准备参考音频（统一转wav）
    ref_wav = "/tmp/voice_clone_ref_temp.wav"
    subprocess.run([
        "ffmpeg", "-i", args.ref_audio, "-ar", "22050", "-ac", "1", "-y", ref_wav
    ], capture_output=True, timeout=30)

    success = False

    if args.method == "auto":
        # 优先级：XTTS > Bark > Edge TTS
        if check_docker():
            print("尝试XTTS（Docker）...")
            success = clone_xtts(args.text, ref_wav, args.output)
        if not success and check_bark():
            print("尝试Bark（本地）...")
            success = clone_bark(args.text, ref_wav, args.output)
        if not success:
            print("使用Edge TTS（保底）...")
            success = generate_edge_tts(args.text, args.output, args.voice, args.rate, args.pitch)

    elif args.method == "xtts":
        success = clone_xtts(args.text, ref_wav, args.output)
    elif args.method == "bark":
        success = clone_bark(args.text, ref_wav, args.output)
    elif args.method == "edge":
        success = generate_edge_tts(args.text, args.output, args.voice, args.rate, args.pitch)

    if success:
        size = os.path.getsize(args.output)
        print(f"✅ 成功！输出: {args.output} ({size} bytes)")
        return 0
    else:
        print("❌ 失败！")
        return 1


if __name__ == "__main__":
    sys.exit(main())