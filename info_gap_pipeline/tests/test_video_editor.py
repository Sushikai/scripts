"""tests/test_video_editor.py — 视频剪辑模块测试（扩展）"""

import os, sys, subprocess, pytest
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from info_gap_pipeline.edit import VideoEditor
from info_gap_pipeline.edit.subtitle_burn import parse_srt


class TestVideoEditorSubtitles:
    """视频编辑器字幕烧录测试"""

    def _make_video(self, path: Path, duration=5):
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:d={duration}",
            "-f", "lavfi", "-i", "aevalsrc=0",
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac",
            "-shortest", str(path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=20)

    def _make_srt(self, path: Path):
        path.write_text(
            "1\n00:00:00,500 --> 00:00:03,000\n美加墨世界杯倒计时一天\n\n"
            "2\n00:00:03,000 --> 00:00:05,000\n这场比赛预计吸引610万观众\n",
            encoding="utf-8",
        )

    def test_burn_subtitles_returns_path(self, tmp_path):
        video = tmp_path / "input.mp4"
        srt = tmp_path / "test.srt"
        out = tmp_path / "output.mp4"
        self._make_video(video)
        self._make_srt(srt)

        editor = VideoEditor(output_dir=tmp_path)
        result = editor.burn_subtitles(video, srt, out)
        assert result.exists()

    def test_burn_subtitles_preserves_resolution(self, tmp_path):
        video = tmp_path / "input.mp4"
        srt = tmp_path / "test.srt"
        out = tmp_path / "output.mp4"
        self._make_video(video, duration=5)
        self._make_srt(srt)

        editor = VideoEditor(output_dir=tmp_path)
        result = editor.burn_subtitles(video, srt, out)

        info = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(result)],
            capture_output=True, text=True,
        )
        import json
        d = json.loads(info.stdout)
        w = d["streams"][0]["width"]
        h = d["streams"][0]["height"]
        assert w == 1080
        assert h == 1920

    def test_burn_subtitles_missing_srt(self, tmp_path):
        video = tmp_path / "input.mp4"
        missing = tmp_path / "no.srt"
        self._make_video(video)

        editor = VideoEditor(output_dir=tmp_path)
        result = editor.burn_subtitles(video, missing)
        assert result == video  # 无SRT返回原路径


class TestVideoEditorCrop:
    """视频裁剪测试"""

    def test_crop_vertical_from_landscape(self, tmp_path):
        landscape = tmp_path / "landscape.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:d=2",
            "-f", "lavfi", "-i", "aevalsrc=0",
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac",
            "-shortest", str(landscape),
        ]
        subprocess.run(cmd, capture_output=True, timeout=15)

        editor = VideoEditor(output_dir=tmp_path)
        cropped = editor.crop(landscape)
        assert cropped.exists()

        info = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(cropped)],
            capture_output=True, text=True,
        )
        import json
        d = json.loads(info.stdout)
        h = d["streams"][0]["height"]
        assert h == 1920  # 竖屏高度


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])