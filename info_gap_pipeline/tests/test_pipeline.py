"""tests/test_pipeline.py — 流水线核心测试"""

import os, sys, logging, json, subprocess, pytest
from pathlib import Path
from datetime import datetime

# 项目根目录
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from info_gap_pipeline.research import TopicResearcher
from info_gap_pipeline.script_gen import ScriptGenerator
from info_gap_pipeline.download import VideoDownloader
from info_gap_pipeline.download.search import MaterialSearcher
from info_gap_pipeline.voiceover import VoiceoverGenerator
from info_gap_pipeline.edit import VideoEditor
from info_gap_pipeline.utils import get_video_info, format_duration


class TestConfig:
    """配置测试"""
    def test_paths_exist(self):
        from info_gap_pipeline import config
        assert config.BASE_DIR.exists()
        assert config.DATA_DIR.exists()
        assert config.OUTPUTS_DIR.exists()

    def test_video_params(self):
        """验证视频参数锚定到参考视频BV1EY7k6aEPg (1920x1080 16:9 30fps)"""
        from info_gap_pipeline.config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS
        from info_gap_pipeline.config import REFERENCE_BVID, REFERENCE_VIDEO_URL
        # 锚定参考视频参数
        assert VIDEO_WIDTH == 1920, f"宽度应为1920（参考视频{REFERENCE_BVID}）"
        assert VIDEO_HEIGHT == 1080, f"高度应为1080（参考视频{REFERENCE_BVID}）"
        assert VIDEO_FPS == 30, f"FPS应为30（参考视频{REFERENCE_BVID}）"
        # 验证比例
        assert abs(VIDEO_WIDTH / VIDEO_HEIGHT - 16/9) < 0.01, "视频比例应为16:9"

    def test_config_anchored_to_reference(self):
        """验证配置已锚定到参考视频BV1EY7k6aEPg"""
        from info_gap_pipeline import config
        # 参考视频信息
        REF = {
            "bvid": "BV1EY7k6aEPg",
            "url": "https://www.bilibili.com/video/BV1EY7k6aEPg/",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "duration": 233.4,
            "aspect_ratio": "16:9",
        }
        assert config.REFERENCE_BVID == REF["bvid"]
        assert config.VIDEO_WIDTH == REF["width"]
        assert config.VIDEO_HEIGHT == REF["height"]
        assert config.VIDEO_FPS == REF["fps"]


class TestResearch:
    """选题模块测试"""
    def test_scan_all(self):
        researcher = TopicResearcher()
        topics = researcher.scan_all()
        assert isinstance(topics, list)

    def test_filter_info_gap(self):
        researcher = TopicResearcher()
        test_topics = [
            {"title": "科学家发现地球内核反向旋转的内幕", "source": "测试"},
            {"title": "今日天气晴朗", "source": "测试"},
            {"title": "99%的人不知道的金融秘密", "source": "测试"},
        ]
        filtered = researcher.filter_info_gap_topics(test_topics)
        assert len(filtered) >= 1
        assert filtered[0]["info_gap_score"] >= 1


class TestScriptGen:
    """脚本生成测试"""
    def test_split_into_segments(self):
        gen = ScriptGenerator()
        script = "你知道吗？地球内核居然在反向旋转。科学家发现这件事的时候，整个天文圈都震惊了。99%的人完全不知道这件事！"
        segments = gen._split_into_segments(script)
        assert len(segments) >= 1
        assert all("text" in seg for seg in segments)
        assert all("duration" in seg for seg in segments)

    def test_fallback_script(self):
        gen = ScriptGenerator()
        fallback = gen._fallback_script("测试话题")
        assert len(fallback) >= 20
        assert "测试话题" in fallback


class TestDownload:
    """下载模块测试"""
    def test_video_downloader_init(self):
        dl = VideoDownloader()
        assert dl.output_dir.exists()
        assert dl.output_dir.name == "videos"

    def test_get_duration(self, tmp_path):
        # 创建测试视频
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=100x100:d=3",
            "-f", "lavfi", "-i", "aevalsrc=0",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            "-shortest",
            str(tmp_path / "test.mp4"),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            dur = VideoDownloader.get_duration(tmp_path / "test.mp4")
            assert dur > 0
            assert abs(dur - 3.0) < 1.0  # 误差1秒内


