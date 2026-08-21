#!/usr/bin/env python3
"""
test_risk_budget.py
Ship 30 单元测试 — 风险预算
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.risk_budget import (
    StrategyRisk, RiskBudgetResult,
    allocate_risk_budget, to_dict,
)


def sr(name, vol=0.02, dd=0.0, n=10):
    return StrategyRisk(name=name, expected_vol=vol,
                        historical_dd=dd, n_trades=n)


class TestAllocate(unittest.TestCase):
    def test_empty(self):
        r = allocate_risk_budget(0.02, [])
        self.assertEqual(r.per_strategy, {})

    def test_basic_risk_parity(self):
        """低 vol 分更多"""
        strategies = [
            sr("low_vol", vol=0.01),   # 1% vol
            sr("high_vol", vol=0.04),  # 4% vol
        ]
        r = allocate_risk_budget(0.02, strategies)
        # low_vol 应该分到约 80% 预算 (4x higher weight)
        self.assertGreater(r.per_strategy_pct["low_vol"], 0.7)
        self.assertLess(r.per_strategy_pct["high_vol"], 0.3)

    def test_dd_penalty(self):
        """DD > 20% 减半"""
        strategies = [
            sr("good", vol=0.02, dd=0.05),
            sr("bad", vol=0.02, dd=0.25),
        ]
        r = allocate_risk_budget(0.02, strategies)
        # good 应分得比 bad 多 (初始权重相等, bad 减半)
        self.assertGreater(r.per_strategy_pct["good"],
                          r.per_strategy_pct["bad"])

    def test_dd_moderate(self):
        """DD 10-20% 减 25%"""
        strategies = [
            sr("baseline", vol=0.02, dd=0.0),
            sr("moderate", vol=0.02, dd=0.12),
        ]
        r = allocate_risk_budget(0.02, strategies)
        # moderate 应略少
        self.assertGreater(r.per_strategy_pct["baseline"],
                          r.per_strategy_pct["moderate"])

    def test_correlation_penalty(self):
        """相关性 > 0.7 减权"""
        strategies = [
            sr("A", vol=0.02),
            sr("B", vol=0.02),
            sr("C", vol=0.02),
        ]
        corr = {
            ("A", "B"): 0.8,
            ("B", "A"): 0.8,
        }
        r1 = allocate_risk_budget(0.02, strategies)
        r2 = allocate_risk_budget(0.02, strategies, corr)
        # r2 中 A 和 B 都被扣权
        # 总权重 A+B 在 r1 比 r2 大 (因为 C 没受影响)
        ab_pct_r1 = r1.per_strategy_pct["A"] + r1.per_strategy_pct["B"]
        ab_pct_r2 = r2.per_strategy_pct["A"] + r2.per_strategy_pct["B"]
        self.assertGreater(ab_pct_r1, ab_pct_r2)
        # C 应分得更多
        self.assertGreater(r2.per_strategy_pct["C"], r1.per_strategy_pct["C"])

    def test_zero_vol_skipped(self):
        strategies = [
            sr("zero", vol=0.0),
            sr("normal", vol=0.02),
        ]
        r = allocate_risk_budget(0.02, strategies)
        self.assertEqual(r.per_strategy["zero"], 0)
        self.assertGreater(r.per_strategy["normal"], 0)
        self.assertTrue(any("zero" in w for w in r.warnings))

    def test_over_budget_warning(self):
        """1 个策略 vol 极低 → 分得过多"""
        strategies = [
            sr("tiny_vol", vol=0.001),
            sr("normal", vol=0.05),
        ]
        r = allocate_risk_budget(0.02, strategies)
        # tiny_vol 应分到 ~98%, 但上限 80% 检查
        if r.per_strategy_pct["tiny_vol"] > 0.8:
            self.assertIn("tiny_vol", r.over_budget)

    def test_max_combined_vol(self):
        strategies = [sr("A", vol=0.02), sr("B", vol=0.02)]
        r = allocate_risk_budget(0.02, strategies)
        # 组合 vol 应 ≤ 总预算
        self.assertLessEqual(r.max_combined_vol, 0.02)

    def test_to_dict(self):
        strategies = [sr("A", vol=0.02)]
        r = allocate_risk_budget(0.02, strategies)
        d = to_dict(r)
        self.assertIn("per_strategy", d)
        self.assertIn("warnings", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
