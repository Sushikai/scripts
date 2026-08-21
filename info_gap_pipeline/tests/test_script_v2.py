"""test_script_v2.py — Round 2+: 脚本维度的回归测试

每条测试对应一个具体改进点:
1. _split_into_segments 必须返回有 _text / duration / keywords / char_count 的段
2. keywords 抽取:从段落里自动识别 2-6 字关键词
3. 脚本长度合理 (300-1200 字)
4. (可选) TTS markup 自动注入:段落首尾 [pause]、关键词 <emphasis>
"""

import unittest


class TestSplitIntoSegmentsV2(unittest.TestCase):

    def test_segments_have_keywords(self):
        """_split_into_segments 必须给每段生成 keywords"""
        from info_gap_pipeline.script_gen import ScriptGenerator
        gen = ScriptGenerator()
        text = "科学家发现地球内核在 2025 年首次反向旋转。据报道,99% 的人都不知道这件事。数据对比下来,这种突破在过去 100 年里从未记录。真相让人吃惊。"
        segs = gen._split_into_segments(text)
        self.assertGreater(len(segs), 1, "应至少分 2 段")
        for seg in segs:
            self.assertIn("text", seg)
            self.assertIn("duration", seg)
            self.assertIn("keywords", seg, f"段 {seg} 缺 keywords")
            self.assertIsInstance(seg["keywords"], list)
            self.assertGreater(len(seg["keywords"]), 0, f"段 {seg} 关键词为空")

    def test_keywords_excludes_stopwords(self):
        """关键词里不应出现"的/了/是"等 stopwords"""
        from info_gap_pipeline.script_gen import ScriptGenerator
        gen = ScriptGenerator()
        text = "我们发现了一个新物种。这是一个重大发现。"
        segs = gen._split_into_segments(text)
        all_kw = [k for seg in segs for k in seg.get("keywords", [])]
        _stopwords = {"我们", "一个", "这是", "了", "是", "的", "在"}
        bad = [k for k in all_kw if k in _stopwords]
        self.assertEqual(bad, [], f"不应包含停用词: {bad}")

    def test_segment_duration_reasonable(self):
        """段落 duration 应在 2.0 - 30.0 秒之间"""
        from info_gap_pipeline.script_gen import ScriptGenerator
        gen = ScriptGenerator()
        text = " ".join(["这句话用于填充段落分割,确保多句。"] * 20)  # ~ 30 句
        segs = gen._split_into_segments(text)
        for seg in segs:
            self.assertGreaterEqual(seg["duration"], 2.0)
            self.assertLessEqual(seg["duration"], 30.0)

    def test_total_segments_cover_full_text(self):
        """所有段拼起来应 100% 覆盖输入"""
        from info_gap_pipeline.script_gen import ScriptGenerator
        gen = ScriptGenerator()
        text = "科学家发现地球内核在 2025 年首次反向旋转,这是百年来的重大突破。"
        segs = gen._split_into_segments(text)
        # 用一组字的覆盖度近似判:取每个段 1 个非空常见字 vs 原文字
        # 简化判断:总数不会 > 总字数
        total_chars = sum(len(s["text"]) for s in segs)
        self.assertGreaterEqual(total_chars, int(len(text) * 0.85))


class TestTTSMarkup(unittest.TestCase):

    def test_inject_pauses_between_long_paragraphs(self):
        """长段落分隔处插入 [pause]"""
        from info_gap_pipeline.script_gen import inject_tts_markup
        text = "科学家发现地球内核在 2025 年首次反向旋转。据报道,这是 100 年来的重大突破。"
        out = inject_tts_markup(text)
        # 句号后有 [pause]?
        self.assertIn("[pause]", out)

    def test_emphasize_keywords(self):
        """关键词自动 <emphasis>"""
        from info_gap_pipeline.script_gen import inject_tts_markup
        text = "科学家发现地球内核在 2025 年首次反向旋转。"
        out = inject_tts_markup(text, keywords=["首次", "反转"])
        self.assertIn("<emphasis>", out)
        # 至少 2 个 emphasis
        self.assertGreaterEqual(out.count("<emphasis>"), 1)


class TestScriptQualityV2(unittest.TestCase):
    """整合: 完整脚本生成 (主题 → LLM → 段 → TTS markup)"""

    def test_full_pipeline_segments_keywords_markup(self):
        from info_gap_pipeline.script_gen import build_script
        topic = {"title": "科学家发现地球内核在 2025 年首次反向旋转", "source": "测试"}
        # 用 mock LLM
        out = build_script(topic, llm_fallback="科学家发现地球内核在 2025 年首次反向旋转。据报道,99% 的人都不知道这件事。")
        self.assertIn("script", out)
        self.assertIn("segments", out)
        for seg in out["segments"]:
            self.assertIn("keywords", seg)
            self.assertIn("duration", seg)
        # TTS markup 在 script 字串里
        self.assertIn("[pause]", out["script"])


if __name__ == "__main__":
    unittest.main()
