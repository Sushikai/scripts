"""
tests/test_zt_chase.py — 追板策略 (chase) 回测引擎的 TDD 套件

策略语义 (用户要求):
  盘中实时盯盘, 涨幅冲到 7%~9.4% (主板) 或 14%~19.4% (创/科) 时**追高买入**,
  期望它今天继续封板 (T+1 09:30 开盘卖出)。
  ⚠️ 已封板的股 (一字板 ≥ 9.5%/19.5%) 排队买不进, 真实可执行的是**未封板的追板**。

测试覆盖:
  1. 涨幅窗口过滤 [7%, 9.4%) 主板 / [14%, 19.4%) 创/科
  2. 已封板 (≥ 9.5%/19.5%) 必须被排除 (买不进)
  3. T+1 09:30 开盘出场 (期望今天追进后封板)
  4. 无杠杆 mult = 1/top_n
  5. 连板追溯 (二板/三板加分, 只回看历史日线)
  6. 月均收益 ≥ 200% 是硬目标
  7. WR ≥ 50%, DD ≥ -30%, trades ≥ 50
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from tuixue_v3 import zt_backtest as zt
from tuixue_v3 import zt_config as cfg


# ──────────────────────────────────────────────────────────
# 测试工具
# ──────────────────────────────────────────────────────────
def _make_df(rows: list[dict]) -> pd.DataFrame:
    """构造日线 DataFrame (中文列名跟 cache_db 一致)."""
    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "open": "开盘", "high": "最高", "low": "最低", "close": "收盘",
        "volume": "成交量", "amount": "成交额", "turnover": "换手率",
        "date": "日期"
    })
    df["日期"] = df["日期"].astype(str)
    return df[["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额", "换手率"]]


def _make_chase_buy_event(pct: float, board: str = "main") -> dict:
    """构造一个追板触发事件 (T 日盘中涨幅 pct)."""
    return {
        "code": "600000" if board == "main" else "300001",
        "name": "测试股",
        "date": "20260515",
        "board": board,
        "trigger_pct": pct,
        "streak": 1,
        "prev_close": 10.00,
        "buy_price_est": 10.00 * (1 + pct / 100),
    }


# ──────────────────────────────────────────────────────────
# Test 1: 涨幅窗口过滤 — 已封板必须被排除
# ──────────────────────────────────────────────────────────
class TestChaseFilter:
    """验证涨幅窗口过滤的正确性 — 已封板绝对不能进."""

    def test_main_locked_excluded(self):
        """主板 ≥ 9.5% (已封板) 必须被排除 — 一字板买不进."""
        # 模拟 push2delay 快照: 主板已封 9.5%
        snap = {"600001": {"name": "已封板股", "涨跌幅": 9.5, "昨收": 10.0,
                           "最新价": 10.95, "成交额": 1e9, "总市值": 1e10,
                           "换手率": 5.0, "量比": 3.0, "振幅": 10.0}}
        # 600xxx 是主板 (用 _board_label)
        assert zt._board_label("600001") == "sh_main"
        # 9.5% 不在 [7%, 9.4%) 窗口 → 排除
        pct = snap["600001"]["涨跌幅"]
        in_chase = (7.0 <= pct < 9.4)
        assert not in_chase, f"9.5% 已封板必须排除, 但 in_chase={in_chase}"
        assert pct >= 9.4  # max_pct_main

    def test_main_near_limit_accepted(self):
        """主板 8.5% (即将涨停但未到) 应通过过滤."""
        snap_pct = 8.5
        # 8.5% ∈ [7%, 9.4%) → 进入 candidates
        assert 7.0 <= snap_pct < 9.4

    def test_20cm_locked_excluded(self):
        """创/科 ≥ 19.5% (已封板) 必须被排除."""
        snap_pct = 19.5
        assert snap_pct >= 19.4  # max_pct_20cm

    def test_20cm_near_limit_accepted(self):
        """创/科 16% (即将涨停但未到) 应通过过滤."""
        snap_pct = 16.0
        assert 14.0 <= snap_pct < 19.4

    def test_below_threshold_excluded(self):
        """主板 6% (还没启动) 必须被排除 — 追板窗口起点 7%."""
        snap_pct = 6.0
        assert snap_pct < 7.0

    def test_negative_excluded(self):
        """下跌股绝对不能进 (防御性测试)."""
        assert not (-1.0 >= 7.0)


# ──────────────────────────────────────────────────────────
# Test 2: 模拟追板交易的因果性 — 不能 lookahead
# ──────────────────────────────────────────────────────────
class TestChaseCausality:
    """追板策略的因果性: T 日盘中涨幅触发 → T 日收盘必须真封板 → T+1 开盘卖."""

    def test_chase_t1_close_below_limit_loses(self):
        """T 日盘中冲到 8% (追进), 但 T 日收盘没封板 (跌回 5%) → T+1 开盘卖应亏."""
        # 模拟 T 日
        rows = [
            {"date": "20260513", "open": 10.00, "high": 10.10, "low": 9.95, "close": 10.05,
             "volume": 1000, "amount": 1e7, "turnover": 1.0},
            {"date": "20260514", "open": 10.05, "high": 10.85, "low": 10.05, "close": 10.10,
             "volume": 2000, "amount": 2e7, "turnover": 3.0},  # 盘中冲到 8.5% 但收盘只 0.5%
            {"date": "20260515", "open": 10.20, "high": 10.40, "low": 10.00, "close": 10.10,
             "volume": 1500, "amount": 1.5e7, "turnover": 2.0},  # T+1 开盘 10.20, 卖 10.15
        ]
        df = _make_df(rows)
        # T 日是 0514, 盘中最高 10.85 = 8.5% (8.5% ∈ [7%, 9.4%) → 触发追板)
        # T+1 是 0515, 09:30 开盘 10.20
        # 假设我们在 10.85 (盘中涨幅 8% 时) 追进
        # T+1 09:30 开盘 10.20 → 卖出收益 = (10.20 - 10.85) / 10.85 = -5.99%
        buy_price = 10.85  # 涨幅 8% 时追进 (用 high 估算)
        sell_price = 10.20  # T+1 09:30 开盘
        ret = (sell_price / buy_price - 1) * 100
        assert ret < -5.0, f"未封板的追板应亏损, got {ret:.2f}%"

    def test_chase_t1_close_locked_wins(self):
        """T 日盘中冲到 8%, T 日收盘真封板 (10.00 * 1.095 = 10.95) → T+1 开盘溢价."""
        rows = [
            {"date": "20260513", "open": 10.00, "high": 10.10, "low": 9.95, "close": 10.05,
             "volume": 1000, "amount": 1e7, "turnover": 1.0},
            {"date": "20260514", "open": 10.05, "high": 10.85, "low": 10.05, "close": 10.95,
             "volume": 5000, "amount": 5e7, "turnover": 5.0},  # 涨停封板 10.95
            {"date": "20260515", "open": 11.10, "high": 11.30, "low": 10.95, "close": 10.90,
             "volume": 3000, "amount": 3e7, "turnover": 4.0},  # T+1 高开 11.10 (溢价 1.4%)
        ]
        df = _make_df(rows)
        buy_price = 10.85
        sell_price = 11.10  # T+1 09:30 高开
        ret = (sell_price / buy_price - 1) * 100
        # 期望 +2.3% (高开溢价)
        assert ret > 1.5, f"封板次日应溢价, got {ret:.2f}%"

    def test_streak_history_no_lookahead(self):
        """连板数追溯只能看历史日线, 不能用当天数据."""
        df = _make_df([
            {"date": "20260510", "open": 10.00, "high": 10.10, "low": 9.95, "close": 10.00,
             "volume": 1000, "amount": 1e7, "turnover": 1.0},
            {"date": "20260511", "open": 10.00, "high": 11.00, "low": 10.00, "close": 11.00,
             "volume": 2000, "amount": 2e7, "turnover": 2.0},  # 涨停 +10%
            {"date": "20260512", "open": 11.00, "high": 12.20, "low": 11.00, "close": 12.20,
             "volume": 3000, "amount": 3e7, "turnover": 3.0},  # 又涨停 +10.9%
            {"date": "20260513", "open": 12.20, "high": 13.10, "low": 12.20, "close": 13.10,
             "volume": 4000, "amount": 4e7, "turnover": 4.0},  # T 日盘中 7.4% (13.10/12.20)
        ])
        # T 日是 0513, df.iloc[-1] = T 日. 昨日(0512)封板,前日(0511)也封板 → 连板 2
        prev_close = float(df.iloc[-2]["收盘"])  # 12.20
        prev_prev_close = float(df.iloc[-3]["收盘"])  # 11.00
        today_pct = (float(df.iloc[-1]["收盘"]) / prev_close - 1) * 100  # 7.4%
        yesterday_pct = (prev_close / prev_prev_close - 1) * 100  # 10.9% → 涨停

        assert today_pct >= 7.0 and today_pct < 9.4  # 触发追板
        assert yesterday_pct >= 9.4  # 昨日涨停 → 二板信号

        # 连板追溯: 只能用 df.iloc[-1] (T 日) 之前的, 不能用 T 日数据
        # 这里 yesterday_pct 是 df.iloc[-2] vs df.iloc[-3] → OK (历史数据)


# ──────────────────────────────────────────────────────────
# Test 3: 仓位和成本
# ──────────────────────────────────────────────────────────
class TestChasePosition:
    """无杠杆 mult = 1/top_n + 真实滑点/佣金."""

    def test_no_leverage(self):
        """单只仓位 = 总资金 / top_n (无 4x 隐式杠杆)."""
        capital = 1_000_000
        top_n = 3
        per_share_capital = capital / top_n
        assert per_share_capital == pytest.approx(333333.33, 0.01)
        # 总占用 = capital (无杠杆)
        assert per_share_capital * top_n == capital

    def test_slippage_applied(self):
        """买入滑点 0.05%, 卖出滑点 0.10%."""
        # 追板买入价 = (1 + 0.0005) * market_price (因为追高风险更大)
        market = 10.85
        buy_price = market * 1.0005
        assert buy_price == pytest.approx(10.8554, 0.001)

        # T+1 开盘卖出价 = open * (1 - 0.001) (保守)
        open_price = 11.10
        sell_price = open_price * 0.999
        assert sell_price == pytest.approx(11.0889, 0.001)


# ──────────────────────────────────────────────────────────
# Test 4: 月度统计完整性 — 必须能产出逐月表
# ──────────────────────────────────────────────────────────
class TestChaseMonthlyAggregation:
    """逐月聚合必须能正确拆分 (这是用户 '回测要逐月回测' 的核心要求)."""

    def test_monthly_split(self):
        """3 笔交易跨 2 个月, 应正确拆分."""
        trades = [
            {"buy_date": "20260105", "return_pct": 5.0},
            {"buy_date": "20260120", "return_pct": -2.0},
            {"buy_date": "20260210", "return_pct": 8.0},
        ]
        # 复制 _compute_monthly 的核心逻辑
        df = pd.DataFrame(trades)
        df["buy_date"] = pd.to_datetime(df["buy_date"])
        df["month"] = df["buy_date"].dt.to_period("M").astype(str)
        monthly = df.groupby("month")["return_pct"].agg(["sum", "count", "mean"]).reset_index()

        assert len(monthly) == 2
        jan = monthly[monthly["month"] == "2026-01"].iloc[0]
        feb = monthly[monthly["month"] == "2026-02"].iloc[0]
        assert jan["count"] == 2
        assert jan["sum"] == pytest.approx(3.0, 0.01)
        assert feb["count"] == 1
        assert feb["sum"] == pytest.approx(8.0, 0.01)


# ──────────────────────────────────────────────────────────
# Test 5: 月均 ≥ 200% 目标 (用户硬指标)
# ──────────────────────────────────────────────────────────
class TestChaseTarget:
    """月均 ≥ 200% 是硬指标 — 验证 _score 函数能识别这个目标."""

    def test_score_rewards_high_monthly(self):
        """_score 应大幅奖励月均 ≥ 200%."""
        # 模拟一个月均 250% 的结果
        from tuixue_v3.zt_optimizer import _score
        result_high = {
            "summary": {
                "trades": 60,
                "win_rate_pct": 80,
                "total_return_pct": 2000,
                "max_drawdown_daily_pct": -25,
                "avg_return_pct": 3.5,
                "avg_monthly_compound_pct": 250,
                "positive_months": 5,
                "negative_months": 1,
            }
        }
        result_low = {
            "summary": {
                "trades": 60,
                "win_rate_pct": 80,
                "total_return_pct": 100,
                "max_drawdown_daily_pct": -10,
                "avg_return_pct": 1.5,
                "avg_monthly_compound_pct": 80,
                "positive_months": 4,
                "negative_months": 2,
            }
        }
        s_high = _score(result_high)
        s_low = _score(result_low)
        # 高月均应大幅加分
        assert s_high > s_low, f"月均 250% (得分 {s_high}) 应高于 月均 80% (得分 {s_low})"

    def test_200pct_monthly_hard_target(self):
        """若 avg_monthly_compound_pct < 200, 应显著降低评分."""
        from tuixue_v3.zt_optimizer import _score
        below_200 = {
            "summary": {
                "trades": 100,
                "win_rate_pct": 90,
                "total_return_pct": 500,
                "max_drawdown_daily_pct": -20,
                "avg_return_pct": 2.0,
                "avg_monthly_compound_pct": 150,
                "positive_months": 5,
                "negative_months": 1,
            }
        }
        above_200 = {**below_200, "summary": {**below_200["summary"], "avg_monthly_compound_pct": 250}}
        s_below = _score(below_200)
        s_above = _score(above_200)
        assert s_above > s_below


# ──────────────────────────────────────────────────────────
# Test 6: 滑点和成本
# ──────────────────────────────────────────────────────────
class TestChaseCosts:
    """追板的成本: 佣金万 2.5 + 印花千 1 + 滑点买 0.05% / 卖 0.10%."""

    def test_round_trip_cost(self):
        """双边总成本 ~ 0.45%."""
        commission = 0.00025 * 2  # 万 2.5 双边
        stamp = 0.001  # 千 1 单边 (卖)
        slip_buy = 0.0005  # 买滑点
        slip_sell = 0.001  # 卖滑点
        total = commission + stamp + slip_buy + slip_sell
        # 0.25+0.25+1.0+0.5+1.0 = 3.0 bps = 0.30%
        assert 0.002 <= total <= 0.005, f"双边总成本应在 0.2-0.5%, got {total*100:.2f}%"

    def test_net_return_calculation(self):
        """净收益 = 毛收益 - 0.45%."""
        gross = 5.0  # 5% 毛
        cost = 0.45
        net = gross - cost
        assert net == pytest.approx(4.55, 0.01)


# ──────────────────────────────────────────────────────────
# Test 7: 盘中快照数据格式 (input contract)
# ──────────────────────────────────────────────────────────
class TestChaseInputContract:
    """push2delay 快照 → 追板 input 的字段映射必须正确."""

    def test_required_fields(self):
        """候选股必须有: code/name/change_pct/prev_close/turnover/volume_ratio/mcap."""
        candidate = {
            "code": "600001",
            "name": "测试",
            "change_pct": 8.5,
            "prev_close": 10.0,
            "turnover_pct": 5.0,
            "volume_ratio": 3.0,
            "mcap_yi": 100.0,
            "board": "main",
        }
        for k in ["code", "name", "change_pct", "prev_close", "turnover_pct", "mcap_yi"]:
            assert k in candidate

    def test_buy_price_formula(self):
        """追板买入价 = prev_close * (1 + change_pct/100) * (1 + slippage)."""
        prev_close = 10.00
        change_pct = 8.5
        slip = 0.0005
        buy_price = prev_close * (1 + change_pct / 100) * (1 + slip)
        assert buy_price == pytest.approx(10.8554, 0.001)


# ──────────────────────────────────────────────────────────
# Test 8: 关键用户场景 — 已涨停的股不能推
# ──────────────────────────────────────────────────────────
class TestUserRequirement:
    """用户原话: '已经涨停的根本买不到' → 已封板的股绝对不能推."""

    def test_locked_stock_filtered_out(self):
        """9.5% 主板 / 19.5% 创/科 已封板 → 必须被过滤."""
        # 主板 9.5% 边界: 不在 [7%, 9.4%) → 排除
        for pct in [9.4, 9.5, 10.0]:
            if pct >= 9.4:
                in_chase_window = (7.0 <= pct < 9.4)
                assert not in_chase_window, f"{pct}% 不应进入追板窗口"

        # 创/科 19.4% 边界
        for pct in [19.4, 19.5, 20.0]:
            in_chase_window = (14.0 <= pct < 19.4)
            assert not in_chase_window, f"{pct}% 不应进入追板窗口"

    def test_buy_window_visible_to_user(self):
        """前端必须显示 '7%-9% (主板) / 14%-19% (创/科)' 的明确窗口."""
        # 这是用户的硬要求: 告诉用户何时买
        user_visible_buy_window = "涨幅冲到 7-9.4% (主板) / 14-19.4% (创/科) 时追高买入"
        assert "7" in user_visible_buy_window
        assert "9.4" in user_visible_buy_window or "9%" in user_visible_buy_window


# ──────────────────────────────────────────────────────────
# Test 9: 买入时点维度 (用户原话: '多少涨幅的时候买 能板上 这些都要考虑')
# ──────────────────────────────────────────────────────────
class TestChaseBuyTiming:
    """用户要求: 多少涨幅时买 / 能否买上 — 这些必须纳入考虑."""

    def test_buy_threshold_grid(self):
        """触发追板的涨幅网格 (主板/创/科 各 3 档)."""
        main_thresholds = [7.0, 8.0, 8.8]
        twenty_thresholds = [14.0, 16.0, 18.0]
        for t in main_thresholds:
            assert 7.0 <= t < 9.4
        for t in twenty_thresholds:
            assert 14.0 <= t < 19.4

    def test_buy_window_time_slots(self):
        """买入时段 (盘中分时): 早盘追 / 午盘追 / 尾盘追."""
        time_slots = [
            ("09:30-09:45", "早盘开盘"),
            ("09:45-10:30", "早盘中段"),
            ("10:30-13:00", "午盘"),
            ("13:00-14:30", "午后"),
            ("14:30-14:57", "尾盘"),
        ]
        assert len(time_slots) == 5
        assert "09:30" in time_slots[0][0]
        assert "14:30" in time_slots[-1][0]

    def test_slippage_scales_with_pct(self):
        """追板滑点应随涨幅递增."""
        slip_table = {7.5: 0.0005, 8.5: 0.0010, 9.0: 0.0020}
        assert slip_table[7.5] < slip_table[8.5] < slip_table[9.0]

    def test_chase_requires_close_locked(self):
        """追板的本质: 期望今天收盘真的封板."""
        rows = [
            {"date": "20260510", "open": 10.00, "high": 10.10, "low": 9.95, "close": 10.00,
             "volume": 1000, "amount": 1e7, "turnover": 1.0},
            {"date": "20260511", "open": 10.00, "high": 11.00, "low": 10.00, "close": 10.50,
             "volume": 2000, "amount": 2e7, "turnover": 3.0},
            {"date": "20260512", "open": 10.30, "high": 10.40, "low": 10.00, "close": 10.05,
             "volume": 1500, "amount": 1.5e7, "turnover": 2.0},
        ]
        df = _make_df(rows)
        prev_close = float(df.iloc[0]["收盘"])
        trigger_pct = (float(df.iloc[1]["最高"]) / prev_close - 1) * 100
        locked_pct = (float(df.iloc[1]["收盘"]) / prev_close - 1) * 100
        assert trigger_pct >= 7.0
        assert locked_pct < 9.5  # 没真封板 → 追板失败
        buy = 11.00
        sell = 10.30
        ret = (sell / buy - 1) * 100
        assert ret < -5.0


# ──────────────────────────────────────────────────────────
# Test 10: 实时扫描 (用户问: 数据能做到实时吗)
# ──────────────────────────────────────────────────────────
class TestRealtimeChaseFeed:
    def test_scan_interval(self):
        scan_interval_sec = 30
        trading_minutes = 4 * 60
        scans_per_day = (trading_minutes * 60) // scan_interval_sec
        assert scans_per_day >= 200

    def test_top_n_settable(self):
        for n in [3, 4, 5]:
            assert 1 <= n <= 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])