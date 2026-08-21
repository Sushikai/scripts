#!/usr/bin/env python3
"""
test_strat_era09_vol_regime.py
Ship 65 单元测试 — 波动率体制
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era09_vol_regime import (
    VolRegime, realized_vol, vol_series,
    classify_regime, recommend, REGIME_STRATEGY,
    detect, detect_universe, aggregate_regime, summarize,
)


class TestRealizedVol(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        v = realized_vol(prices, window=20)
        self.assertIsNotNone(v)
        self.assertGreater(v, 0)

    def test_short(self):
        self.assertIsNone(realized_vol([10.0] * 5, window=20))


class TestVolSeries(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 + (i % 3) * 0.05 for i in range(60)]
        vs = vol_series(prices, window=20)
        self.assertGreater(len(vs), 0)


class TestClassifyRegime(unittest.TestCase):
    def test_low(self):
        # vol_history 5 个 0.01, current 0.001 → pct 接近 0
        regime, pct, z = classify_regime(0.001, [0.01, 0.012, 0.015, 0.02, 0.025])
        self.assertEqual(regime, "low_vol")

    def test_high(self):
        # 0.0225 → 落在 0.02 和 0.025 之间 (rank 4/5 = 80%)
        regime, pct, z = classify_regime(0.0225, [0.01, 0.012, 0.015, 0.02, 0.025])
        self.assertEqual(regime, "high_vol")

    def test_crisis(self):
        # 0.1 → rank 5/5 = 100% (>= 95% → crisis)
        regime, pct, z = classify_regime(0.1, [0.01, 0.012, 0.015, 0.02, 0.025])
        self.assertEqual(regime, "crisis")

    def test_normal(self):
        regime, pct, z = classify_regime(0.015, [0.01, 0.012, 0.015, 0.02, 0.025])
        self.assertEqual(regime, "normal")


class TestRecommend(unittest.TestCase):
    def test_basic(self):
        for r in ["low_vol", "normal", "high_vol", "crisis"]:
            rec, size, reason = recommend(r)
            self.assertIn(rec, ["trend", "reversion", "defensive", "neutral"])
            self.assertGreater(size, 0)


class TestDetect(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 + (i % 5) * 0.05 for i in range(100)]
        r = detect("a", prices)
        self.assertIsNotNone(r)
        self.assertIn(r.regime, ["low_vol", "normal", "high_vol", "crisis"])

    def test_short(self):
        r = detect("a", [10.0] * 30)
        self.assertIsNone(r)


class TestDetectUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(100)],
            "b": [10.0 + (i % 10) * 0.5 for i in range(100)],
        }
        out = detect_universe(universe)
        self.assertEqual(len(out), 2)


class TestAggregateRegime(unittest.TestCase):
    def test_basic(self):
        regimes = {
            "a": VolRegime("a", "low_vol", 0.01, 0.1, 0.0, "trend", 1.0, "low"),
            "b": VolRegime("b", "high_vol", 0.05, 0.8, 1.5, "reversion", 0.8, "high"),
            "c": VolRegime("c", "normal", 0.02, 0.5, 0.0, "neutral", 1.0, "normal"),
        }
        agg = aggregate_regime(regimes)
        self.assertEqual(agg["low_vol"], 1)
        self.assertEqual(agg["high_vol"], 1)
        self.assertEqual(agg["normal"], 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(100)]
        r = detect("a", prices)
        self.assertIsNotNone(r)
        s = summarize(r)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 for i in range(100)]
        r = detect("a", prices)
        d = r.to_dict()
        self.assertEqual(d["code"], "a")
        self.assertIn("regime", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)