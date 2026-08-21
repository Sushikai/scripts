"""tests/test_zt_close_t0_lockup.py — close_t0 entry 的 T+1 lockup 行为 TDD

close_t0: 在 T 日收盘集合竞价买入 → T+1 就是第一个合法卖出日
(跟 open_t1 不同: open_t1 在 T+1 开盘买 → 必须等 T+2 才能卖)

修复: entry_rule="close_t0" 时 trail_t2/gap_high/stop_loss 应在 T+1 触发, 不是 T+2.
"""
import pandas as pd
import pytest

from tuixue_v3.zt_backtest import _simulate_trade


def _df(rows):
    return pd.DataFrame(rows)


def _zt():
    return {
        "code": "000001",
        "name": "测试股",
        "streak": 2,
        "date": "20260101",
        "sector": "测试",
    }


# ── 场景 1: close_t0 → T+1 触发 trail_t2 止盈 ──

def test_close_t0_trail_triggers_on_t1():
    """close_t0: T close 买入 → T+1 就可以卖. T+1 high ≥ activate → trail pullback 退出."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},  # T (涨停日)
        # T+1: buy=T close=10.0. high=10.5 ≥ activate=10.1 → trail 触发, pullback=10.5*0.995=10.4475
        {"日期": "20260102", "开盘": 10.2, "收盘": 10.4, "最高": 10.5, "最低": 10.1},
        {"日期": "20260105", "开盘": 10.3, "收盘": 10.3, "最高": 10.3, "最低": 10.3},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "close_t0", 1.0, 0.5, -5.0)
    # 应在 T+1 触发 trail_t2, 不是等到 T+2
    assert r["sell_date"] == "20260102", f"close_t0 应在 T+1 退出, 实际 {r['sell_date']}"
    assert r["trigger"] in ("trail_t2", "gap_high"), f"应在 T+1 触发 trail, 实际 {r['trigger']}"
    assert r["return_pct"] > 3.0, f"应盈利 ~4%, 实际 {r['return_pct']}%"


# ── 场景 2: close_t0 → T+1 触发 gap_high ──

def test_close_t0_gap_high_triggers_on_t1():
    """close_t0: T+1 高开 ≥ buy×1.003 → gap_high 触发, exit at T+1 high."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},  # T
        # T+1 open=10.5 ≥ 10.0*1.003=10.03 → gap_high 触发, exit at t1_high=11.0
        {"日期": "20260102", "开盘": 10.5, "收盘": 10.8, "最高": 11.0, "最低": 10.4},
        {"日期": "20260105", "开盘": 10.6, "收盘": 10.5, "最高": 10.7, "最低": 10.4},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "close_t0", 1.0, 0.5, -5.0)
    assert r["sell_date"] == "20260102", f"close_t0 gap_high 应在 T+1 退出, 实际 {r['sell_date']}"
    assert r["trigger"] == "gap_high", f"应触发 gap_high, 实际 {r['trigger']}"
    # buy=10.0, exit at t1_high=11.0 → +9.4% (含成本)
    assert r["return_pct"] > 8.0, f"应大盈, 实际 {r['return_pct']}%"


# ── 场景 3: close_t0 → T+1 触发 stop_loss ──

def test_close_t0_stop_loss_on_t1():
    """close_t0: T+1 low ≤ stop → T+1 止损."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},  # T
        # T+1: buy=10.0, stop=9.5. T+1 open=9.7 > 9.5, low=9.3 ≤ 9.5 → 止损触发
        # open > stop → 盘中触发, exit=stop_price=9.5
        {"日期": "20260102", "开盘": 9.7, "收盘": 9.4, "最高": 9.8, "最低": 9.3},
        {"日期": "20260105", "开盘": 9.5, "收盘": 9.6, "最高": 9.7, "最低": 9.4},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "close_t0", 1.0, 0.5, -5.0)
    assert r["sell_date"] == "20260102", f"close_t0 stop_loss 应在 T+1, 实际 {r['sell_date']}"
    assert r["trigger"] == "stop_loss", f"应触发 stop_loss, 实际 {r['trigger']}"
    assert r["sell_price"] == pytest.approx(9.5, abs=0.01)
    # -5.5% (含成本)
    assert r["return_pct"] < -5.0, f"应亏损 ~-5.5%, 实际 {r['return_pct']}%"


# ── 场景 4: close_t0 → T+1 未触发 → T+2 兜底 ──

def test_close_t0_falls_back_to_t2_when_t1_no_trigger():
    """close_t0: T+1 没触发任何信号 → T+2 继续等待 → T+2 close 兜底."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},  # T
        # T+1: high=10.02 < activate=10.1, low=9.9 > stop=9.5 → 不触发
        {"日期": "20260102", "开盘": 10.0, "收盘": 9.98, "最高": 10.02, "最低": 9.9},
        # T+2: 同样不触发 → close 兜底
        {"日期": "20260105", "开盘": 9.98, "收盘": 10.0, "最高": 10.02, "最低": 9.9},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "close_t0", 1.0, 0.5, -5.0)
    # 应该在 T+2 close 退出 (T+1 没触发, T+2 兜底)
    assert r["sell_date"] == "20260105", f"未触发时应 T+2 兜底, 实际 {r['sell_date']}"


# ── 场景 5: close_t0 → T+1 未高开但暴跌, stop_loss 触发 (真正的止损优先场景) ──

def test_close_t0_t1_no_gap_but_crash_stop_wins():
    """close_t0: T+1 open 没高开 (< gap_activate), 但盘中暴跌 low ≤ stop → stop_loss.
    这才是真实的止损场景: 股票没给高开逃命机会, 直接崩.
    """
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},  # T
        # T+1: open=10.0 (没高开), high=10.02 (没触发 trail), low=9.3 ≤ stop=9.5
        {"日期": "20260102", "开盘": 10.0, "收盘": 9.4, "最高": 10.02, "最低": 9.3},
        {"日期": "20260105", "开盘": 9.5, "收盘": 9.6, "最高": 9.7, "最低": 9.4},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "close_t0", 1.0, 0.5, -5.0)
    assert r["sell_date"] == "20260102", f"应在 T+1 退出, 实际 {r['sell_date']}"
    # open=10.0 ≤ stop=9.5? No → 盘中触发, exit=stop_price=9.5
    assert r["trigger"] == "stop_loss", f"止损应触发, 实际 {r['trigger']}"
    assert r["sell_price"] == pytest.approx(9.5, abs=0.01)
    # -5.5% (含成本)
    assert r["return_pct"] < -5.0, f"应亏损 ~-5.5%, 实际 {r['return_pct']}%"


# ── 场景 6: open_t1 vs close_t0 行为差异验证 ──

def test_open_t1_still_uses_t2():
    """open_t1 不应该被 close_t0 修复影响 — 仍应 T+2 触发."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # open_t1: buy=T+1 open=10.5. T+1 锁仓, T+2 才能卖.
        {"日期": "20260102", "开盘": 10.5, "收盘": 10.8, "最高": 11.0, "最低": 10.3},
        {"日期": "20260105", "开盘": 10.6, "收盘": 10.5, "最高": 10.7, "最低": 10.4},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    # open_t1 仍应在 T+2 退出 (T+1 锁仓)
    assert r["sell_date"] == "20260105", f"open_t1 应 T+2 退出, 实际 {r['sell_date']}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
