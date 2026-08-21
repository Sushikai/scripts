#!/usr/bin/env python3
"""
test_strat_era01_adaptive.py
Ship 57 单元测试 — 自适应 regime 策略
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era01_adaptive import (
    AdaptivePick, AdaptiveResult, REGIME_PREFERENCE,
    adaptive_score, select_picks,
    best_regime_for_score, regime_suitability, to_dict,
)


class TestAdaptiveScore(unittest.TestCase):
    def test_bull_mom_boost(self):
        # bull + 高 mom → 加成
        s = adaptive_score(1.0, {"mom": 1.0, "val": 0.0}, "bull", 1.0)
        # bull pref mom=1.5
        self.assertAlmostEqual(s, 1.5)

    def test_crisis_penalize_mom(self):
        s = adaptive_score(1.0, {"mom": 1.0}, "crisis", 1.0)
        # crisis pref mom=0.0 → multiplier=0
        self.assertAlmostEqual(s, 0.0)

    def test_no_exposures(self):
        s = adaptive_score(1.0, {}, "bull", 1.0)
        # 无暴露 → multiplier=1
        self.assertAlmostEqual(s, 1.0)

    def test_regime_factor(self):
        s = adaptive_score(1.0, {"mom": 1.0}, "bull", 1.5)
        self.assertAlmostEqual(s, 1.5 * 1.5)


class TestSelectPicks(unittest.TestCase):
    def test_basic(self):
        candidates = [
            {"code": "1", "name": "A", "raw_score": 1.0, "factor_exposures": {"mom": 1.0}},
            {"code": "2", "name": "B", "raw_score": 0.5, "factor_exposures": {"mom": 1.0}},
            {"code": "3", "name": "C", "raw_score": -0.5, "factor_exposures": {"val": 1.0}},
        ]
        r = select_picks("bull", candidates)
        self.assertEqual(r.regime, "bull")
        self.assertEqual(len(r.top_n(5)), 3)
        # 第一个应是 raw 分最高的 (mom bull 偏好)
        self.assertEqual(r.picks[0].code, "1")

    def test_max_picks(self):
        candidates = [
            {"code": str(i), "name": f"A{i}", "raw_score": float(i)}
            for i in range(20)
        ]
        r = select_picks("bull", candidates, max_picks=5)
        # candidates 全收进 picks, 但 top_n=5
        self.assertEqual(len(r.top_n(5)), 5)

    def test_weight_distribution(self):
        candidates = [
            {"code": "1", "name": "A", "raw_score": 1.0, "factor_exposures": {"mom": 1.0}},
            {"code": "2", "name": "B", "raw_score": 1.0, "factor_exposures": {"mom": 1.0}},
        ]
        r = select_picks("bull", candidates, max_picks=2)
        # 两个权重应近似 0.5
        total = sum(p.weight for p in r.top_n(2))
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_unknown_regime(self):
        r = select_picks("foo", [])
        self.assertEqual(r.regime, "foo")
        self.assertEqual(r.picks, [])


class TestBestRegime(unittest.TestCase):
    def test_basic(self):
        scores = {"bull": 0.1, "bear": 0.3, "range": -0.1}
        self.assertEqual(best_regime_for_score(scores), "bear")

    def test_empty(self):
        self.assertEqual(best_regime_for_score({}), "unknown")


class TestRegimeSuitability(unittest.TestCase):
    def test_bull_mom_lover(self):
        # 高 mom 暴露 → bull suit 高
        suit = regime_suitability({"mom": 1.0, "val": 0.0}, "bull")
        # bull pref mom=1.5, val=0.5
        # suit: 1*1.5 + 0*0.5 = 1.5; |mom|+|val| = 1; ratio = 1.5
        # final = 0.5 + 1.5 * 0.5 = 1.25 → clamp 1.0
        self.assertEqual(suit, 1.0)

    def test_bear_mom_lover_low(self):
        # 高 mom → bear suit 低
        suit = regime_suitability({"mom": 1.0}, "bear")
        # bear pref mom=0.5
        # suit = 0.5 + 0.5 * 0.5 = 0.75
        self.assertAlmostEqual(suit, 0.75, places=2)

    def test_no_exposures(self):
        suit = regime_suitability({}, "bull")
        self.assertEqual(suit, 0.5)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        candidates = [
            {"code": "1", "raw_score": 1.0, "factor_exposures": {"mom": 1.0}},
        ]
        r = select_picks("bull", candidates)
        d = to_dict(r)
        self.assertEqual(d["regime"], "bull")
        self.assertEqual(len(d["picks"]), 1)


class TestRegimePreferences(unittest.TestCase):
    def test_bull_prefers_mom(self):
        self.assertGreater(REGIME_PREFERENCE["bull"]["mom"],
                          REGIME_PREFERENCE["bear"]["mom"])

    def test_crisis_zero_mom(self):
        self.assertEqual(REGIME_PREFERENCE["crisis"]["mom"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
