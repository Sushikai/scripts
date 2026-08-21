"""test_footage_v2.py — Round 4: 视频素材匹配的回归测试

1. 不允许降级到 cellauto noise 作为兜底视频
2. 段落级 keyword 必须出现在素材标题/描述里 (sub keyword match)
3. 段落时长匹配 (±30%)
"""

import unittest
from pathlib import Path
from unittest.mock import patch


class TestFootageV2NoFallback(unittest.TestCase):
    """Round 4: 必须禁止降级到 noise"""

    def test_no_cellauto_fallback(self):
        """R11: pipeline 已彻底删除 _generate_test_video, ensure_footage 必须返 None"""
        from info_gap_pipeline.download_v2 import (
            ensure_footage,
            has_cellauto_fallback,
        )
        # 默认: 无 URL → 不应再退到 cellauto
        res = ensure_footage(bvid=None, segment_idx=0, output_dir=Path("/tmp/_footage_v2"))
        # 应为 None(没有 noise MP4)
        self.assertIsNone(res, f"不应回退到 noise, 实际 {res}")


class TestKeywordMatch(unittest.TestCase):

    def test_segment_keyword_match(self):
        """段落 keywords 命中素材标题"""
        from info_gap_pipeline.download_v2 import score_keyword_match
        score = score_keyword_match(
            segment={"keywords": ["NASA", "地球内核", "旋转"]},
            material={"title": "NASA 拍摄地球内核变化", "desc": "科学家研究旋转现象"}
        )
        # 3 个关键词,1 个命中 → 33, 2 个 → 66, 3 个 → 100
        self.assertGreaterEqual(score, 33)
        self.assertLessEqual(score, 100)

    def test_no_keyword_match(self):
        """无关键词 → 不报错,返 0"""
        from info_gap_pipeline.download_v2 import score_keyword_match
        score = score_keyword_match(
            segment={"keywords": []},
            material={"title": "Some"}
        )
        self.assertEqual(score, 0)

    def test_pick_best_material(self):
        """多个素材选 keyword 匹配最佳的"""
        from info_gap_pipeline.download_v2 import pick_best_material
        candidates = [
            {"title": "新闻联播今天的内容", "bvid": "BV1"},
            {"title": "NASA 发现地球内核异常", "bvid": "BV2"},
            {"title": "美食探店", "bvid": "BV3"},
        ]
        best = pick_best_material(candidates, segment_keywords=["NASA", "地球内核"])
        self.assertEqual(best["bvid"], "BV2")


class TestDurationMatch(unittest.TestCase):

    def test_duration_in_range(self):
        """duration 在 ±30% 内算合格"""
        from info_gap_pipeline.download_v2 import duration_match_score
        # 5.0s request, 4.0s video → 偏差 25% → 100
        self.assertGreaterEqual(duration_match_score(5.0, 4.0), 80)
        # 5.0s req, 0s → 0 (看是否能正确处理零长)
        self.assertEqual(duration_match_score(5.0, 0), 0)
        # 5.0s req, 5.0s → 100
        self.assertEqual(duration_match_score(5.0, 5.0), 100)
