"""
subtitle_burn.py — 用PIL生成字幕PNG + ffmpeg overlay叠加烧录字幕
方案：每个字幕片段生成一张透明PNG，用ffmpeg overlay在正确时间段叠加到视频
"""
import subprocess, re, os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def parse_srt(srt_path: Path):
    """解析SRT文件，返回 [(start_s, end_s, text), ...]"""
    text = srt_path.read_text(encoding="utf-8")
    blocks = text.strip().split("\n\n")
    subs = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        m = re.match(
            r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})",
            lines[1],
        )
        if not m:
            continue
        h1, mi1, s1, ms1, h2, mi2, s2, ms2 = m.groups()
        start = int(h1) * 3600 + int(mi1) * 60 + int(s1) + int(ms1) / 1000
        end = int(h2) * 3600 + int(mi2) * 60 + int(s2) + int(ms2) / 1000
        subs.append((start, end, " ".join(lines[2:])))
    return subs


def render_subtitle_png(text: str, output_path: Path, W=1920, H=1080, font_size=72):
    """用PIL把字幕文字渲染到透明PNG（黑色描边+黄色粗体字，底部居中）"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # 使用黑体粗体
    font_path = "/System/Library/Fonts/STHeiti Bold.ttc"
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        try:
            font_path = "/System/Library/Fonts/Helvetica.ttc"
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = ImageFont.load_default()

    # 计算居中位置（底部居中）
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (W - tw) // 2
    y = H - th - 30

    # 黑色描边（加粗边缘）
    for sx in [-5, -3, -1, 1, 3, 5]:
        for sy in [-5, -3, -1, 1, 3, 5]:
            if sx == 0 and sy == 0:
                continue
            draw.text((x + sx, y + sy), text, font=font, fill=(0, 0, 0, 220))
    # 黄色粗体文字
    draw.text((x, y), text, font=font, fill=(255, 255, 0, 255))
    img.save(output_path, "PNG")


def burn_subtitles(video_path: Path, srt_path: Path, output_path: Path) -> Path:
    """
    主函数：将SRT字幕烧录到视频。
    每个字幕段生成PNG，用ffmpeg overlay在对应时间段叠加。
    使用filter_complex显式流引用叠加字幕PNG，音频透传保留原音。
    """
    import tempfile

    video_path = Path(video_path)
    srt_path = Path(srt_path)
    output_path = output_path or video_path.with_name(f"{video_path.stem}_sub.mp4")

    if not srt_path.exists():
        print("警告: SRT文件不存在")
        return video_path
    subs = parse_srt(srt_path)
    if not subs:
        print("警告: SRT无有效字幕")
        return video_path

    tmpdir = Path(tempfile.mkdtemp())
    tmpdir.joinpath(".gitkeep").touch()

    # 渲染每条字幕为PNG
    png_paths = []
    for i, (start, end, text) in enumerate(subs):
        png_path = tmpdir / f"sub_{i:04d}.png"
        render_subtitle_png(text[:80], png_path)
        png_paths.append((start, end, png_path))

    print(f"渲染了 {len(png_paths)} 张字幕PNG")

    # 构建ffmpeg命令：用filter_complex overlay逐段叠加字幕PNG
    # 每个字幕PNG在对应时间段内叠加到底部，使用显式流引用确保音频保留
    inputs = ["-i", str(video_path)]
    for i, (_, _, png) in enumerate(png_paths):
        inputs.extend(["-i", str(png)])

    # 链式overlay：逐条叠加字幕PNG，末尾添加音频透传
    # 字幕1: [0:v][1:v]overlay -> [s0]
    # 字幕2: [s0][2:v]overlay -> [s1]
    # ...
    # 最后: [0:a]anull -> [outa]
    links = []
    for i, (start, end, _) in enumerate(png_paths):
        png_input_idx = i + 1  # PNG输入索引（从1开始，视频是0）
        is_last = (i == len(png_paths) - 1)
        this_out = "[outv]" if is_last else f"[s{i}]"
        main_in = "[0:v]" if i == 0 else f"[s{i-1}]"
        links.append(
            f"{main_in}[{png_input_idx}:v]overlay=x=0:y=H-overlay_h:"
            f"enable='between(t,{start:.3f},{end:.3f})'{this_out}"
        )
    # 添加音频透传（从原始视频提取音频，原样传递）
    links.append("[0:a]anull[outa]")
    chain = ";".join(links)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", chain,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(output_path),
    ]

    print("执行:", " ".join(cmd[:20]), "...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode == 0:
        print(f"成功: {output_path}")
        return output_path
    else:
        print(f"失败: {result.stderr[-500:]}")
        return video_path


if __name__ == "__main__":
    import sys
    video = Path(sys.argv[1])
    srt = Path(sys.argv[2])
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    burn_subtitles(video, srt, out)