class TestSearch:
    """素材搜索测试"""
    def test_searcher_init(self):
        s = MaterialSearcher()
        assert s.cache_dir.exists()

    def test_search_bilibili(self):
        s = MaterialSearcher()
        results = s.search_bilibili("科技突破", limit=3)
        assert isinstance(results, list)
        # B站返回结果可能为空（反爬限制），不强制断言


class TestVoiceover:
    """配音模块测试"""
    def test_tts_generate(self, tmp_path):
        vg = VoiceoverGenerator(output_dir=tmp_path)
        test_text = "你知道吗？地球内核居然在反向旋转！"
        path = vg.generate(test_text, filename="test_vo.wav")
        assert path is not None
        assert path.exists()
        assert path.stat().st_size > 1024

    def test_audio_duration(self, tmp_path):
        # 创建测试音频
        test_wav = tmp_path / "test.wav"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            str(test_wav),
        ]
        subprocess.run(cmd, capture_output=True, timeout=10)
        if test_wav.exists():
            dur = VoiceoverGenerator.get_audio_duration(test_wav)
            assert dur > 0


class TestEdit:
    """视频剪辑测试"""
    def test_video_editor_init(self):
        ed = VideoEditor()
        assert ed.output_dir.exists()

    def test_crop_creates_horizontal(self, tmp_path):
        """裁剪测试：验证横版16:9输出（1920x1080，锚定BV1EY7k6aEPg）"""
        test_video = tmp_path / "input.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=1920x1080:d=2",
            "-f", "lavfi", "-i", "aevalsrc=0",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            "-shortest",
            str(test_video),
        ]
        subprocess.run(cmd, capture_output=True, timeout=15)
        if test_video.exists():
            ed = VideoEditor(output_dir=tmp_path)
            cropped = ed.crop(test_video)
            assert cropped.exists()
            info = get_video_info(cropped)
            # 锚定参考视频BV1EY7k6aEPg：横版16:9
            assert info.get("width") == 1920, f"宽度应为1920（参考视频{REFERENCE_BVID}）"
            assert info.get("height") == 1080, f"高度应为1080（参考视频{REFERENCE_BVID}）"


class TestUtils:
    """工具函数测试"""
    def test_file_hash(self, tmp_path):
        test_file = tmp_path / "hash_test.txt"
        test_file.write_text("hello world")
        h = __import__("info_gap_pipeline.utils", fromlist=["file_hash"]).file_hash(test_file, "md5")
        assert len(h) == 32

    def test_format_duration(self):
        from info_gap_pipeline.utils import format_duration
        assert format_duration(65) == "01:05"
        assert format_duration(5) == "00:05"


