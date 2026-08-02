#!/usr/bin/env python3
"""
test_performance_metrics.py
Ship 29 单元测试 — 业绩归因
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.performance_metrics import (
    AttributedTrade, AttributionResult,
    compute_attribution, to_dict, _pearson,
)


def make_trades(n=50, seed=42):
    import random
    rng = random.Random(seed)
    trades = []
    sectors = ["新能源", "半导体", "医药", "金融"]
    for i in range(n):
        momentum = rng.uniform(-1, 1)
        sentiment = rng.uniform(-1, 1)
        composite = (momentum + sentiment) / 2
        # 收益跟 momentum 正相关
        ret = 0.02 + 0.05 * momentum + rng.uniform(-0.02, 0.02)
        trades.append(AttributedTrade(
            code=f"S{i:04d}", sector=sectors[i % 4],
            factor_composite=composite,
            factor_components={"momentum": momentum, "sentiment": sentiment},
            ret=ret, weight=0.05,
            date="2026-08-01",
        ))
    return trades


class TestPearson(unittest.TestCase):
    def test_perfect(self):
        self.assertAlmostEqual(_pearson([1, 2, 3], [2, 4, 6]), 1.0, places=4)
        self.assertAlmostEqual(_pearson([1, 2, 3], [6, 4, 2]), -1.0, places=4)


class TestComputeAttribution(unittest.TestCase):
    def test_insufficient(self):
        trades = make_trades(n=10)
        r = compute_attribution(trades, min_samples=30)
        self.assertFalse(r.is_meaningful)
        self.assertEqual(r.factor_contributions, {})

    def test_meaningful(self):
        trades = make_trades(n=50)
        r = compute_attribution(trades, min_samples=30)
        self.assertTrue(r.is_meaningful)
        self.assertIn("momentum", r.factor_contributions)
        self.assertIn("_composite", r.factor_contributions)

    def test_momentum_high_ic(self):
        """momentum 与收益正相关 → IC 应 > 0"""
        trades = make_trades(n=50)
        r = compute_attribution(trades, min_samples=30)
        # momentum 和 ret 强正相关, IC 应 > 0.3
        self.assertGreater(r.factor_contributions["momentum"], 0.3)

    def test_sector_contributions(self):
        trades = make_trades(n=50)
        r = compute_attribution(trades, min_samples=30)
        self.assertGreater(len(r.sector_contributions), 0)
        # 占比之和应接近 0 (正负相抵)
        s = sum(r.sector_contributions.values())
        self.assertLess(abs(s), 0.2)  # 宽松

    def test_selection_contribution(self):
        """top 25% - bottom 25% 应 > 0 (因为 momentum 有效)"""
        trades = make_trades(n=50)
        r = compute_attribution(trades, min_samples=30)
        self.assertGreater(r.selection_contribution, 0)

    def test_factor_breakdown(self):
        trades = make_trades(n=50)
        r = compute_attribution(trades, min_samples=30)
        self.assertIn("momentum", r.factor_breakdown)
        mb = r.factor_breakdown["momentum"]
        self.assertIn("top_avg_ret", mb)
        self.assertIn("bottom_avg_ret", mb)
        # top spread > 0 (因为 ret 跟 momentum 正相关)
        self.assertGreater(mb["spread"], 0)

    def test_empty(self):
        r = compute_attribution([])
        self.assertEqual(r.n_trades, 0)
        self.assertFalse(r.is_meaningful)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        trades = make_trades(n=50)
        r = compute_attribution(trades)
        d = to_dict(r)
        self.assertIn("factor_contributions", d)
        self.assertIn("sector_contributions", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
