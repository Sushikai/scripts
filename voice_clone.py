#!/usr/bin/env python3
"""
VoiceClone — 本地声音克隆工具 (XTTS v2)
支持：XTTS v2（本地 M4 Mac / NVIDIA GPU）、Edge TTS（云端备选）
用法：
    python3 voice_clone.py "待转文字" /path/to/ref.wav output.wav
    python3 voice_clone.py --ref /path/to/ref.wav --text "文字内容" --output output.wav

M4 Mac 使用 MPS 加速，无需 GPU。
"""

import subprocess, sys, os, argparse, warnings, time
from pathlib import Path

warnings.filterwarnings('ignore')

# ── XTTS 本地推理（支持 M4 Mac MPS） ──────────────────────────
def clone_xtts(text: str, ref_audio_wav: str, output_path: str, gpu: bool = False) -> bool:
    """使用 XTTS v2 本地克隆音色（支持 M4 Mac MPS）"""
    try:
        import torch
        import numpy as np
        from TTS.api import TTS
        import scipy.io.wavfile as wavfile
    except ImportError as e:
        print(f"⚠️ 缺少依赖: {e}")
        return False

    mps_ok = torch.backends.mps.is_available()
    cuda_ok = torch.cuda.is_available()
    device = "cuda" if gpu and cuda_ok else "mps" if mps_ok else "cpu"
    print(f"使用设备: {device}")

    try:
        print(f"加载 XTTS v2 模型...")
        # 强制使用 CPU/MPS，避免 CUDA 检测问题
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
        print("模型加载完成")

        start = time.time()
        wav = tts.tts(text=text, speaker_wav=ref_audio_wav, language="zh")
        elapsed = time.time() - start

        wav = np.array(wav)
        wavfile.write(output_path, 24000, wav)

        duration = len(wav) / 24000
        rtf = elapsed / duration if duration > 0 else 0
        print(f"完成! 时长: {duration:.1f}s, 耗时: {elapsed:.1f}s, 实时率: {rtf:.2f}x")
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        print(f"⚠️ XTTS 失败: {e}")
        return False


# ── Edge TTS（保底，云端） ────────────────────────────────────
def generate_edge_tts(text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural",
                      rate: str = "+15%", pitch: str = "+0Hz") -> bool:
    """Edge TTS 云端语音合成（不是克隆音色，但质量稳定）"""
    try:
        import edge_tts, asyncio
    except ImportError:
        return False

    async def _run():
        c = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
        await c.save(output_path)

    try:
        asyncio.run(_run())
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        print(f"⚠️ Edge TTS 失败: {e}")
        return False


def check_edge_tts():
    try:
        import edge_tts
        return True
    except:
        return False


# ── 主函数 ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="本地声音克隆工具 (XTTS v2)")
    parser.add_argument("--ref", required=True, help="参考音频路径（.wav/.m4a）")
    parser.add_argument("--text", required=True, help="要合成的文字内容")
    parser.add_argument("--output", default="/tmp/cloned_voice.wav", help="输出路径")
    parser.add_argument("--gpu", action="store_true", help="启用 NVIDIA GPU（而非 MPS）")
    parser.add_argument("--method", choices=["xtts", "edge", "auto"], default="auto",
                        help="TTS 方法: xtts=本地克隆, edge=云端备选, auto=自动")
    parser.add_argument("--voice", default="zh-CN-XiaoxiaoNeural",
                        help="Edge TTS 音色（method=edge 时使用）")
    parser.add_argument("--rate", default="+15%", help="Edge TTS 语速")
    parser.add_argument("--pitch", default="+0Hz", help="Edge TTS 音调")

    args = parser.parse_args()

    text = args.text
    ref_audio = args.ref
    output = args.output

    if not text:
        print("错误: 必须提供文字内容 (--text 或位置参数 text)", file=sys.stderr)
        sys.exit(1)
    if not ref_audio:
        print("错误: 必须提供参考音频 (--ref 或位置参数 ref_audio)", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(ref_audio):
        print(f"错误: 参考音频不存在: {ref_audio}", file=sys.stderr)
        sys.exit(1)

    print(f"=== 声音克隆工具 ===")
    print(f"文字: {text[:80]}{'...' if len(text) > 80 else ''}")
    print(f"参考音频: {ref_audio}")
    print(f"输出: {output}")
    print(f"方法: {args.method}")
    print()

    # 统一参考音频为 16kHz 单声道 WAV（XTTS 推荐格式）
    ref_wav = "/tmp/voice_clone_ref_temp.wav"
    print(f"预处理参考音频...")
    r = subprocess.run([
        "ffmpeg", "-i", ref_audio, "-ar", "16000", "-ac", "1",
        "-ss", "0", "-t", "30", "-y", ref_wav
    ], capture_output=True, timeout=30)
    if r.returncode != 0:
        print(f"⚠️ 音频预处理失败: {r.stderr.decode()[:200]}")
        ref_wav = ref_audio

    success = False

    if args.method in ("auto", "xtts"):
        print("使用 XTTS v2 本地克隆...")
        success = clone_xtts(text, ref_wav, output, gpu=args.gpu)

    if not success and args.method in ("auto", "edge"):
        print("使用 Edge TTS（云端保底）...")
        success = generate_edge_tts(text, output, args.voice, args.rate, args.pitch)

    if success:
        size = os.path.getsize(output)
        print(f"✅ 成功! 输出: {output} ({size/1024:.0f} KB)")
        return 0
    else:
        print("❌ 失败!")
        return 1


if __name__ == "__main__":
    sys.exit(main())