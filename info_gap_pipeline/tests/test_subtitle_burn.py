"""tests/test_subtitle_burn.py — 字幕烧录模块测试"""

import os, sys, subprocess, pytest
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from info_gap_pipeline.edit.subtitle_burn import parse_srt, render_subtitle_png, burn_subtitles


class TestParseSRT:
    """SRT解析测试"""

    def test_parse_valid_srt(self, tmp_path):
        srt = tmp_path / "test.srt"
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:03,500\n第一、美加墨世界杯倒计时一天\n\n"
            "2\n00:00:03,500 --> 00:00:06,200\n这场比赛预计吸引610万观众\n",
            encoding="utf-8",
        )
        subs = parse_srt(srt)
        assert len(subs) == 2
        assert subs[0][0] == 0.0       # start
        assert subs[0][1] == 3.5        # end
        assert "美加墨世界杯" in subs[0][2]
        assert subs[1][0] == 3.5
        assert subs[1][1] == 6.2

    def test_parse_empty(self, tmp_path):
        srt = tmp_path / "empty.srt"
        srt.write_text("", encoding="utf-8")
        assert parse_srt(srt) == []

    def test_parse_malformed(self, tmp_path):
        srt = tmp_path / "bad.srt"
        srt.write_text("这不是SRT格式\n", encoding="utf-8")
        assert parse_srt(srt) == []


class TestRenderSubtitlePNG:
    """字幕PNG渲染测试"""

    def test_render_creates_file(self, tmp_path):
        out = tmp_path / "sub.png"
        render_subtitle_png("测试字幕文字", out)
        assert out.exists()
        assert out.stat().st_size > 1000  # PNG应 > 1KB

    def test_render_long_text(self, tmp_path):
        out = tmp_path / "long.png"
        long_text = "这是一段很长的字幕文字，用于测试渲染是否正常工作" * 2
        render_subtitle_png(long_text, out)
        assert out.exists()

    def test_render_special_chars(self, tmp_path):
        out = tmp_path / "special.png"
        render_subtitle_png("数字: 99% 美元: $100万", out)
        assert out.exists()


class TestBurnSubtitles:
    """字幕烧录集成测试"""

    def _make_test_video(self, path: Path, duration=5):
        """创建测试竖屏视频"""
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:d={duration}",
            "-f", "lavfi", "-i", "aevalsrc=0",
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac",
            "-shortest", str(path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=20)

    def _make_test_srt(self, path: Path):
        path.write_text(
            "1\n00:00:00,500 --> 00:00:03,000\n美加墨世界杯倒计时一天\n\n"
            "2\n00:00:03,000 --> 00:00:05,000\n这场比赛预计吸引610万观众\n",
            encoding="utf-8",
        )

    def test_burn_subtitles_creates_output(self, tmp_path):
        video = tmp_path / "input.mp4"
        srt = tmp_path / "test.srt"
        out = tmp_path / "output.mp4"
        self._make_test_video(video, duration=5)
        self._make_test_srt(srt)

        result = burn_subtitles(video, srt, out)
        assert result.exists()
        assert result.stat().st_size > video.stat().st_size  # 字幕PNG叠加后更大

    def test_burn_subtitles_duration(self, tmp_path):
        video = tmp_path / "input.mp4"
        srt = tmp_path / "test.srt"
        out = tmp_path / "output.mp4"
        self._make_test_video(video, duration=5)
        self._make_test_srt(srt)

        result = burn_subtitles(video, srt, out)
        info = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(result)],
            capture_output=True, text=True,
        )
        import json
        d = json.loads(info.stdout)
        dur = float(d["format"]["duration"])
        assert 4 < dur < 7  # 应接近5秒（最后一条字幕结束时间 + buffer）

    def test_burn_subtitles_no_srt_file(self, tmp_path):
        video = tmp_path / "input.mp4"
        missing = tmp_path / "missing.srt"
        out = tmp_path / "output.mp4"
        self._make_test_video(video)

        result = burn_subtitles(video, missing, out)
        # 无SRT应返回原视频路径
        assert result == video


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])