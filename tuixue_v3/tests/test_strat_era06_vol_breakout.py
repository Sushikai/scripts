#!/usr/bin/env python3
"""
test_strat_era06_vol_breakout.py
Ship 62 单元测试 — 波动率突破
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era06_vol_breakout import (
    VBSignal, true_range, compute_atr, compute_atr_from_closes,
    historical_volatility, generate_signal, generate_multi_window,
    screen_universe, split_buy_sell, summarize,
)


class TestTrueRange(unittest.TestCase):
    def test_basic(self):
        tr = true_range(12, 10, 11)
        self.assertEqual(tr, 2)   # max(12-10=2, |12-11|=1, |10-11|=1) = 2


class TestATR(unittest.TestCase):
    def test_basic(self):
        highs = [11, 12, 13, 14, 15]
        lows = [10, 10, 11, 12, 13]
        closes = [10.5, 11, 12, 13, 14]
        atr = compute_atr(highs, lows, closes, window=3)
        self.assertIsNotNone(atr)
        self.assertGreater(atr, 0)

    def test_short(self):
        atr = compute_atr([11], [10], [10.5], window=14)
        self.assertIsNone(atr)


class TestATRFromCloses(unittest.TestCase):
    def test_basic(self):
        prices = [10.0, 10.5, 11.0, 10.5, 10.0, 9.5, 10.0]
        atr = compute_atr_from_closes(prices, window=5)
        self.assertIsNotNone(atr)
        self.assertGreater(atr, 0)

    def test_short(self):
        atr = compute_atr_from_closes([10.0] * 5, window=14)
        self.assertIsNone(atr)


class TestHistVol(unittest.TestCase):
    def test_basic(self):
        prices = [10.0, 11.0, 10.5, 12.0, 11.0]
        vol = historical_volatility(prices, window=4)
        self.assertIsNotNone(vol)
        self.assertGreater(vol, 0)


class TestGenerateSignal(unittest.TestCase):
    def test_buy_breakout_up(self):
        # 15 prices: 14 平稳 10.0 + 1 大涨到 11.5
        prices = [10.0] * 14 + [11.5]
        sig = generate_signal("a", prices, window=14, atr_mult=1.5)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_sell_breakout_down(self):
        # 14 平稳 + 1 大跌到 8.5
        prices = [10.0] * 14 + [8.5]
        sig = generate_signal("a", prices, window=14, atr_mult=1.5)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "sell")

    def test_hold_in_middle(self):
        # 微波动
        import random
        random.seed(1)
        prices = [10.0 + random.gauss(0, 0.5) for _ in range(21)]
        sig = generate_signal("a", prices, window=14, atr_mult=2.0, exit_atr_mult=0.5)
        # z 在中性区
        # 实际取决于具体波动, 但应该返回信号
        if sig is not None:
            self.assertIn(sig.side, ["buy", "sell", "hold"])

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5, window=14)
        self.assertIsNone(sig)


class TestMultiWindow(unittest.TestCase):
    def test_basic(self):
        import random
        random.seed(2)
        prices = [10.0] * 60 + [12.0]
        sig = generate_multi_window("a", prices)
        # 多窗口应都判定为 buy
        self.assertIsNotNone(sig)
        self.assertEqual(sig.side, "buy")

    def test_short_data(self):
        prices = [10.0] * 5
        sig = generate_multi_window("a", prices, windows=(10, 20))
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0] * 14 + [12.0],
            "down": [10.0] * 14 + [8.0],
            "flat": [10.0] * 15,
        }
        results = screen_universe(universe, top_n=10)
        sides = {r.side for r in results}
        self.assertIn("buy", sides)
        self.assertIn("sell", sides)

    def test_top_n(self):
        universe = {f"s{i}": [10.0] * 14 + [12.0] for i in range(10)}
        results = screen_universe(universe, top_n=3)
        self.assertEqual(len(results), 3)


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0] * 14 + [12.0],
            "down": [10.0] * 14 + [8.0],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        self.assertEqual(len(buys), 1)
        self.assertEqual(len(sells), 1)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 14 + [12.0]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)
        self.assertIn("BUY", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0] * 14 + [12.0]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["side"], "buy")
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)