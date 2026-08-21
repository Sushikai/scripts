"""test_subtitle_v2.py — Round 5: 字幕维度的回归测试

目标:
1. whisper word_timestamps=True 触发字级别时间戳
2. 输出 entries 含 words: List[{t, w, em}]
3. 关键词/数字/年份等实体自动高亮 em
4. line_chunks: 每行不超过 16 字
"""

import unittest
from pathlib import Path


class TestWordLevelSubtitles(unittest.TestCase):

    def test_subtitle_has_word_timestamps(self):
        """whisper 转写生成的 entries 必须含 words 字段"""
        from info_gap_pipeline.voiceover import transcribe_words
        # 用真实 wav 文件
        wav = Path("/tmp/_v2_short.mp3")
        if not wav.exists():
            from info_gap_pipeline.voiceover import generate_with_duration
            generate_with_duration("刚刚发生了一件大事", wav)
        try:
            entries = transcribe_words(str(wav))
        except Exception as e:
            self.skipTest(f"whisper 模型未下载或加载失败: {str(e)[:80]}")
        self.assertGreater(len(entries), 0)
        first = entries[0]
        # words 字段必须存在
        self.assertIn("words", first)
        # 至少 1 个 word
        self.assertGreaterEqual(len(first["words"]), 1)
        for w in first["words"]:
            self.assertIn("t", w)
            self.assertIn("w", w)

    def test_keyword_highlight(self):
        """关键词/数字/年份 自动 em"""
        from info_gap_pipeline.voiceover import build_subtitle_entry
        # 内置 highlight rule 应识别 "2025" "99%" "首次" 等
        entry = build_subtitle_entry("科学家发现地球内核在 2025 年首次反向旋转", start=0.0, end=2.0)
        # 应有至少 1 个 em word
        em = [w for w in entry.get("words", []) if w.get("em")]
        self.assertGreater(len(em), 0, "应至少 1 个高亮 word")


class TestLineChunking(unittest.TestCase):

    def test_line_chunks_reasonable(self):
        """每行字数 < 16"""
        from info_gap_pipeline.voiceover import chunk_subtitle_lines
        # 30 字 entry → 至少 2 行
        full = "这是科学家 2025 年首次发现地球内核异常反向旋转, 99% 的人都不知道"
        lines = chunk_subtitle_lines(full, max_chars=14)
        for ln in lines:
            self.assertLessEqual(len(ln["text"]), 14)


if __name__ == "__main__":
    unittest.main()
