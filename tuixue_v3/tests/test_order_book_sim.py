#!/usr/bin/env python3
"""
test_order_book_sim.py
Ship 31 单元测试 — 订单簿模拟
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.order_book_sim import (
    PriceLevel, OrderBook, FillResult,
    simulate_fill, estimate_slippage, to_dict,
)


def make_book(mid=10.0, levels=5, depth_per_level=1000, spread_bps=5):
    """合成盘口"""
    spread = mid * spread_bps / 10000
    bid0 = mid - spread / 2
    ask0 = mid + spread / 2
    bids = [PriceLevel(price=bid0 - i * 0.01, volume=depth_per_level)
            for i in range(levels)]
    asks = [PriceLevel(price=ask0 + i * 0.01, volume=depth_per_level)
            for i in range(levels)]
    return OrderBook(bids=bids, asks=asks, last_price=mid)


class TestSimulateFill(unittest.TestCase):
    def test_zero_shares(self):
        r = simulate_fill(make_book(), "buy", 0)
        self.assertEqual(r.filled_shares, 0)
        self.assertEqual(r.unfilled_shares, 0)

    def test_buy_one_level(self):
        book = make_book(levels=5, depth_per_level=1000)
        r = simulate_fill(book, "buy", 500)
        # 吃第一档 1000 股 @ ask0, VWAP = ask0
        self.assertEqual(r.filled_shares, 500)
        self.assertEqual(r.levels_consumed, 1)
        self.assertEqual(r.unfilled_shares, 0)

    def test_buy_multi_levels(self):
        book = make_book(levels=5, depth_per_level=1000, spread_bps=10)
        r = simulate_fill(book, "buy", 2500)
        # 3 档 (1000+1000+500)
        self.assertEqual(r.filled_shares, 2500)
        self.assertEqual(r.levels_consumed, 3)
        self.assertEqual(r.unfilled_shares, 0)

    def test_buy_exceed_depth(self):
        book = make_book(levels=3, depth_per_level=1000)
        r = simulate_fill(book, "buy", 10000)
        # 只成交 3000
        self.assertEqual(r.filled_shares, 3000)
        self.assertEqual(r.unfilled_shares, 7000)
        self.assertEqual(r.levels_consumed, 3)

    def test_sell_one_level(self):
        book = make_book(levels=5, depth_per_level=1000)
        r = simulate_fill(book, "sell", 500)
        # 抛第一档 bid0
        self.assertEqual(r.filled_shares, 500)
        self.assertEqual(r.levels_consumed, 1)

    def test_sell_multi_levels(self):
        book = make_book(levels=5, depth_per_level=1000)
        r = simulate_fill(book, "sell", 2500)
        self.assertEqual(r.filled_shares, 2500)
        self.assertEqual(r.levels_consumed, 3)

    def test_slippage_calculation(self):
        book = make_book(mid=10.0, spread_bps=5, depth_per_level=1000)
        r = simulate_fill(book, "buy", 100)
        # VWAP = ask0 = 10.025, mid = 10, slip = 0.025/10 * 10000 = 25 bp? No
        # spread = 10 * 5/10000 = 0.005, half = 0.0025
        # ask0 = 10.0025, VWAP = 10.0025, slip = 0.0025/10 * 10000 = 2.5 bp
        self.assertGreater(r.slippage_bps, 0)
        self.assertLess(r.slippage_bps, 100)

    def test_no_book_default_slippage(self):
        book = OrderBook(last_price=10.0)
        r = simulate_fill(book, "buy", 100)
        self.assertEqual(r.filled_shares, 100)
        self.assertEqual(r.levels_consumed, 0)
        # default slippage = 10bp
        self.assertAlmostEqual(r.slippage_bps, 10.0, places=1)

    def test_no_mid_price(self):
        book = OrderBook()
        r = simulate_fill(book, "buy", 100)
        self.assertEqual(r.filled_shares, 0)
        self.assertEqual(r.unfilled_shares, 100)
        self.assertIn("error", r.cost_breakdown[0])

    def test_vwap_weighted(self):
        """VWAP 应按成交量加权"""
        book = OrderBook(
            asks=[
                PriceLevel(price=10.0, volume=500),
                PriceLevel(price=10.05, volume=500),
            ],
            last_price=10.0,
        )
        r = simulate_fill(book, "buy", 1000)
        # VWAP = (500*10 + 500*10.05)/1000 = 10.025
        self.assertAlmostEqual(r.vwap, 10.025, places=3)

    def test_estimate_slippage(self):
        book = make_book(mid=10.0)
        s = estimate_slippage(book, "buy", 500)
        self.assertGreaterEqual(s, 0)

    def test_to_dict(self):
        book = make_book()
        r = simulate_fill(book, "buy", 100)
        d = to_dict(r)
        self.assertIn("vwap", d)
        self.assertIn("slippage_bps", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
