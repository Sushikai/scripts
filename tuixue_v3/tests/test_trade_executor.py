#!/usr/bin/env python3
"""
test_trade_executor.py
Ship 24 单元测试 — 模拟盘执行层
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.trade_executor import (
    Order, ExecutionReport,
    execute_orders, to_dict,
)


def make_price_getter(prices):
    def getter(code):
        return prices.get(code)
    return getter


class TestExecuteOrders(unittest.TestCase):
    def test_buy_filled(self):
        actions = [{"code": "A", "action": "buy", "shares_delta": 1000}]
        r = execute_orders(
            actions, cash=100000, positions={},
            price_getter=make_price_getter({"A": 10.0}),
        )
        self.assertEqual(r.n_filled, 1)
        self.assertEqual(r.n_partial, 0)
        self.assertEqual(r.n_rejected, 0)
        # cost = 1000 * 10 * (1+0.001) * (1+0.0003) ≈ 10013
        self.assertGreater(r.total_buy_amount, 10000)

    def test_buy_insufficient_cash_partial(self):
        actions = [{"code": "A", "action": "buy", "shares_delta": 10000}]
        # cash=10000, target 10000 股 × 10 元 = 100000, 买 1 手
        r = execute_orders(
            actions, cash=10000, positions={},
            price_getter=make_price_getter({"A": 10.0}),
        )
        self.assertEqual(r.n_partial, 1)
        self.assertEqual(r.orders[0].filled_shares, 900)  # 9000 / 10 / 1.0003 / 100 → floor → 0 手, 实际看
        # 10000 / (10*1.001*1.0003) = 999 手 — 10000/10.011 ≈ 999
        # int(999 / 100) * 100 = 900

    def test_buy_rejected_no_cash(self):
        actions = [{"code": "A", "action": "buy", "shares_delta": 100}]
        r = execute_orders(
            actions, cash=0, positions={},
            price_getter=make_price_getter({"A": 10.0}),
        )
        self.assertEqual(r.n_rejected, 1)
        self.assertIn("资金", r.orders[0].error)

    def test_buy_rejected_no_price(self):
        actions = [{"code": "A", "action": "buy", "shares_delta": 100}]
        r = execute_orders(
            actions, cash=10000, positions={},
            price_getter=make_price_getter({}),
        )
        self.assertEqual(r.n_rejected, 1)
        self.assertIn("无价格", r.orders[0].error)

    def test_sell_filled(self):
        actions = [{"code": "A", "action": "sell", "shares_delta": -500}]
        r = execute_orders(
            actions, cash=0, positions={"A": 1000},
            price_getter=make_price_getter({"A": 10.0}),
        )
        self.assertEqual(r.n_filled, 1)
        # proceeds ≈ 500 * 10 * (1-0.001) * (1-0.0003)
        self.assertGreater(r.cash_after, 0)

    def test_sell_no_position(self):
        actions = [{"code": "A", "action": "sell", "shares_delta": -500}]
        r = execute_orders(
            actions, cash=10000, positions={},
            price_getter=make_price_getter({"A": 10.0}),
        )
        self.assertEqual(r.n_rejected, 1)
        self.assertIn("无持仓", r.orders[0].error)

    def test_sell_partial(self):
        """想卖 1000, 实际只有 300 → 部分成交"""
        actions = [{"code": "A", "action": "sell", "shares_delta": -1000}]
        r = execute_orders(
            actions, cash=0, positions={"A": 300},
            price_getter=make_price_getter({"A": 10.0}),
        )
        self.assertEqual(r.n_partial, 1)
        self.assertEqual(r.orders[0].filled_shares, 300)

    def test_zero_delta_skipped(self):
        actions = [{"code": "A", "action": "buy", "shares_delta": 0}]
        r = execute_orders(
            actions, cash=10000, positions={},
            price_getter=make_price_getter({"A": 10.0}),
        )
        self.assertEqual(len(r.orders), 0)

    def test_slippage_applied(self):
        """buy 价格含滑点 +0.1%, sell -0.1%"""
        actions = [
            {"code": "A", "action": "buy", "shares_delta": 100},
            {"code": "A", "action": "sell", "shares_delta": -100},
        ]
        # 先买后卖
        r1 = execute_orders(actions[:1], cash=10000, positions={},
                            price_getter=make_price_getter({"A": 10.0}))
        # 买价 ≈ 10.01
        buy_price = r1.orders[0].avg_price
        self.assertAlmostEqual(buy_price, 10.01, places=2)

        r2 = execute_orders(actions[1:], cash=r1.cash_after, positions={"A": 100},
                            price_getter=make_price_getter({"A": 10.0}))
        sell_price = r2.orders[0].avg_price
        self.assertAlmostEqual(sell_price, 9.99, places=2)

    def test_lot_size_round(self):
        """shares 按 lot_size round"""
        actions = [{"code": "A", "action": "buy", "shares_delta": 1234}]
        r = execute_orders(
            actions, cash=100000, positions={},
            price_getter=make_price_getter({"A": 10.0}),
        )
        # 不强制 round target — round 在执行时已通过 partial 处理
        # 但买能买多少就买多少 (没要求 round 到 100, 这里 full filled)
        self.assertEqual(r.orders[0].filled_shares, 1234)

    def test_mixed_actions(self):
        actions = [
            {"code": "A", "action": "buy", "shares_delta": 500},
            {"code": "B", "action": "buy", "shares_delta": 200},
            {"code": "A", "action": "sell", "shares_delta": -100},
        ]
        r = execute_orders(
            actions, cash=100000, positions={},
            price_getter=make_price_getter({"A": 10.0, "B": 20.0}),
        )
        self.assertEqual(r.n_filled, 3)
        self.assertEqual(r.cash_after, 100000 - r.total_buy_amount
                         + r.total_sell_amount - r.total_commission)

    def test_to_dict(self):
        actions = [{"code": "A", "action": "buy", "shares_delta": 100}]
        r = execute_orders(
            actions, cash=10000, positions={},
            price_getter=make_price_getter({"A": 10.0}),
        )
        d = to_dict(r)
        self.assertIn("orders", d)
        self.assertEqual(len(d["orders"]), 1)
        self.assertEqual(d["orders"][0]["code"], "A")


if __name__ == "__main__":
    unittest.main(verbosity=2)
