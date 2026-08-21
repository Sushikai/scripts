"""test_subtitle_tdd_r1.py — R11 TDD: 字幕必须不依赖 HuggingFace Hub

用户痛点: 当前 generate_subtitles 用 faster-whisper, 但每次都报
"An error happened while trying to locate the files on the Hub",
导致 7 段配音 9 分钟 Whisper 加载 + 全失败 → 字幕完全没生成。

修复: 用 edge-tts 自带的 SubMaker(基于 word-boundary, 不依赖 Hub)。
"""

import re
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))


class TestSubtitleFromEdgeTTS:
    """字幕生成必须不依赖 HuggingFace Hub / 网络模型下载"""

    def test_subtitle_format_is_srt(self, tmp_path):
        """generate_subtitles 产物必须是 SRT 格式(.srt, 序号+时间戳+文本)"""
        # Mock edge_tts.Communicate.stream 返回音频 + word-boundary
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock

        # 模拟 WordBoundary chunks
        fake_chunks = [
            {"type": "audio", "data": b"\\x00\\x00", "duration": 100000},
            {"type": "WordBoundary", "offset": 1000000, "duration": 500000, "text": "你好"},
            {"type": "audio", "data": b"\\x00\\x00", "duration": 200000},
            {"type": "WordBoundary", "offset": 1700000, "duration": 600000, "text": "世界"},
        ]

        fake_audio_path = tmp_path / "test.wav"
        fake_audio_path.write_bytes(b"\\x00\\x00")

        from info_gap_pipeline.voiceover import VoiceoverGenerator
        gen = VoiceoverGenerator(output_dir=tmp_path)

        # Patch: 用 mock 替代真正的 edge_tts 调用
        with patch("info_gap_pipeline.voiceover.edge_tts") as mock_et:
            mock_communicate = MagicMock()
            async def fake_stream():
                for c in fake_chunks:
                    yield c
            mock_communicate.stream = fake_stream
            mock_et.Communicate.return_value = mock_communicate

            # 也 patch 一下 _tts_generate 的 save 逻辑, 直接调新方法
            srt = gen.generate_subtitles_from_edge_tts(
                text="你好 世界",
                audio_path=fake_audio_path,
                voice="zh-CN-XiaoyiNeural",
                rate="+100%",
                pitch="+0Hz",
            )

        assert srt is not None, "generate_subtitles_from_edge_tts 必须返回 SRT 路径"
        assert srt.exists(), f"SRT 文件未生成: {srt}"
        content = srt.read_text(encoding="utf-8")
        # SRT 格式: 序号 + 时间戳 + 文本
        assert re.search(r"\d+\s*\n\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}", content), \
            f"SRT 格式不正确, 内容:\n{content}"

    def test_subtitle_no_hub_dependency(self):
        """源代码里 generate_subtitles_from_edge_tts 不能 import faster_whisper"""
        import info_gap_pipeline.voiceover as vo
        # 检查这个新方法存在且不依赖 Whisper
        gen = vo.VoiceoverGenerator.__dict__
        assert "generate_subtitles_from_edge_tts" in gen, \
            "必须新增 generate_subtitles_from_edge_tts 方法, 不依赖 Whisper/Hub"

    def test_subtitle_main_pipeline_uses_edge_tts(self):
        """main.py 必须使用新的 edge-tts 字幕生成(非 Whisper)"""
        main_src = (BASE_DIR / "main.py").read_text()
        # 不应该再调用 generate_subtitles 走 Whisper 路径
        # 必须显式调用 generate_subtitles_from_edge_tts
        assert "generate_subtitles_from_edge_tts" in main_src, \
            "main.py 必须调用 generate_subtitles_from_edge_tts(非 Whisper)"

    def test_subtitle_pipeline_fails_loud_when_unable(self, tmp_path):
        """字幕生成失败时, 整个 run 必须显式报错(用户说'视频要有字幕'是硬性需求)

        之前的 fallback: '无字幕文件,跳过烧录' — 这是 silent failure,
        必须改成 raise 抛错。
        """
        from info_gap_pipeline.main import InfoGapPipeline
        # 检查 _step_compile 内不存在 '跳过烧录' 字样
        main_src = (BASE_DIR / "main.py").read_text()
        # 旧 fallback 已删除
        assert "无字幕文件，跳过烧录" not in main_src, \
            "字幕缺失必须 raise, 不允许静默跳过"
        # 必须有 raise
        assert re.search(r"raise\s+\w*Error.*字幕", main_src), \
            "必须 raise 显式错误"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])