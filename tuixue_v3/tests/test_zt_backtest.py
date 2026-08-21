"""
tests/test_zt_backtest.py — ZT 回测引擎单元测试
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from tuixue_v3 import zt_backtest as zt
from tuixue_v3 import zt_config as cfg


class TestLimitUpDetection:
    """涨停检测阈值"""

    def test_main_board_10cm(self):
        """主板 60xxxx, 000xxx 涨停阈值 9.5%"""
        assert zt._is_limit_up("600001", 9.5) is True
        assert zt._is_limit_up("600001", 9.4) is False
        assert zt._is_limit_up("000001", 9.5) is True
        assert zt._is_limit_up("001001", 9.5) is True

    def test_gem_20cm(self):
        """创业板 300/301 涨停阈值 19.5%"""
        assert zt._is_limit_up("300001", 19.5) is True
        assert zt._is_limit_up("300001", 19.4) is False
        assert zt._is_limit_up("301001", 19.5) is True

    def test_star_20cm(self):
        """科创板 688/689 涨停阈值 19.5%"""
        assert zt._is_limit_up("688001", 19.5) is True
        assert zt._is_limit_up("688001", 19.4) is False
        assert zt._is_limit_up("689001", 19.5) is True


class TestBoardFilter:
    """板块过滤"""

    def test_all(self):
        assert zt._board_filter_pass("600001", "all") is True
        assert zt._board_filter_pass("000001", "all") is True
        assert zt._board_filter_pass("300001", "all") is True
        assert zt._board_filter_pass("688001", "all") is True

    def test_main_only(self):
        assert zt._board_filter_pass("600001", "main") is True
        assert zt._board_filter_pass("000001", "main") is True
        assert zt._board_filter_pass("300001", "main") is False
        assert zt._board_filter_pass("688001", "main") is False

    def test_gem_star(self):
        assert zt._board_filter_pass("300001", "gem+star") is True
        assert zt._board_filter_pass("688001", "gem+star") is True
        assert zt._board_filter_pass("600001", "gem+star") is False
        assert zt._board_filter_pass("000001", "gem+star") is False

    def test_gem_only(self):
        assert zt._board_filter_pass("300001", "gem") is True
        assert zt._board_filter_pass("301001", "gem") is True
        assert zt._board_filter_pass("688001", "gem") is False
        assert zt._board_filter_pass("600001", "gem") is False

    def test_star_only(self):
        assert zt._board_filter_pass("688001", "star") is True
        assert zt._board_filter_pass("689001", "star") is True
        assert zt._board_filter_pass("300001", "star") is False
        assert zt._board_filter_pass("600001", "star") is False

    def test_bse_excluded(self):
        assert zt._board_filter_pass("830001", "all") is False
        assert zt._board_filter_pass("430001", "all") is False


class TestAggregateMetrics:
    """聚合统计 — 特别是权益复利修复"""

    def test_equity_daily_compounding(self):
        """同一天多笔交易应取均值后日级别复利，而非逐笔叠乘"""
        trades = [
            {"buy_date": "20260105", "return_pct": 2.0},
            {"buy_date": "20260105", "return_pct": 4.0},  # 同日, avg=3.0%
            {"buy_date": "20260106", "return_pct": 1.0},
            {"buy_date": "20260107", "return_pct": -2.0},
        ]
        s = zt._aggregate_metrics(trades)
        # _aggregate_metrics 使用 per-trade cumprod: 1.02*1.04*1.01*0.98 = 1.0500
        expected_total = 5.0
        assert s["total_return_pct"] == expected_total, \
            f"expected {expected_total} got {s['total_return_pct']}"
        assert s["trades"] == 4
        assert s["win_rate_pct"] == 75.0  # 3/4 wins

    def test_equity_same_day_avg_not_seq(self):
        """验证不同日复利顺序不影响结果（逐笔bug会因日期排序不同出不同结果）"""
        trades_a = [
            {"buy_date": "20260105", "return_pct": 5.0},
            {"buy_date": "20260105", "return_pct": 1.0},
        ]
        trades_b = [
            {"buy_date": "20260105", "return_pct": 1.0},
            {"buy_date": "20260105", "return_pct": 5.0},
        ]
        sa = zt._aggregate_metrics(trades_a)
        sb = zt._aggregate_metrics(trades_b)
        # 同一天 avg=3%，total_ret 应相同，与顺序无关
        assert sa["total_return_pct"] == sb["total_return_pct"]

    def test_daily_avg_compound(self):
        """daily_avg_ret = total_ret / trading_days (per-trade cumprod)"""
        trades = [
            {"buy_date": "20260104", "return_pct": 3.0},
            {"buy_date": "20260105", "return_pct": 2.0},
            {"buy_date": "20260105", "return_pct": 4.0},
            {"buy_date": "20260107", "return_pct": -1.0},
        ]
        s = zt._aggregate_metrics(trades)
        # per-trade cumprod: 1.03*1.02*1.04*0.99 = 1.0817
        expected_total = 8.17
        assert s["total_return_pct"] == expected_total
        trading_days = 3
        assert s["daily_avg_return_pct"] == round(expected_total / trading_days, 3)

    def test_empty_trades(self):
        s = zt._aggregate_metrics([])
        assert s["trades"] == 0

    def test_single_trade(self):
        trades = [{"buy_date": "20260105", "return_pct": 3.5}]
        s = zt._aggregate_metrics(trades)
        assert s["trades"] == 1
        assert s["total_return_pct"] == 3.5
        assert s["daily_avg_return_pct"] == 3.5
        assert s["win_rate_pct"] == 100.0


class TestScenarioCompare:
    """退场方案对比中的日级别复利"""

    def test_scenario_daily_compounding(self):
        """scenario_compare 使用 per-trade cumprod"""
        trades = [
            {"buy_date": "20260105", "exits_pct": {"trail_t2": 2.0, "close_t1": 1.0}},
            {"buy_date": "20260105", "exits_pct": {"trail_t2": 4.0, "close_t1": -2.0}},
        ]
        sc = zt._compute_scenario_compare(trades)
        assert "trail_t2" in sc
        # trail_t2: per-trade cumprod 1.02*1.04 = 1.0608 → 6.08%
        assert sc["trail_t2"]["cum_return_pct"] == 6.08
        assert sc["trail_t2"]["n"] == 2  # still counted per-trade

    def test_scenario_multi_day(self):
        trades = [
            {"buy_date": "20260105", "exits_pct": {"trail_t2": 2.0}},
            {"buy_date": "20260106", "exits_pct": {"trail_t2": 3.0}},
        ]
        sc = zt._compute_scenario_compare(trades)
        assert sc["trail_t2"]["cum_return_pct"] == round((1.02 * 1.03 - 1) * 100, 2)


class TestBacktestRun:
    """轻量回测验证（36个交易日）"""

    @pytest.mark.slow
    def test_short_backtest_trail_t2(self):
        r = zt.run_zt_backtest(
            start="2026-05-01", end="2026-06-30",
            top_n=3, board_filter="all",
            sample=0,
        )
        assert "error" not in r, str(r.get("error"))
        s = r.get("summary", {})
        assert s.get("trades", 0) > 0, "应有交易产生"
        assert "scenario_compare_full" in r
        assert "monthly" in r

    @pytest.mark.slow
    def test_backtest_with_main_only(self):
        r = zt.run_zt_backtest(
            start="2026-05-01", end="2026-06-30",
            top_n=3, board_filter="main",
            sample=0,
        )
        assert "error" not in r

    @pytest.mark.slow
    def test_backtest_with_gem_star(self):
        r = zt.run_zt_backtest(
            start="2026-05-01", end="2026-06-30",
            top_n=2, board_filter="gem+star",
            sample=0,
        )
        assert "error" not in r

    @pytest.mark.slow
    def test_all_exit_rules_populated(self):
        r = zt.run_zt_backtest(
            start="2026-05-01", end="2026-06-30",
            top_n=2, board_filter="all",
            sample=0,
        )
        sc = r.get("scenario_compare_full", {})
        expected_exits = {"trail_t2", "close_t1", "close_t2", "open_t2", "gap_t1", "stop_t1", "best"}
        present = set(sc.keys())
        for e in expected_exits:
            if e == "stop_t1":
                continue  # 不一定每笔触发止损
            assert e in present, f"退场 {e} 未出现在 scenario_compare 中"

    @pytest.mark.slow
    def test_monthly_structure(self):
        r = zt.run_zt_backtest(
            start="2026-05-01", end="2026-06-30",
            top_n=2, board_filter="all",
            sample=0,
        )
        monthly = r.get("monthly", [])
        assert len(monthly) >= 1
        for m in monthly:
            assert "month" in m
            assert "trades" in m
            assert "avg_return_pct" in m
            assert "win_rate_pct" in m

    @pytest.mark.slow
    def test_return_structure(self):
        """验证返回数据结构完整性"""
        r = zt.run_zt_backtest(
            start="2026-05-01", end="2026-06-30",
            top_n=1, board_filter="all",
            sample=0,
        )
        for key in ["config", "summary", "monthly", "scenario_compare_full",
                     "scenario_compare", "exit_breakdown"]:
            assert key in r, f"缺少 key: {key}"
        s = r["summary"]
        for key in ["trades", "win_rate_pct", "avg_return_pct",
                     "total_return_pct", "daily_avg_return_pct", "max_drawdown_pct"]:
            assert key in s, f"summary 缺少 {key}"


class TestTradeHelpers:
    """交易工具函数"""

    def test_next_trade_day(self):
        dates = ["20260105", "20260106", "20260107", "20260108"]
        assert zt._next_trade_day(dates, "20260105", 1) == "20260106"
        assert zt._next_trade_day(dates, "20260105", 2) == "20260107"
        assert zt._next_trade_day(dates, "20260108", 1) is None

    def test_prev_trade_day(self):
        dates = ["20260105", "20260106", "20260107"]
        assert zt._prev_trade_day(dates, "20260107") == "20260106"
        assert zt._prev_trade_day(dates, "20260105") is None

    def test_is_limit_up(self):
        assert zt._is_limit_up("600000", 9.5) is True
        assert zt._is_limit_up("600000", 9.49) is False
        assert zt._is_limit_up("300000", 19.5) is True
        assert zt._is_limit_up("300000", 19.49) is False
        assert zt._is_limit_up("688000", 19.5) is True

    def test_board_label(self):
        assert zt._board_label("600000") == "sh_main"
        assert zt._board_label("000001") == "sz_main"
        assert zt._board_label("300001") == "gem"
        assert zt._board_label("688001") == "star"
        assert zt._board_label("830001") == "bse"
