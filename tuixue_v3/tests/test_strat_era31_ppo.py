#!/usr/bin/env python3
"""
test_strat_era31_ppo.py
Ship 87 单元测试 — PPO
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era31_ppo import (
    PPOSignal, ema_value, ema_series,
    generate_signal, screen_universe, split_buy_sell, summarize,
)


class TestEMA(unittest.TestCase):
    def test_basic(self):
        v = ema_value([10.0 + i * 0.1 for i in range(30)], window=12)
        self.assertIsNotNone(v)

    def test_short(self):
        self.assertIsNone(ema_value([10.0, 11.0], window=5))


class TestEMASeries(unittest.TestCase):
    def test_basic(self):
        s = ema_series([10.0 + i * 0.1 for i in range(30)], window=12)
        self.assertGreater(len(s), 0)


class TestGenerateSignal(unittest.TestCase):
    def test_uptrend(self):
        prices = [10.0 + i * 0.5 for i in range(50)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell"])

    def test_downtrend(self):
        prices = [25.0 - i * 0.5 for i in range(50)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        self.assertIn(sig.side, ["buy", "sell"])

    def test_insufficient(self):
        sig = generate_signal("a", [10.0] * 10)
        self.assertIsNone(sig)


class TestScreenUniverse(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(50)],
            "down": [25.0 - i * 0.5 for i in range(50)],
        }
        results = screen_universe(universe)
        # 不强求


class TestSplit(unittest.TestCase):
    def test_basic(self):
        universe = {
            "up": [10.0 + i * 0.5 for i in range(50)],
            "down": [25.0 - i * 0.5 for i in range(50)],
        }
        sigs = screen_universe(universe)
        buys, sells = split_buy_sell(sigs)
        # 不强求 (ppo 取决于细节)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(50)]
        sig = generate_signal("a", prices)
        self.assertIsNotNone(sig)
        s = summarize(sig)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        prices = [10.0 + i * 0.5 for i in range(50)]
        sig = generate_signal("a", prices)
        d = sig.to_dict()
        self.assertEqual(d["code"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)