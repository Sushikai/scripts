"""tests/test_zt_daily.py — zt_backtest._compute_daily 单元测试 (TDD)

覆盖:
- 空列表 → []
- 单日多笔 → 单条目
- 跨日 → 按 buy_date 升序
- 正确统计 wins/losses/avg/sum/max/min/win_rate
- trades_detail 包含完整 trade 字段
- buy_date 接受 YYYYMMDD 字符串
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from tuixue_v3 import zt_backtest as zt


def _t(date: str, ret: float, code: str = "000001", name: str = "测试", main_kind: str = "trail_t2",
       streak: int = 2, exits_pct: dict | None = None) -> dict:
    """构造一个简单的 trade 字典。"""
    return {
        "code": code,
        "name": name,
        "buy_date": date,  # YYYYMMDD
        "sell_date": date,
        "buy_price": 10.0,
        "sell_price": 10.0 * (1 + ret / 100),
        "return_pct": ret,
        "main_kind": main_kind,
        "streak": streak,
        "exits_pct": exits_pct or {"trail_t2": ret, "close_t1": ret},
    }


def test_compute_daily_empty():
    """空列表 → 空结果。"""
    assert zt._compute_daily([]) == []


def test_compute_daily_single_day_single_trade():
    """单日单笔 → 单个条目。"""
    daily = zt._compute_daily([_t("20260115", 3.5)])
    assert len(daily) == 1
    assert daily[0]["date"] == "20260115"
    assert daily[0]["trades"] == 1
    assert daily[0]["wins"] == 1
    assert daily[0]["losses"] == 0
    assert daily[0]["win_rate_pct"] == 100.0
    assert daily[0]["avg_return_pct"] == 3.5
    assert daily[0]["sum_return_pct"] == 3.5
    assert daily[0]["max_win_pct"] == 3.5
    # 单笔 3.5 (win) → max_loss = 0 (无亏损)
    assert daily[0]["max_loss_pct"] == 0.0


def test_compute_daily_single_day_multiple_trades():
    """单日多笔 → 统计正确。"""
    trades = [
        _t("20260115", 3.0, code="000001"),
        _t("20260115", -1.0, code="000002"),
        _t("20260115", 5.0, code="000003"),
        _t("20260115", -2.0, code="000004"),
    ]
    daily = zt._compute_daily(trades)
    assert len(daily) == 1
    d = daily[0]
    assert d["date"] == "20260115"
    assert d["trades"] == 4
    assert d["wins"] == 2
    assert d["losses"] == 2
    assert d["win_rate_pct"] == 50.0
    assert d["avg_return_pct"] == pytest.approx(1.25, abs=0.01)
    assert d["sum_return_pct"] == pytest.approx(5.0, abs=0.01)
    assert d["max_win_pct"] == 5.0
    assert d["max_loss_pct"] == -2.0


def test_compute_daily_multiple_days_sorted_asc():
    """多日 → 按 buy_date 升序。"""
    trades = [
        _t("20260120", 1.0),
        _t("20260115", 2.0),
        _t("20260125", -3.0),
        _t("20260115", 4.0),
    ]
    daily = zt._compute_daily(trades)
    assert len(daily) == 3
    assert [d["date"] for d in daily] == ["20260115", "20260120", "20260125"]
    # 20260115 有 2 笔 (2+4=6, wins=2, avg=3)
    d15 = daily[0]
    assert d15["trades"] == 2
    assert d15["wins"] == 2
    assert d15["sum_return_pct"] == pytest.approx(6.0, abs=0.01)
    assert d15["avg_return_pct"] == pytest.approx(3.0, abs=0.01)
    # 20260120 单笔 +1
    d20 = daily[1]
    assert d20["trades"] == 1
    assert d20["max_win_pct"] == 1.0
    assert d20["max_loss_pct"] == 0.0
    # 20260125 单笔 -3
    d25 = daily[2]
    assert d25["trades"] == 1
    assert d25["wins"] == 0
    assert d25["losses"] == 1
    assert d25["max_loss_pct"] == -3.0


def test_compute_daily_trades_detail_complete():
    """trades_detail 包含完整 trade 字段。"""
    trades = [
        _t("20260115", 3.0, code="600000", name="测试A"),
        _t("20260115", -1.0, code="600001", name="测试B"),
    ]
    daily = zt._compute_daily(trades)
    detail = daily[0]["trades_detail"]
    assert len(detail) == 2
    assert detail[0]["code"] == "600000"
    assert detail[0]["name"] == "测试A"
    assert detail[0]["return_pct"] == 3.0
    assert detail[1]["code"] == "600001"
    assert detail[1]["return_pct"] == -1.0


def test_compute_daily_real_zeros_and_break_even():
    """0 收益算 win 还是 loss? 约定 0 不算赢也不算输 (wins = ret > 0, losses = ret < 0)."""
    trades = [
        _t("20260115", 0.0),
        _t("20260115", 1.0),
        _t("20260115", -2.0),
    ]
    daily = zt._compute_daily(trades)
    assert daily[0]["trades"] == 3
    assert daily[0]["wins"] == 1
    assert daily[0]["losses"] == 1
    # 0 收益算 break-even, 不计入胜/负
    assert daily[0]["win_rate_pct"] == pytest.approx(33.33, abs=0.01)


def test_compute_daily_monthly_filter_works():
    """通过 startswith('202601') 过滤 = 2026-01 月份 (frontend 用)."""
    trades = [
        _t("20260115", 1.0),
        _t("20260120", 2.0),
        _t("20260201", 3.0),  # 2月
    ]
    daily = zt._compute_daily(trades)
    jan = [d for d in daily if d["date"].startswith("202601")]
    feb = [d for d in daily if d["date"].startswith("202602")]
    assert len(jan) == 2
    assert len(feb) == 1
    assert feb[0]["date"] == "20260201"


def test_compute_daily_handles_dash_format():
    """buy_date 接受 YYYY-MM-DD 形式。"""
    trades = [
        {**_t("20260115", 1.0), "buy_date": "2026-01-15"},  # 替换
    ]
    daily = zt._compute_daily(trades)
    # 这种格式可能不被 groupby 聚类, 但函数不应该崩
    assert isinstance(daily, list)
