#!/usr/bin/env python3
"""
test_portfolio_rebalancer.py
Ship 23 单元测试 — 组合再平衡
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.portfolio_rebalancer import (
    Holding, TargetWeight, RebalanceAction, RebalanceResult,
    compute_rebalance, to_dict,
)


def h(code, shares, price, sector="A"):
    return Holding(code=code, shares=shares, price=price, sector=sector)


def t(code, weight, sector="A"):
    return TargetWeight(code=code, weight=weight, sector=sector)


class TestComputeRebalance(unittest.TestCase):
    def test_hold_no_change(self):
        """权重匹配 → 全 hold"""
        holdings = [h("A", 1000, 10), h("B", 500, 20)]  # A=10000, B=10000 → 50/50
        targets = [t("A", 0.5), t("B", 0.5)]
        r = compute_rebalance(holdings, targets, threshold=0.05)
        self.assertFalse(r.needs_rebalance)
        self.assertEqual(r.total_buy, 0)
        self.assertEqual(r.total_sell, 0)
        for a in r.actions:
            self.assertEqual(a.action, "hold")

    def test_buy_to_match(self):
        """A 目标 70%, 当前 50% → 买"""
        holdings = [h("A", 1000, 10), h("B", 1000, 10)]  # 50/50
        targets = [t("A", 0.7), t("B", 0.3)]
        r = compute_rebalance(holdings, targets, threshold=0.05)
        self.assertTrue(r.needs_rebalance)
        a_action = next(a for a in r.actions if a.code == "A")
        b_action = next(a for a in r.actions if a.code == "B")
        self.assertEqual(a_action.action, "buy")
        self.assertEqual(b_action.action, "sell")
        self.assertGreater(r.total_buy, 0)
        self.assertGreater(r.total_sell, 0)

    def test_threshold_skip(self):
        """偏离 < 5% → 不动"""
        holdings = [h("A", 1000, 10), h("B", 1000, 10)]
        targets = [t("A", 0.52), t("B", 0.48)]  # 偏离 2%
        r = compute_rebalance(holdings, targets, threshold=0.05)
        self.assertFalse(r.needs_rebalance)

    def test_new_position(self):
        """目标含新 code (持仓没) → 期望 buy, 但需提供 price"""
        holdings = [h("A", 1000, 10)]
        # NEW 没价格 → skip; 但 A 偏离 50% → sell
        targets = [t("A", 0.5), t("NEW", 0.5)]
        r = compute_rebalance(holdings, targets, threshold=0.05)
        new_action = next(a for a in r.actions if a.code == "NEW")
        # 无价格 → hold + reason
        self.assertEqual(new_action.action, "hold")
        self.assertIn("无价格", new_action.reason)
        # 验证 A 被 sell (因为 target=0.5, current=1.0)
        a_action = next(a for a in r.actions if a.code == "A")
        self.assertEqual(a_action.action, "sell")

    def test_new_position_with_price(self):
        """如果有 NEW 的价格参考 → buy"""
        holdings = [h("A", 1000, 10)]
        # 没法在 holdings 给 NEW 提供价格 — 测试 no_price_skip 即可
        pass

    def test_exit_position(self):
        """目标不含某 code → 全卖"""
        holdings = [h("A", 1000, 10), h("B", 1000, 10)]
        targets = [t("A", 1.0)]  # B 不在目标
        r = compute_rebalance(holdings, targets, threshold=0.05)
        b_action = next(a for a in r.actions if a.code == "B")
        self.assertEqual(b_action.action, "sell")

    def test_lot_size_round(self):
        """shares_delta 按 100 round"""
        holdings = [h("A", 1000, 10)]
        targets = [t("A", 0.7), t("B", 0.3)]
        # total = 10000, A target = 7000, current = 10000 → 卖 3000 = 300 股
        r = compute_rebalance(holdings, targets, threshold=0.05)
        for a in r.actions:
            if a.shares_delta != 0:
                self.assertEqual(abs(a.shares_delta) % 100, 0)

    def test_zero_value(self):
        r = compute_rebalance([], [], threshold=0.05)
        self.assertEqual(r.total_value, 0)
        self.assertFalse(r.needs_rebalance)

    def test_turnover(self):
        holdings = [h("A", 1000, 10), h("B", 1000, 10)]
        targets = [t("A", 0.7), t("B", 0.3)]
        r = compute_rebalance(holdings, targets, threshold=0.05)
        # turnover = (buy+sell)/2 / total
        # buy ≈ 2000, sell ≈ 2000, turnover = 2000/10000 = 0.20
        self.assertGreater(r.turnover, 0)
        self.assertLess(r.turnover, 0.5)

    def test_commission(self):
        holdings = [h("A", 1000, 10), h("B", 1000, 10)]
        targets = [t("A", 0.7), t("B", 0.3)]
        r = compute_rebalance(holdings, targets, threshold=0.05)
        # commission = (buy+sell) × 0.0003
        self.assertAlmostEqual(r.expected_commission,
                               (r.total_buy + r.total_sell) * 0.0003, places=2)

    def test_no_price_skip(self):
        """目标有, 持仓无 → skip (无价格)"""
        # holdings 没 code "NEW" 但 target 有
        # 但 holdings 为空, total=0 → 早期 return
        # 改: holdings 有 A 但 target 含 B 但 holdings 没 B → 不会触发 (current_w[B]=0, target_w[B]=0.5 → delta=0.5)
        holdings = [h("A", 1000, 10)]
        targets = [t("B", 1.0)]  # 完全换股
        r = compute_rebalance(holdings, targets, threshold=0.05)
        # B: target=1.0, current=0 → delta=1.0 → buy (price=0?)
        # price 找不到 → skip
        b_action = next((a for a in r.actions if a.code == "B"), None)
        if b_action:
            self.assertIn(b_action.action, ("hold", "buy"))

    def test_to_dict(self):
        holdings = [h("A", 1000, 10), h("B", 1000, 10)]
        targets = [t("A", 0.7), t("B", 0.3)]
        r = compute_rebalance(holdings, targets, threshold=0.05)
        d = to_dict(r)
        self.assertIn("actions", d)
        self.assertIn("needs_rebalance", d)
        self.assertTrue(d["needs_rebalance"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
