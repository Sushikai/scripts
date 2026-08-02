#!/usr/bin/env python3
"""
test_factor_pipeline.py
Ship 11 单元测试 — 多因子融合 pipeline (5 类 → 综合)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_pipeline import (
    FactorScore, composite_score, build_from_components, build_minimal,
    rank_scores, explain, to_dict_list,
    _score_sector_rotation, _score_event, _score_sentiment,
    _score_momentum, _score_volatility, _saturate, _clamp,
    _DEFAULT_WEIGHTS,
)


class TestClamp(unittest.TestCase):
    def test_within(self):
        self.assertEqual(_clamp(0.5), 0.5)
    def test_above(self):
        self.assertEqual(_clamp(5.0), 1.0)
    def test_below(self):
        self.assertEqual(_clamp(-5.0), -1.0)


class TestSaturate(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(_saturate(0, 100), 0.0)
    def test_monotonic_positive(self):
        a = _saturate(100, 1000)
        b = _saturate(500, 1000)
        c = _saturate(5000, 1000)
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertLess(c, 1.0)
    def test_negative(self):
        self.assertLess(_saturate(-5000, 1000), -0.7)
    def test_zero_scale(self):
        self.assertEqual(_saturate(50, 0), 1.0)


class TestScoreSectorRotation(unittest.TestCase):
    def test_passthrough(self):
        self.assertEqual(_score_sector_rotation(0.5), 0.5)
        self.assertEqual(_score_sector_rotation(-0.7), -0.7)
        self.assertEqual(_score_sector_rotation(2.0), 1.0)


class TestScoreEvent(unittest.TestCase):
    def test_all_positive(self):
        s = _score_event(10000, 5000, 0.10, 20, 10.0)
        self.assertGreater(s, 0.5)

    def test_all_negative(self):
        s = _score_event(-10000, -5000, -0.10, 0, -10.0)
        self.assertLess(s, -0.5)

    def test_neutral(self):
        s = _score_event(0, 0, 0, 0, 0)
        self.assertEqual(s, 0.0)

    def test_bounds(self):
        s = _score_event(1e9, 1e9, 10, 1000, 100)
        self.assertLessEqual(s, 1.0)
        s = _score_event(-1e9, -1e9, -10, 0, -100)
        self.assertGreaterEqual(s, -1.0)


class TestScoreSentiment(unittest.TestCase):
    def test_positive(self):
        s = _score_sentiment(0.8, 0.9)
        self.assertGreater(s, 0.5)

    def test_negative(self):
        s = _score_sentiment(-0.9, 0.9)
        self.assertLess(s, -0.5)

    def test_low_confidence_dampened(self):
        """conf 太低时情绪值被压制 (避免噪声)"""
        high = abs(_score_sentiment(0.5, 0.9))
        low = abs(_score_sentiment(0.5, 0.1))
        self.assertLess(low, high)

    def test_conf_floor(self):
        """conf 即使为 0, 情绪也至少有 0.3 系数 — 不完全归零"""
        s = _score_sentiment(0.9, 0.0)
        self.assertGreater(s, 0.2)


class TestScoreMomentum(unittest.TestCase):
    def test_positive(self):
        s = _score_momentum(0.05)  # +5%
        self.assertGreater(s, 0)

    def test_negative(self):
        s = _score_momentum(-0.05)
        self.assertLess(s, 0)

    def test_extreme_clamped(self):
        self.assertEqual(_score_momentum(0.99), 1.0)
        self.assertEqual(_score_momentum(-0.99), -1.0)


class TestScoreVolatility(unittest.TestCase):
    def test_zero_vol_max(self):
        self.assertEqual(_score_volatility(0), 1.0)

    def test_high_vol_negative(self):
        self.assertLess(_score_volatility(0.10), 0)

    def test_threshold(self):
        """5% 日波动 → -1"""
        self.assertEqual(_score_volatility(0.05), -1.0)

    def test_monotonic_decreasing(self):
        a = _score_volatility(0.01)
        b = _score_volatility(0.02)
        c = _score_volatility(0.03)
        self.assertGreater(a, b)
        self.assertGreater(b, c)


class TestCompositeScore(unittest.TestCase):
    """综合分加权"""

    def test_default_weights_sum(self):
        self.assertAlmostEqual(sum(_DEFAULT_WEIGHTS.values()), 1.0, places=4)

    def test_empty(self):
        f = FactorScore(code="x")
        composite_score(f)
        self.assertEqual(f.composite, 0.0)
        self.assertEqual(f.confidence, 0.0)

    def test_full_data_high_score(self):
        f = FactorScore(
            code="x",
            sector_rotation=0.8, event=0.9, sentiment=0.7,
            momentum=0.5, volatility=0.6,
        )
        composite_score(f)
        self.assertGreater(f.composite, 0.5)
        self.assertEqual(f.confidence, 1.0)

    def test_missing_data_renormalizes(self):
        """只传 1 类因子 → 综合分 = 该类值, 置信度 = 0.2"""
        f = FactorScore(code="x", event=0.9)
        composite_score(f)
        self.assertEqual(f.composite, 0.9)
        self.assertAlmostEqual(f.confidence, 0.2, places=4)

    def test_missing_two_classes(self):
        f = FactorScore(code="x", event=0.8, momentum=0.6)
        composite_score(f)
        # weight event=0.25, momentum=0.20 → 0.45 总权重
        # composite = (0.25*0.8 + 0.20*0.6) / 0.45 ≈ 0.7111
        self.assertAlmostEqual(f.composite, 0.7111, places=3)
        self.assertAlmostEqual(f.confidence, 0.4, places=4)

    def test_zero_weights_fallback(self):
        """所有权重都为 0 → 均匀加权"""
        f = FactorScore(code="x", sector_rotation=0.6, event=0.4)
        composite_score(f, weights={k: 0.0 for k in _DEFAULT_WEIGHTS})
        self.assertAlmostEqual(f.composite, 0.5, places=4)


class TestBuildFromComponents(unittest.TestCase):
    def test_full(self):
        f = build_from_components(
            "600519",
            sector_rotation=0.5,
            event_components={
                "institution_net": 10000, "hot_money_net": 5000,
                "block_premium": 0.05, "investigate": 10, "lhb_reversal": 5.0,
            },
            sentiment_components={"sentiment": 0.6, "confidence": 0.8},
            ret_n=0.05, vol_n=0.02,
        )
        self.assertEqual(f.code, "600519")
        self.assertGreater(f.composite, 0.3)
        self.assertEqual(f.confidence, 1.0)
        self.assertEqual(f.components["ret_n"], 0.05)
        self.assertTrue(f.has_data)

    def test_partial(self):
        f = build_from_components("600519", sector_rotation=0.5)
        self.assertEqual(f.composite, 0.5)
        self.assertAlmostEqual(f.confidence, 0.2, places=4)
        self.assertEqual(f.sector_rotation, 0.5)
        self.assertIsNone(f.event)  # 没传 = None, 不是 0.0

    def test_empty(self):
        f = build_from_components("600519")
        self.assertEqual(f.composite, 0.0)
        self.assertEqual(f.confidence, 0.0)
        self.assertFalse(f.has_data)

    def test_build_minimal(self):
        f = build_minimal("x", ret_n=0.1)
        self.assertEqual(f.code, "x")
        self.assertGreater(f.momentum, 0)


class TestRankScores(unittest.TestCase):
    def test_basic(self):
        scores = [
            FactorScore(code="A", composite=0.2),
            FactorScore(code="B", composite=0.8),
            FactorScore(code="C", composite=0.5),
        ]
        ranked = rank_scores(scores)
        self.assertEqual(ranked[0].code, "B")
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual(ranked[2].code, "A")
        self.assertEqual(ranked[2].rank, 3)

    def test_top_n(self):
        scores = [FactorScore(code=str(i), composite=i / 10) for i in range(5)]
        ranked = rank_scores(scores, top_n=2)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0].code, "4")

    def test_empty(self):
        self.assertEqual(rank_scores([]), [])


class TestExplain(unittest.TestCase):
    def test_no_significant(self):
        f = FactorScore(code="x", composite=0.05)
        self.assertIn("无显著因子", explain(f))

    def test_all_positive(self):
        f = FactorScore(
            code="x", sector_rotation=0.5, event=0.6, sentiment=0.4,
            momentum=0.3, volatility=0.5,
        )
        text = explain(f)
        self.assertIn("板块+", text)
        self.assertIn("事件+", text)
        self.assertIn("情绪+", text)

    def test_negative_format(self):
        f = FactorScore(code="x", event=-0.5, momentum=-0.3)
        text = explain(f)
        self.assertIn("事件-", text)
        self.assertIn("动量-", text)

    def test_small_value_hidden(self):
        """|value| < 0.1 的因子不列出来, 避免噪音"""
        f = FactorScore(code="x", sentiment=0.05, event=0.5)
        text = explain(f)
        self.assertNotIn("情绪", text)
        self.assertIn("事件", text)


class TestToDictList(unittest.TestCase):
    def test_serialization(self):
        d = to_dict_list([FactorScore(code="x", composite=0.5)])[0]
        self.assertEqual(d["code"], "x")
        self.assertEqual(d["composite"], 0.5)
        self.assertEqual(d["rank"], 0)
        self.assertIn("components", d)

    def test_empty(self):
        self.assertEqual(to_dict_list([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)