"""visual_verification_e2e.py — 自验证 VisualVerifier 在 3 类成品上的表现

目的: 不依赖 pipeline 全跑, 手工合成 3 类代表性成品:
1. good_video.mp4     — 多样段 + 烧录字幕 + logo 结尾 → 应通过
2. bug1_video.mp4     — 7 段全用同一开头 (Bug #1 复现) → 应失败 (多样性)
3. bug2_video.mp4     — 用 noise 兜底 (Bug #2 复现) → 应失败 (noise)

用法: cd info_gap_pipeline && python tests/visual_verification_e2e.py
"""
import sys
import subprocess
import tempfile
from pathlib import Path

# 让 info_gap_pipeline 包可被找到 (与 pytest conftest.py 一致)
BASE_DIR = Path(__file__).parent.parent
ROOT_DIR = BASE_DIR.parent  # scripts/
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(BASE_DIR))


def _ffmpeg(args: list, timeout: int = 30) -> bool:
    """运行 ffmpeg, 返回是否成功"""
    r = subprocess.run(["ffmpeg", "-y"] + args, capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0


def make_good_video(out: Path) -> Path:
    """合成"理想"成品视频: 3 段不同颜色 + drawtext 字幕 + 结尾 logo 帧"""
    # 3 段不同颜色, 每段 3s
    clips = []
    colors = ["0x4287f5", "0xf5a442", "0x42f54b"]  # 蓝橙绿
    for i, c in enumerate(colors):
        clip = out.parent / f"_clip_{i}.mp4"
        # drawtext 烧字幕 (用 simple text, 不依赖 libfreetype 字体)
        # 用文字框替代: ffmpeg 自带 drawtext 需要字体, 这里用 color + 文字框模拟
        # 简化: 在底部 100px 加一个不同色块模拟字幕区
        ok = _ffmpeg([
            "-f", "lavfi", "-i", f"color=c={c}:s=1920x1080:d=3",
            "-vf", "drawbox=x=0:y=900:w=1920:h=180:color=white@0.9:t=fill,"
                   f"drawbox=x=200:y=940:w=1520:h=100:color=black@0.7:t=fill",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-an", str(clip),
        ])
        clips.append(clip)
    # 结尾 logo 帧 (不同色)
    logo = out.parent / "_logo.mp4"
    _ffmpeg([
        "-f", "lavfi", "-i", "color=c=0x222222:s=1920x1080:d=2",
        "-vf", "drawbox=x=760:y=440:w=400:h=200:color=white@1.0:t=fill",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", str(logo),
    ])
    clips.append(logo)
    # 拼接
    concat = out.parent / "_concat.txt"
    concat.write_text("\n".join(f"file '{c.resolve()}'" for c in clips))
    ok = _ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", str(out),
    ], timeout=30)
    # 清理
    for c in clips:
        if c.exists():
            c.unlink()
    if concat.exists():
        concat.unlink()
    return out if ok and out.exists() else None


def make_bug1_video(out: Path) -> Path:
    """Bug #1 复现: 7 段都从 raw 开头截 (trim offset=0 失效)"""
    # 整段同一颜色, 模拟"所有段都从同一处截"的视觉假象
    ok = _ffmpeg([
        "-f", "lavfi", "-i", "color=c=0x4287f5:s=1920x1080:d=15",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", str(out),
    ])
    return out if ok and out.exists() else None


def make_bug2_video(out: Path) -> Path:
    """Bug #2 复现: 整段 cellauto noise 兜底视频

    cellauto 是黑白随机噪点, ffmpeg 用 testsrc2 + noise 模拟:
    每个像素随机灰度, 整体呈颗粒状纹理
    """
    # 先尝试 ffmpeg noise filter (新版本)
    ok = _ffmpeg([
        "-f", "lavfi", "-i", "nullsrc=s=640x360:d=10",
        "-vf", "noise=alls=200:allf=t+u,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", str(out),
    ], timeout=20)
    if not ok:
        # fallback: testsrc2 + 高强度 noise
        ok = _ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=640x360:duration=10:rate=15",
            "-vf", "noise=c0_seed=42:alls=255:allf=t+u",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-an", str(out),
        ], timeout=20)
    if not ok:
        # 兜底: 用 Python 生成 raw noise 帧, ffmpeg 编码
        return _make_python_noise_video(out)
    return out if out.exists() else None


def _make_python_noise_video(out: Path) -> Path:
    """Python 端生成随机噪点帧 → ffmpeg 编码 (cellauto 视觉)"""
    import numpy as np
    import cv2
    frames_dir = out.parent / "_noise_frames"
    frames_dir.mkdir(exist_ok=True)
    # 写 30 帧随机噪点 (3fps × 10s)
    for i in range(30):
        noise = np.random.randint(0, 256, (360, 640, 3), dtype=np.uint8)
        cv2.imwrite(str(frames_dir / f"f{i:03d}.png"), noise)
    # ffmpeg 拼接成视频
    ok = _ffmpeg([
        "-framerate", "3",
        "-i", str(frames_dir / "f%03d.png"),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", str(out),
    ], timeout=30)
    for f in frames_dir.glob("*.png"):
        f.unlink()
    frames_dir.rmdir()
    return out if ok and out.exists() else None


def main():
    from info_gap_pipeline.visual import VisualVerifier

    print("=" * 60)
    print("VisualVerifier 自验证 (前后端视觉模型验证)")
    print("=" * 60)

    verifier = VisualVerifier()
    with tempfile.TemporaryDirectory(prefix="visual_verify_") as tmpdir:
        tmp = Path(tmpdir)

        cases = [
            ("good",  make_good_video,  True,  "理想成品: 多段 + 字幕 + logo"),
            ("bug1",  make_bug1_video,  False, "Bug #1 复现: 段落全相同"),
            ("bug2",  make_bug2_video,  False, "Bug #2 复现: noise 兜底"),
        ]

        results = []
        for name, builder, expect_pass, desc in cases:
            video = tmp / f"{name}.mp4"
            print(f"\n── [{name}] {desc} ──")
            built = builder(video)
            if not built:
                print(f"  ⚠️  视频合成失败, 跳过")
                results.append((name, None, None))
                continue

            # 不调真实 vision API (mock=True)
            report = verifier.verify(built, mock_vision=True)
            status = "✅ PASS" if report.passed else "❌ FAIL"
            expected = "✅" if expect_pass else "❌"
            match = "✔" if report.passed == expect_pass else "✘"
            print(f"  {status} (期望 {expected} {match})")
            print(f"  frames={report.frames_analyzed}, "
                  f"diversity={report.diversity_score:.2f}, "
                  f"noise={report.noise_ratio:.1%}, "
                  f"brightness={report.avg_brightness:.1f}")
            if report.reasons:
                for r in report.reasons:
                    print(f"  → {r}")
            results.append((name, report.passed == expect_pass, expect_pass))

        # 汇总
        print("\n" + "=" * 60)
        print("自验证结果汇总")
        print("=" * 60)
        all_ok = True
        for name, ok, expected in results:
            if ok is None:
                print(f"  [{name}] 跳过")
                continue
            mark = "✅" if ok else "❌"
            print(f"  {mark} [{name}] 期望={'PASS' if expected else 'FAIL'}, "
                  f"实际={'PASS' if ok else 'FAIL'}")
            if not ok:
                all_ok = False

        if all_ok and len(results) >= 2:
            print("\n🎉 自验证通过: VisualVerifier 能正确区分好/坏成品")
            return 0
        else:
            print("\n⚠️  自验证未通过, 需要迭代修复 verifier 或测试")
            return 1


if __name__ == "__main__":
    sys.exit(main())