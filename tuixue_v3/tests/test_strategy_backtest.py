#!/usr/bin/env python3
"""
test_strategy_backtest.py
Ship 16 单元测试 — 策略回测 (factor → pick → buy → sell → stats)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strategy_backtest import (
    BacktestTrade, BacktestResult,
    run_strategy_backtest,
    _compute_max_drawdown, _compute_sharpe,
)


def make_synthetic_data(n_dates=20, n_codes=5, seed=42):
    """合成 20 日 5 只股票的 close 数据 + 因子"""
    import random
    rng = random.Random(seed)
    base = "2026-07-01"
    dates = []
    for i in range(n_dates):
        d = f"{base[:4]}-{int(base[5:7]) + i // 22:02d}-{int(base[8:10]) + i % 22:02d}"
        dates.append(d)
    # 简化: 用索引生成日期
    dates = [f"2026-07-{i + 1:02d}" for i in range(n_dates)]
    # 5 只股票价格: 随机游走 [8, 12]
    codes = [f"S{i:04d}" for i in range(n_codes)]
    kline = {}
    for c in codes:
        kline[c] = {}
        px = 10.0
        for d in dates:
            px *= (1 + rng.uniform(-0.03, 0.03))
            kline[c][d] = round(px, 2)
    # 因子: 用一个伪函数, 越靠前分数越高 (rank 1 最高)
    def factor_provider(date, code):
        idx = codes.index(code)
        return 0.5 - idx * 0.1
    # K 线查表 callable
    def kline_lookup(code):
        return kline.get(code, {})
    # 候选: 每天所有 5 只
    candidates = {d: list(codes) for d in dates}
    return dates, candidates, factor_provider, kline_lookup, codes


class TestMaxDrawdown(unittest.TestCase):
    def test_no_dd(self):
        self.assertEqual(_compute_max_drawdown([100, 110, 120]), 0.0)

    def test_basic(self):
        # peak=120, trough=90 → -25%
        self.assertAlmostEqual(_compute_max_drawdown([100, 120, 90, 110]), -0.25, places=4)

    def test_empty(self):
        self.assertEqual(_compute_max_drawdown([]), 0.0)


class TestSharpe(unittest.TestCase):
    def test_constant_return(self):
        # 每天 +1% → sharpe 高 (但 std=0 兜底)
        eq = [100 * (1.01 ** i) for i in range(20)]
        s = _compute_sharpe(eq)
        self.assertGreater(s, 0)

    def test_volatile(self):
        eq = [100, 110, 90, 105, 95, 115, 100]
        s = _compute_sharpe(eq)
        self.assertIsInstance(s, float)

    def test_too_short(self):
        self.assertEqual(_compute_sharpe([100]), 0.0)


class TestRunStrategyBacktest(unittest.TestCase):
    def test_basic_run(self):
        dates, cands, fp, kl, codes = make_synthetic_data(n_dates=20, n_codes=5)
        r = run_strategy_backtest(
            trade_dates=dates,
            candidates_by_date=cands,
            factor_provider=fp,
            kline_lookup=kl,
            initial_capital=100000,
            hold_days=3,
            max_picks_per_day=3,
        )
        print(r.summary())
        self.assertGreater(r.n_trades, 0)
        self.assertEqual(r.initial_capital, 100000)
        self.assertGreater(r.final_capital, 0)

    def test_factor_skip(self):
        """因子抛异常 → 跳过"""
        dates, cands, fp, kl, codes = make_synthetic_data()
        def bad_factor(date, code):
            if code == "S0001":
                raise ValueError("simulated fail")
            return 0.5
        r = run_strategy_backtest(
            trade_dates=dates, candidates_by_date=cands,
            factor_provider=bad_factor, kline_lookup=kl,
        )
        # S0001 不应出现在任何 trade 里
        for t in r.trades:
            self.assertNotEqual(t.code, "S0001")

    def test_hold_days_respected(self):
        dates, cands, fp, kl, codes = make_synthetic_data(n_dates=30)
        r = run_strategy_backtest(
            trade_dates=dates, candidates_by_date=cands,
            factor_provider=fp, kline_lookup=kl, hold_days=5,
        )
        # 至少找到一个 hold ≥ 5 的 trade (排除尾部被 clamp 的)
        held_long = [t for t in r.trades
                     if dates.index(t.sell_date) - dates.index(t.buy_date) >= 5]
        # 至少大部分 trade 应当满足 hold (允许尾部 clamp 缩短)
        if r.trades:
            pct_long = len(held_long) / len(r.trades)
            self.assertGreater(pct_long, 0.5,
                              f"只有 {pct_long:.0%} trades 满足 hold≥5")

    def test_max_picks(self):
        dates, cands, fp, kl, codes = make_synthetic_data(n_dates=10)
        r = run_strategy_backtest(
            trade_dates=dates, candidates_by_date=cands,
            factor_provider=fp, kline_lookup=kl, max_picks_per_day=2,
        )
        # 每天最多 2 笔新买 (但可能 < 2 因资金/持仓冲突)
        # 简单校验: 任何 trade 的 rank <= 2
        for t in r.trades:
            self.assertLessEqual(t.rank, 2)

    def test_zero_candidates(self):
        dates, _, fp, kl, _ = make_synthetic_data()
        r = run_strategy_backtest(
            trade_dates=dates, candidates_by_date={},
            factor_provider=fp, kline_lookup=kl,
        )
        self.assertEqual(r.n_trades, 0)
        self.assertEqual(r.final_capital, r.initial_capital)

    def test_metrics_reasonable(self):
        dates, cands, fp, kl, _ = make_synthetic_data(n_dates=50, n_codes=10)
        r = run_strategy_backtest(
            trade_dates=dates, candidates_by_date=cands,
            factor_provider=fp, kline_lookup=kl, hold_days=5,
        )
        # Sharpe 应当是有限 float
        self.assertFalse(math.isnan(r.sharpe))
        self.assertFalse(math.isnan(r.annualized))
        self.assertGreaterEqual(r.max_drawdown, -1.0)
        self.assertLessEqual(r.max_drawdown, 0.0)

    def test_risk_distribution(self):
        dates, cands, fp, kl, _ = make_synthetic_data()
        r = run_strategy_backtest(
            trade_dates=dates, candidates_by_date=cands,
            factor_provider=fp, kline_lookup=kl,
        )
        self.assertIn("ok", r.risk_distribution)
        # 至少记录了一些 ok
        self.assertGreaterEqual(r.risk_distribution["ok"], 0)

    def test_monthly_breakdown(self):
        dates, cands, fp, kl, _ = make_synthetic_data(n_dates=40)
        r = run_strategy_backtest(
            trade_dates=dates, candidates_by_date=cands,
            factor_provider=fp, kline_lookup=kl,
        )
        # 月度至少 1 个 bucket (都是 2026-07)
        self.assertGreater(len(r.monthly), 0)

    def test_commission_cost(self):
        """高手续费 → 净收益应略低于毛收益"""
        dates, cands, fp, kl, _ = make_synthetic_data(n_dates=30)
        r_low = run_strategy_backtest(
            trade_dates=dates, candidates_by_date=cands,
            factor_provider=fp, kline_lookup=kl, commission=0.0001,
        )
        r_high = run_strategy_backtest(
            trade_dates=dates, candidates_by_date=cands,
            factor_provider=fp, kline_lookup=kl, commission=0.01,  # 1% 单边
        )
        # 高手续费的 net_ret 平均应更低
        if r_low.trades and r_high.trades:
            avg_low = sum(t.net_ret for t in r_low.trades) / len(r_low.trades)
            avg_high = sum(t.net_ret for t in r_high.trades) / len(r_high.trades)
            self.assertLess(avg_high, avg_low)


import math

if __name__ == "__main__":
    unittest.main(verbosity=2)
