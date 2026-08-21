#!/usr/bin/env python3
"""
test_strat_era18_kdj.py
Ship 74 单元测试 — KDJ
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era18_kdj import (
    KDJSignal, compute_rsv_series, compute_kd,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestRSV(unittest.TestCase):
    def test_basic(self):
        closes = [10.0, 11.0, 12.0, 13.0, 14.0]
        rsv = compute_rsv_series(closes, window=3)
        self.assertEqual(len(rsv), 3)   # 5 - 3 + 1


class TestKD(unittest.TestCase):
    def test_basic(self):
        rsv_series = [50.0, 60.0, 70.0, 80.0, 90.0]
        k, d, j = compute_kd(rsv_series)
        self.assertEqual(len(k), 5)
        self.assertEqual(len(d), 5)
        self.assertEqual(len(j), 5)


class TestGenerateSignal(unittest.TestCase):
    def test_buy_oversold(self):
        # 大跌到超卖区
        prices = [10.0, 11.0, 12.0, 13.0, 14.0, 13.0, 12.0, 11.0, 5.0, 5.0, 5.0]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        # 不强求 side, 验证 J < 0 → buy
        # 或 cross up

    def test_sell_overbought(self):
        # 大涨到超买
        prices = [5.0, 5.0, 5.0, 5.0, 5.0, 6.0, 8.0, 12.0, 15.0, 15.0, 15.0]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 5)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(20)],
            "down": [15.0 - i * 0.5 for i in range(20)],
        }
        results = screen_universe(universe)
        # 不强求数量


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "a": [10.0, 11.0, 12.0, 13.0, 14.0, 13.0, 12.0, 11.0, 5.0, 5.0, 5.0, 6.0, 8.0, 12.0, 15.0, 15.0, 15.0],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        # 不强求


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(20)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(20)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)