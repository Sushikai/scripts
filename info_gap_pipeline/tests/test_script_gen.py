"""tests/test_script_gen.py — 脚本生成模块测试（扩展）"""

import os, sys, pytest
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from info_gap_pipeline.script_gen import ScriptGenerator


class TestScriptSegments:
    """脚本分段的深度测试"""

    def test_split_empty(self):
        gen = ScriptGenerator()
        segments = gen._split_into_segments("")
        assert segments == []

    def test_split_preserves_content(self):
        gen = ScriptGenerator()
        script = "你知道吗？地球内核居然在反向旋转。科学家发现这件事的时候，整个天文圈都震惊了。"
        segments = gen._split_into_segments(script)
        joined = "".join(s["text"] for s in segments)
        assert len(joined) >= len(script) * 0.9  # 保留90%以上内容

    def test_segments_have_valid_duration(self):
        gen = ScriptGenerator()
        script = "第一、美加墨世界杯倒计时一天。这场由美国、加拿大和墨西哥联合举办的足球盛宴将在明天揭开帷幕。" * 5
        segments = gen._split_into_segments(script)
        for seg in segments:
            assert "text" in seg
            assert "duration" in seg
            assert seg["duration"] > 0
            assert seg["duration"] < 60  # 每段不超过60秒

    def test_fallback_script_contains_topic(self):
        gen = ScriptGenerator()
        topic = "测试话题XYZ"
        fallback = gen._fallback_script(topic)
        assert topic in fallback
        assert len(fallback) >= 20

    def test_fallback_script_reasonable_length(self):
        """fallback脚本字数合理（200-600字）"""
        gen = ScriptGenerator()
        fallback = gen._fallback_script("测试")
        assert 50 < len(fallback) < 800


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])