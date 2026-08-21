#!/usr/bin/env python3
"""
test_strat_era11_risk_parity.py
Ship 67 单元测试 — 风险平价
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era11_risk_parity import (
    RiskParityResult, asset_vol, risk_parity_weights,
    compute_risk_contribs, vol_targeting_weights,
    allocate, rebalance_needed, summarize,
)


class TestAssetVol(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.1 + (i % 3) * 0.05 for i in range(30)]
        v = asset_vol(prices, window=20)
        self.assertIsNotNone(v)
        self.assertGreater(v, 0)

    def test_short(self):
        self.assertIsNone(asset_vol([10.0] * 5, window=20))


class TestRiskParityWeights(unittest.TestCase):
    def test_basic(self):
        vols = {"a": 0.10, "b": 0.20, "c": 0.40}
        w = risk_parity_weights(vols)
        # 反比: a 应最大
        self.assertEqual(w["a"], max(w.values()))
        # 总和 = 1
        self.assertAlmostEqual(sum(w.values()), 1.0)

    def test_empty(self):
        w = risk_parity_weights({})
        self.assertEqual(w, {})

    def test_zero_vol(self):
        vols = {"a": 0.10, "b": 0.0}
        w = risk_parity_weights(vols)
        self.assertAlmostEqual(sum(w.values()), 1.0)


class TestRiskContribs(unittest.TestCase):
    def test_basic(self):
        weights = {"a": 0.5, "b": 0.5}
        vols = {"a": 0.1, "b": 0.2}
        rc = compute_risk_contribs(weights, vols)
        self.assertAlmostEqual(rc["a"], 0.05)
        self.assertAlmostEqual(rc["b"], 0.10)


class TestVolTargeting(unittest.TestCase):
    def test_basic(self):
        vols = {"a": 0.10, "b": 0.20}
        w = vol_targeting_weights(vols, target_vol=0.10)
        # 缩放后总和 ≈ target_vol
        port_vol = sum(w[c] * vols[c] for c in w)
        self.assertAlmostEqual(port_vol, 0.10, places=3)


class TestAllocate(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(30)],
            "b": [10.0 - i * 0.1 for i in range(30)],
        }
        r = allocate(universe, window=20)
        self.assertEqual(r.n_assets, 2)
        # 用 vol_targeting, 缩放后可能 > 1
        # 不带 vol_targeting 应该 = 1
        r2 = allocate(universe, window=20, use_vol_targeting=False)
        self.assertAlmostEqual(sum(r2.weights.values()), 1.0, places=2)

    def test_empty(self):
        r = allocate({})
        self.assertEqual(r.n_assets, 0)


class TestRebalanceNeeded(unittest.TestCase):
    def test_needs(self):
        current = {"a": 0.6, "b": 0.4}
        target = {"a": 0.5, "b": 0.5}
        needs, deltas = rebalance_needed(current, target, threshold=0.05)
        self.assertTrue(needs)

    def test_not_needed(self):
        current = {"a": 0.51, "b": 0.49}
        target = {"a": 0.5, "b": 0.5}
        needs, deltas = rebalance_needed(current, target, threshold=0.05)
        self.assertFalse(needs)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(30)],
            "b": [10.0 - i * 0.1 for i in range(30)],
        }
        r = allocate(universe, window=20)
        s = summarize(r)
        self.assertIn("Risk Parity", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0 + i * 0.1 for i in range(30)],
        }
        r = allocate(universe, window=20)
        d = r.to_dict()
        self.assertEqual(d["n_assets"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)