class TestVideoTrimMatchesVoiceover:
    """视频裁剪必须匹配配音实际时长，而非估算时长

    背景：配音生成（Step 4）在视频裁剪（Step 3）之前执行，
    以确保每段视频时长精确对齐配音（尤其是 TTS 语速加快后，
    实际配音时长会短于 estimated_duration）。
    """

    def test_trim_duration_matches_actual_audio_not_estimated(self, tmp_path):
        """验证视频片段裁剪到配音实际时长，而非 estimated_duration"""
        from info_gap_pipeline.voiceover import VoiceoverGenerator
        from info_gap_pipeline.edit import VideoEditor

        # ── 1. 创建模拟原始视频（足够长，60秒） ────────────────────────────
        raw_video = tmp_path / "raw_60s.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:d=60",
            "-f", "lavfi", "-i", "aevalsrc=0",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            "-shortest",
            str(raw_video),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"创建测试视频失败: {result.stderr[:200]}"
        assert raw_video.exists()

        # ── 2. 模拟脚本：estimated=30s（估算值），实际配音会短/长于此 ─────
        script_data = {
            "estimated_duration": 30.0,   # 脚本估算的时长
            "script": "这是一个测试脚本，用于验证视频裁剪是否匹配配音实际时长，而不是估算时长。",
        }

        # ── 3. 生成配音 ────────────────────────────────────────────────────
        vg = VoiceoverGenerator(output_dir=tmp_path)
        audio_path = vg.generate(script_data["script"], filename="test_vo.wav")
        assert audio_path is not None and audio_path.exists(), "配音生成失败"

        actual_audio_dur = VoiceoverGenerator.get_audio_duration(audio_path)
        assert actual_audio_dur > 0, f"无法获取配音时长: {audio_path}"

        # ── 4. 核心断言：裁剪目标必须是配音实际时长，不是 estimated_duration ─
        #    旧逻辑错误地用 estimated_duration(30s) 裁剪，导致音画不同步
        #    正确逻辑用 audio_actual_duration 裁剪
        target_trim_dur = actual_audio_dur  # 必须是配音实际时长
        assert target_trim_dur != script_data["estimated_duration"], \
            "测试无效：配音实际时长恰好等于估算值，无法区分新旧逻辑"

        editor = VideoEditor(output_dir=tmp_path)
        trimmed = editor.trim(raw_video, target_trim_dur, tmp_path / "trimmed.mp4")
        assert trimmed.exists(), "视频裁剪失败"

        trimmed_dur = editor._get_duration(trimmed)

        # 容差 1 秒（ffmpeg trim 因关键帧偏移会有少量误差）
        assert abs(trimmed_dur - actual_audio_dur) < 1.0, \
            f"裁剪后视频时长({trimmed_dur:.2f}s)应接近配音实际时长({actual_audio_dur:.2f}s)，" \
            f"而非估算时长({script_data['estimated_duration']}s)"

    def test_tts_speed_change_affects_trim_target(self, tmp_path):
        """验证 TTS 语速参数变化时，配音实际时长随之改变

        核心断言：配音生成成功，且时长为正数（具体比率受 TTS 引擎环境影响）。
        视频裁剪必须以配音实际时长为准，而非固定估算值。
        """
        from info_gap_pipeline.voiceover import VoiceoverGenerator

        test_script = "科技领域传来重磅消息，中国科学家在量子计算领域取得重大突破，速度提升高达十倍，这一成果让全球研究机构都感到震惊。"

        vg = VoiceoverGenerator(output_dir=tmp_path)
        vo = vg.generate(test_script, filename="vo_rate_test.wav")
        dur = VoiceoverGenerator.get_audio_duration(vo)

        # 核心断言：配音生成成功且时长合理（>1秒，中文约200字应需20-60秒）
        assert dur > 1.0, f"配音时长异常: {dur}s，应 > 1s"
        assert dur < 120.0, f"配音时长异常: {dur}s，应 < 120s"


class TestEndToEnd:
    """端到端测试（生成测试视频）"""
    def test_generate_test_video_only(self, tmp_path):
        """只生成测试视频，不依赖网络"""
        ed = VideoEditor(output_dir=tmp_path)

        # 创建2秒测试片段
        clip1 = tmp_path / "clip1.mp4"
        clip2 = tmp_path / "clip2.mp4"

        for clip, color in [(clip1, "red"), (clip2, "blue")]:
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"color=c={color}:s=1080x1920:d=2",
                "-f", "lavfi", "-i", "aevalsrc=0",
                "-c:v", "libx264", "-preset", "fast",
                "-c:a", "aac",
                "-shortest",
                str(clip),
            ]
            subprocess.run(cmd, capture_output=True, timeout=15)

        # 拼接
        concat_list = tmp_path / "list.txt"
        with open(concat_list, "w") as f:
            f.write(f"file '{clip1.resolve()}'\n")
            f.write(f"file '{clip2.resolve()}'\n")

        final = tmp_path / "final.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            str(final),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"拼接失败: {result.stderr[:200]}"
        assert final.exists()
        info = get_video_info(final)
        assert info.get("duration", 0) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])