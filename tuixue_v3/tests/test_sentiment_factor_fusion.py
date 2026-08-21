#!/usr/bin/env python3
"""
test_sentiment_factor_fusion.py
Ship 51 单元测试 — 情绪 × 因子融合
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.sentiment_factor_fusion import (
    FusionResult, fuse, fuse_multi, reverse_fuse, _ic_to_unit,
)


class TestICToUnit(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(_ic_to_unit(0.0), 0.5)
        self.assertAlmostEqual(_ic_to_unit(1.0), 1.0)
        self.assertAlmostEqual(_ic_to_unit(-1.0), 0.0)


class TestFuse(unittest.TestCase):
    def test_neutral(self):
        """中性情绪 + 中性 IC → 1.0"""
        r = fuse(base_alpha=1.0, sentiment_pct=0.5, factor_ic=0.0)
        self.assertAlmostEqual(r.fusion, 1.0)
        self.assertEqual(r.regime, "neutral")

    def test_positive_ic_fear_boost(self):
        """正 IC + 恐惧 → 加成"""
        r = fuse(1.0, sentiment_pct=0.1, factor_ic=0.5)
        # fear (0.1 < 0.5) + positive IC → adjustment > 0 → boost
        self.assertEqual(r.regime, "boost")
        self.assertGreater(r.fusion, 1.0)

    def test_positive_ic_greed_suppress(self):
        """正 IC + 贪婪 → 抑制"""
        r = fuse(1.0, sentiment_pct=0.9, factor_ic=0.5)
        self.assertEqual(r.regime, "suppress")
        self.assertLess(r.fusion, 1.0)

    def test_negative_ic_greed_boost(self):
        """负 IC + 贪婪 → 加成"""
        r = fuse(1.0, sentiment_pct=0.9, factor_ic=-0.5)
        # sign < 0, sentiment_pct > 0.5 → adjustment > 0 → boost
        self.assertEqual(r.regime, "boost")

    def test_bounded(self):
        """fusion 限幅 [0.6, 1.4]"""
        r1 = fuse(1.0, 0.0, 0.0)  # min
        self.assertGreaterEqual(r1.fusion, 0.6)
        r2 = fuse(1.0, 1.0, 1.0)  # max
        self.assertLessEqual(r2.fusion, 1.4)

    def test_adjusted_alpha(self):
        """adjusted = base * fusion"""
        r = fuse(2.0, sentiment_pct=0.9, factor_ic=0.5)
        self.assertAlmostEqual(
            r.adjusted_alpha, r.base_alpha * r.fusion,
        )


class TestFuseMulti(unittest.TestCase):
    def test_basic(self):
        ics = {"a": 0.1, "b": 0.2}
        f = fuse_multi(0.5, ics)
        # 中性情绪 → 1.0
        self.assertAlmostEqual(f, 1.0)

    def test_no_factors(self):
        f = fuse_multi(0.5, {})
        self.assertEqual(f, 1.0)

    def test_with_weights(self):
        ics = {"a": 0.5, "b": 0.0}
        # a 全权重, 调整方向 = -(0.5-0.5)*0.5*0.8 = 0
        f = fuse_multi(0.5, ics, factor_weights={"a": 1.0, "b": 0.0})
        self.assertAlmostEqual(f, 1.0)


class TestReverseFuse(unittest.TestCase):
    def test_basic(self):
        r1 = fuse(1.0, 0.8, 0.3)
        r2 = reverse_fuse(1.0, 0.8, 0.3)
        # 应不同
        self.assertNotAlmostEqual(r1.fusion, r2.fusion, places=2)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        r = fuse(1.0, 0.6, 0.1)
        d = r.to_dict()
        self.assertIn("fusion", d)
        self.assertIn("regime", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
