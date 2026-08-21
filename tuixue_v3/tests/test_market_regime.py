#!/usr/bin/env python3
"""
test_market_regime.py
Ship 17 单元测试 — 市场状态识别 (Bull/Bear/Range/Crisis)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.market_regime import (
    detect_regime, get_position_factor, describe,
    POSITION_FACTOR, _sma, _pct_change, _amplitude, _volume_trend,
)


def gen_uptrend(n=60, start=100, daily=0.005, vol=None):
    """持续上涨"""
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + daily))
    volumes = vol or [1e6] * n
    return prices, volumes


def gen_downtrend(n=60, start=100, daily=-0.005):
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + daily))
    return prices, [1e6] * n


def gen_range(n=60, start=100, amp=0.03):
    """窄幅震荡"""
    import math
    prices = [start]
    for i in range(n - 1):
        prices.append(start + amp * start * math.sin(i * 0.5))
    return prices, [1e6] * n


def gen_crisis(n=60, start=100):
    """先涨后暴跌"""
    prices = [start]
    for i in range(n - 1):
        if i < n - 5:
            prices.append(prices[-1] * 1.001)
        else:
            prices.append(prices[-1] * 0.96)  # 连日暴跌
    return prices, [2e6] * n


class TestHelpers(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(_sma([1, 2, 3, 4, 5], 3), 4.0)
        self.assertIsNone(_sma([1, 2], 5))
        self.assertIsNone(_sma([1, 2, 3], 0))

    def test_pct_change(self):
        self.assertAlmostEqual(_pct_change([100, 110], 1), 0.1, places=4)
        self.assertIsNone(_pct_change([100], 5))
        self.assertIsNone(_pct_change([0, 100], 1))

    def test_amplitude(self):
        # [100, 110, 90] → amp = (110-90)/90 = 0.222
        self.assertAlmostEqual(_amplitude([100, 110, 90], 3), 0.222, places=2)
        self.assertIsNone(_amplitude([100], 5))

    def test_volume_trend(self):
        # 前 5 平均 1M, 后 5 平均 2M → trend = 1.0
        v = [1e6] * 5 + [2e6] * 5
        self.assertAlmostEqual(_volume_trend(v, 5), 1.0, places=4)
        self.assertIsNone(_volume_trend([1e6] * 3, 5))


class TestDetectRegime(unittest.TestCase):
    def test_insufficient_data(self):
        r = detect_regime([100, 101, 102])
        self.assertEqual(r.regime, "unknown")
        self.assertEqual(r.position_factor, 0.5)

    def test_bull(self):
        prices, volumes = gen_uptrend(n=60, daily=0.008)  # ~50% 上涨
        r = detect_regime(prices, volumes)
        print(f"  bull: regime={r.regime} conf={r.confidence} pf={r.position_factor}")
        self.assertEqual(r.regime, "bull")
        self.assertEqual(r.position_factor, 1.0)
        self.assertGreater(r.confidence, 0.5)

    def test_bear(self):
        prices, volumes = gen_downtrend(n=60, daily=-0.008)
        r = detect_regime(prices, volumes)
        print(f"  bear: regime={r.regime} conf={r.confidence}")
        self.assertEqual(r.regime, "bear")
        self.assertEqual(r.position_factor, 0.3)

    def test_range(self):
        prices, volumes = gen_range(n=60, amp=0.02)
        r = detect_regime(prices, volumes)
        print(f"  range: regime={r.regime} conf={r.confidence} amp={r.metrics.get('amp20_pct')}")
        self.assertEqual(r.regime, "range")
        self.assertEqual(r.position_factor, 0.6)

    def test_crisis_5d_drop(self):
        prices, volumes = gen_crisis(n=60)
        r = detect_regime(prices, volumes)
        print(f"  crisis: regime={r.regime} chg5={r.metrics.get('chg5_pct')}")
        self.assertEqual(r.regime, "crisis")
        self.assertEqual(r.position_factor, 0.1)

    def test_crisis_1d_drop(self):
        # 平稳 19 天, 第 20 天 -5%
        prices = [100] * 19 + [95]
        r = detect_regime(prices)
        print(f"  crisis_1d: regime={r.regime} chg1={r.metrics.get('chg1_pct')}")
        self.assertEqual(r.regime, "crisis")

    def test_no_volume(self):
        """无 volume 数据也能跑"""
        prices, _ = gen_uptrend(n=60)
        r = detect_regime(prices)  # 没 volume
        self.assertIn(r.regime, ("bull", "unknown"))

    def test_partial_data_60_to_30(self):
        """30 数据点 → 用 20 日均线, 不够 60 日"""
        prices, volumes = gen_uptrend(n=30, daily=0.008)
        r = detect_regime(prices, volumes)
        # 没 60 日数据, 用 20 日 chg + 5 日量
        self.assertIn(r.regime, ("bull", "unknown", "range"))

    def test_position_factor_lookup(self):
        self.assertEqual(get_position_factor("bull"), 1.0)
        self.assertEqual(get_position_factor("range"), 0.6)
        self.assertEqual(get_position_factor("bear"), 0.3)
        self.assertEqual(get_position_factor("crisis"), 0.1)
        self.assertEqual(get_position_factor("unknown"), 0.5)
        self.assertEqual(get_position_factor("nonsense"), 0.5)

    def test_describe(self):
        self.assertIn("上升", describe("bull"))
        self.assertIn("震荡", describe("range"))
        self.assertIn("下跌", describe("bear"))
        self.assertIn("危机", describe("crisis"))

    def test_reasons_nonempty(self):
        prices, _ = gen_uptrend(n=60)
        r = detect_regime(prices)
        self.assertGreater(len(r.reasons), 0)

    def test_metrics_dict(self):
        prices, volumes = gen_uptrend(n=60)
        r = detect_regime(prices, volumes)
        self.assertIn("ma20", r.metrics)
        self.assertIn("chg20_pct", r.metrics)


if __name__ == "__main__":
    unittest.main(verbosity=2)
