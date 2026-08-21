#!/usr/bin/env python3
"""
test_news_sentiment.py
Ship 9 单元测试 — 新闻情绪打分 5 维 (LLM 链路 + 规则兜底)
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.news_sentiment import (
    NewsSentiment, EVENT_TYPES,
    score_by_rule, score_titles, build_prompt,
    normalize_llm_result, aggregate, to_dict_list,
)


class TestScoreByRule(unittest.TestCase):
    """规则链路打分"""

    def test_positive_earnings(self):
        r = score_by_rule("XX股份发布业绩预增公告,净利润同比增长 200%")
        self.assertGreater(r.sentiment, 0.5)
        self.assertEqual(r.event_type, "业绩")
        self.assertEqual(r.source, "rule")

    def test_negative_regulatory(self):
        r = score_by_rule("XX股份因信披违规被证监会立案调查")
        self.assertLess(r.sentiment, -0.5)
        self.assertEqual(r.event_type, "监管")

    def test_delisting_most_negative(self):
        r = score_by_rule("XX股份触及退市指标")
        self.assertLessEqual(r.sentiment, -0.9)

    def test_no_keyword_neutral(self):
        r = score_by_rule("XX股份参加行业交流会议")
        self.assertEqual(r.sentiment, 0.0)
        self.assertEqual(r.event_type, "其他")
        self.assertLess(r.confidence, 0.2)

    def test_empty_title(self):
        r = score_by_rule("")
        self.assertEqual(r.sentiment, 0.0)
        self.assertEqual(r.title, "")

    def test_none_title(self):
        r = score_by_rule(None)
        self.assertEqual(r.sentiment, 0.0)

    def test_mixed_signals_averaged(self):
        """预增(+0.8) + 股东减持(-0.6) → 均值应接近 0, 不该被单边放大"""
        r = score_by_rule("XX股份业绩预增,同时股东减持 2%")
        self.assertLess(abs(r.sentiment), 0.5)

    def test_multi_hit_higher_confidence(self):
        single = score_by_rule("XX股份中标")
        multi = score_by_rule("XX股份中标大额订单,业绩预增")
        self.assertGreater(multi.confidence, single.confidence)

    def test_confidence_capped(self):
        r = score_by_rule("业绩预增 中标 大额订单 回购 股东增持 技术突破 获批 政策支持")
        self.assertLessEqual(r.confidence, 0.9)

    def test_summary_truncated_60(self):
        r = score_by_rule("业绩预增" + "啊" * 200)
        self.assertLessEqual(len(r.summary), 60)


class TestNegator(unittest.TestCase):
    """否定词反转"""

    def test_not_meeting_expectation(self):
        pos = score_by_rule("XX股份业绩超预期")
        neg = score_by_rule("XX股份业绩不及超预期")
        self.assertGreater(pos.sentiment, 0)
        self.assertLess(neg.sentiment, 0)

    def test_failed_bid(self):
        r = score_by_rule("XX股份未中标该项目")
        self.assertLess(r.sentiment, 0)

    def test_negator_out_of_window_no_flip(self):
        """否定词离关键词太远 (>6 字) 不应反转"""
        r = score_by_rule("公司取消了去年的某项无关安排后今日宣布中标")
        self.assertGreater(r.sentiment, 0)


class TestSectorImpact(unittest.TestCase):
    """板块外溢"""

    def test_policy_high_spillover(self):
        r = score_by_rule("国家出台新能源政策支持措施")
        self.assertGreater(abs(r.sector_impact), 0.5)

    def test_personnel_low_spillover(self):
        r = score_by_rule("XX股份高管离职")
        self.assertLess(abs(r.sector_impact), 0.2)

    def test_impact_within_bounds(self):
        for title in ["业绩预增", "退市", "政策支持", "立案"]:
            r = score_by_rule(title)
            self.assertGreaterEqual(r.sector_impact, -1.0)
            self.assertLessEqual(r.sector_impact, 1.0)


class TestBuildPrompt(unittest.TestCase):
    """prompt 构造"""

    def test_returns_system_and_user(self):
        system, user = build_prompt(["标题A", "标题B"])
        self.assertIn("JSON", system)
        self.assertIn("event_type", system)
        self.assertIn("1. 标题A", user)
        self.assertIn("2. 标题B", user)

    def test_empty_list(self):
        system, user = build_prompt([])
        self.assertTrue(system)
        self.assertIsInstance(user, str)


class TestNormalizeLlmResult(unittest.TestCase):
    """LLM 输出归一"""

    def test_valid(self):
        r = normalize_llm_result({
            "sentiment": 0.7, "confidence": 0.8, "event_type": "业绩",
            "sector_impact": 0.3, "summary": "业绩大增",
        }, "原标题")
        self.assertEqual(r.sentiment, 0.7)
        self.assertEqual(r.event_type, "业绩")
        self.assertEqual(r.title, "原标题")
        self.assertEqual(r.source, "llm")

    def test_out_of_range_clamped(self):
        r = normalize_llm_result({"sentiment": 5.0, "confidence": -2.0,
                                  "sector_impact": 99}, "x")
        self.assertEqual(r.sentiment, 1.0)
        self.assertEqual(r.confidence, 0.0)
        self.assertEqual(r.sector_impact, 1.0)

    def test_unknown_event_type_prefix_matched(self):
        """LLM 常返子类 '业绩预增' → 应收敛到 '业绩'"""
        r = normalize_llm_result({"event_type": "业绩预增"}, "x")
        self.assertEqual(r.event_type, "业绩")

    def test_garbage_event_type_falls_back(self):
        r = normalize_llm_result({"event_type": "zzz无此类"}, "x")
        self.assertEqual(r.event_type, "其他")

    def test_string_numbers_coerced(self):
        r = normalize_llm_result({"sentiment": "0.5", "confidence": "0.6"}, "x")
        self.assertEqual(r.sentiment, 0.5)
        self.assertEqual(r.confidence, 0.6)

    def test_non_numeric_defaults_zero(self):
        r = normalize_llm_result({"sentiment": "很好"}, "x")
        self.assertEqual(r.sentiment, 0.0)

    def test_non_dict_falls_back_to_rule(self):
        r = normalize_llm_result(None, "XX股份业绩预增")
        self.assertEqual(r.source, "rule")
        self.assertGreater(r.sentiment, 0)

    def test_missing_summary_uses_title(self):
        r = normalize_llm_result({"sentiment": 0.1}, "标题在此")
        self.assertEqual(r.summary, "标题在此")

    def test_event_types_all_valid(self):
        for et in EVENT_TYPES:
            r = normalize_llm_result({"event_type": et}, "x")
            self.assertEqual(r.event_type, et)


class TestScoreTitles(unittest.TestCase):
    """批量入口 + 降级"""

    def test_empty(self):
        self.assertEqual(score_titles([]), [])

    def test_no_llm_uses_rule(self):
        rs = score_titles(["XX业绩预增", "XX被立案"], use_llm=False)
        self.assertEqual(len(rs), 2)
        self.assertTrue(all(r.source == "rule" for r in rs))

    @patch("tuixue_v3.news_sentiment._score_via_llm")
    def test_llm_success(self, mock_llm):
        mock_llm.return_value = [
            NewsSentiment(title="a", sentiment=0.5, source="llm"),
            NewsSentiment(title="b", sentiment=-0.5, source="llm"),
        ]
        rs = score_titles(["a", "b"])
        self.assertEqual(len(rs), 2)
        self.assertEqual(rs[0].source, "llm")

    @patch("tuixue_v3.news_sentiment._score_via_llm")
    def test_llm_exception_degrades(self, mock_llm):
        mock_llm.side_effect = RuntimeError("no api key")
        rs = score_titles(["XX股份业绩预增"])
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0].source, "rule")
        self.assertGreater(rs[0].sentiment, 0)

    @patch("tuixue_v3.news_sentiment._score_via_llm")
    def test_llm_length_mismatch_degrades(self, mock_llm):
        """LLM 少返一条 → 整批降级, 不能错位对齐"""
        mock_llm.return_value = [NewsSentiment(title="a", source="llm")]
        rs = score_titles(["XX业绩预增", "XX被立案"])
        self.assertEqual(len(rs), 2)
        self.assertTrue(all(r.source == "rule" for r in rs))

    @patch("tuixue_v3.news_sentiment._score_via_llm")
    def test_llm_empty_degrades(self, mock_llm):
        mock_llm.return_value = []
        rs = score_titles(["XX业绩预增"])
        self.assertEqual(rs[0].source, "rule")


class TestAggregate(unittest.TestCase):
    """多条聚合"""

    def test_empty(self):
        a = aggregate([])
        self.assertEqual(a["count"], 0)
        self.assertEqual(a["sentiment"], 0.0)

    def test_confidence_weighted(self):
        """高置信利空不该被低置信噪音稀释"""
        items = [
            NewsSentiment(title="立案", sentiment=-0.9, confidence=0.9, event_type="监管"),
            NewsSentiment(title="论坛", sentiment=0.1, confidence=0.1),
            NewsSentiment(title="论坛", sentiment=0.1, confidence=0.1),
            NewsSentiment(title="论坛", sentiment=0.1, confidence=0.1),
        ]
        a = aggregate(items)
        self.assertLess(a["sentiment"], -0.5)
        self.assertEqual(a["count"], 4)

    def test_top_event(self):
        items = [
            NewsSentiment(title="a", sentiment=0.1, confidence=0.5, event_type="人事"),
            NewsSentiment(title="b", sentiment=-0.9, confidence=0.9, event_type="监管"),
        ]
        self.assertEqual(aggregate(items)["top_event"], "监管")

    def test_zero_confidence_falls_back_to_mean(self):
        items = [
            NewsSentiment(title="a", sentiment=0.4, confidence=0.0),
            NewsSentiment(title="b", sentiment=-0.2, confidence=0.0),
        ]
        a = aggregate(items)
        self.assertAlmostEqual(a["sentiment"], 0.1, places=4)

    def test_bounds(self):
        items = [NewsSentiment(title="x", sentiment=1.0, confidence=1.0)] * 5
        a = aggregate(items)
        self.assertLessEqual(a["sentiment"], 1.0)
        self.assertLessEqual(a["confidence"], 1.0)


class TestToDictList(unittest.TestCase):
    """序列化"""

    def test_serialization(self):
        d = to_dict_list([NewsSentiment(title="t", sentiment=0.5, event_type="业绩")])[0]
        self.assertEqual(d["title"], "t")
        self.assertEqual(d["sentiment"], 0.5)
        self.assertEqual(d["event_type"], "业绩")
        self.assertIn("source", d)

    def test_empty(self):
        self.assertEqual(to_dict_list([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
