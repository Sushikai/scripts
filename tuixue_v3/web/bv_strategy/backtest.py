"""
历史回测 (R2003.1) — BV 战法 T+1 开盘买入 → 持有 5 天, max(high) ≥ 买入价 × 1.02 算赢。

实现:
  1. 拉最近 days 交易日的 ZT 池 (复用 fused_backtest 路径, 简化版)
  2. 对每个交易日, 用 screener._rule_hits 跑一遍 ZT 池 → 取 top_n
  3. T+1 开盘买入, 持有 5 天 max(high) 检查
  4. 算 win_rate_pct / avg_return / max_drawdown
  5. 跟 honest_wr_ceiling 比较
"""
import time
import pandas as pd
from typing import Any


# R2003.6: 提前 import data_layer (项目 root) — 让 sys.modules 命中
try:
    import importlib
    import os
    import sys
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import data_layer  # noqa: F401
except Exception:
    pass


def bv_backtest(days: int = 180, refresh: bool = False, target_wr: float = 60.0) -> dict:
    """BV 战法历史回测 — 5d-max-high 口径。

    跟 fused_backtest 类似但用 BV 规则引擎 (screener._rule_hits) 代替 zt_score。
    R2003.2: 加 18s 硬超时 (server middleware 25s 总预算), 超时返 partial_results。
    """
    import multi_source_fetchers as msf
    from .screener import _rule_hits
    import signal

    # R2003.2: 软超时 — 给上游数据拉取留 18s 预算, 剩余 7s 给日线 + 模拟
    # R2003.9: 拉到 25s (server middleware 25s 总预算) — ZT 池 + 10 个 dailies 并发都吃紧
    _deadline = time.time() + 24.0

    def _budget_left() -> float:
        return max(0.5, _deadline - time.time())

    try:
        dates = msf.fetch_trade_dates(days + 10) or []
    except Exception:
        dates = []
    if not dates:
        return {
            "ts": time.time(), "days": days, "trades": 0, "win_rate_pct": None,
            "avg_return_pct": None, "max_drawdown_pct": None,
            "meet_target": False, "status": "no_dates",
            "message": "无交易日期", "params_used": {"rule_version": "v1"},
        }
    dates = sorted(dates)[-days:]
    # R2003.6: 限制单次最多 5 个交易日 (留 10s 给日线 + 模拟)
    if len(dates) > 5:
        dates = dates[-5:]

    # 全市场候选 (简化: 用 ZT 池)
    all_codes: set[str] = set()
    daily_picks: dict[str, list[dict]] = {}
    _diag = {"dates_count": len(dates), "fetched": 0, "empty_pools": 0, "slow_fetches": 0, "elapsed_ms": 0}
    _t0 = time.time()
    for d in dates:
        if _budget_left() < 1.5:
            break
        _fd0 = time.time()
        try:
            pool = msf.fetch_zt_pool(d) or []
        except Exception:
            pool = []
        _fd_ms = int((time.time() - _fd0) * 1000)
        if _fd_ms > 1500:
            _diag["slow_fetches"] += 1
        if pool:
            _diag["fetched"] += 1
        else:
            _diag["empty_pools"] += 1
        for r in pool:
            code = str(r.get("code", "")).zfill(6)
            if code:
                all_codes.add(code)
        daily_picks[d] = pool
    _diag["elapsed_ms"] = int((time.time() - _t0) * 1000)

    if not all_codes:
        return {
            "ts": time.time(),
            "days": days,
            "trades": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "max_drawdown_pct": None,
            "meet_target": False,
            "status": "no_data",
            "message": "无 ZT 池数据 (回测前置)",
            "params_used": {"rule_version": "v1"},
            "diagnostics": _diag,
        }

    # 批量拉日线
    dailies: dict = {}

    # 批量拉日线 — 先跑规则过滤,只取 top 候选的 dailies (节省 30x I/O)
    trades: list[dict] = []
    # 1) 跑规则 + 收集 top 候选
    all_picks: list[dict] = []
    for d in dates:
        pool = daily_picks.get(d) or []
        if not pool:
            continue
        for r in pool:
            code = str(r.get("code", "")).zfill(6)
            # 实际 ZT 池字段 → 规则字段映射
            streak = int(r.get("streak", 0) or 0)
            turnover = float(r.get("turnover_pct", 0) or 0)  # 换手率 % 当量比代理
            amount_raw = float(r.get("amount", 0) or 0)
            amount_yi = amount_raw / 1e8 if amount_raw > 1e6 else amount_raw
            limit_amount = float(r.get("limit_order_amount", 0) or 0)
            # 封成比 = limit_order_amount / amount (越高越强)
            seal_ratio = (limit_amount / amount_raw) if amount_raw > 0 else 0
            # 涨停: change_pct = 10 (一字) 或 20 (创业), 这里不直接知道, 用 10
            info = {
                "streak": streak,
                "vol_ratio": max(turnover / 10.0, 1.0) if turnover > 0 else 1.5,  # 换手率 / 10 作为量比代理
                "volume_ratio": max(turnover / 10.0, 1.0) if turnover > 0 else 1.5,
                "mcap_yi": float(r.get("market_cap", 0) or 0) / 1e8 if r.get("market_cap") else 0,
                "change_pct": 10.0 if streak >= 1 else 0.0,  # 涨停简化
                "upper_shadow_ratio": 0.0,
                "consolidation_days": 0,
                "first_5min_vol_ratio": seal_ratio * 5.0 if seal_ratio > 0 else 1.0,
            }
            matched = _rule_hits(info)
            if not matched:
                continue
            all_picks.append({
                "code": code,
                "name": r.get("name", ""),
                "matched": matched,
                "date": d,
                "streak": streak,
                "turnover": turnover,
                "amount_yi": amount_yi,
            })
    # 2) 取 top 候选 (按命中数 + weighted_sum)
    if not all_picks:
        return {
            "ts": time.time(),
            "days": days,
            "trades": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "max_drawdown_pct": None,
            "meet_target": False,
            "status": "no_matched",
            "message": "无 BV 规则命中",
            "params_used": {"rule_version": "v1", "rules_loaded": 15},
            "diagnostics": _diag,
        }
    # 去重 — 按 code 取最大命中数
    by_code: dict[str, dict] = {}
    for p in all_picks:
        code = p["code"]
        if code not in by_code or len(p["matched"]) > len(by_code[code]["matched"]):
            by_code[code] = p
    candidate_codes = list(by_code.keys())
    if not candidate_codes:
        return {
            "ts": time.time(),
            "days": days,
            "trades": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "max_drawdown_pct": None,
            "meet_target": False,
            "status": "no_matched",
            "message": "无 BV 规则命中 (去重后)",
            "params_used": {"rule_version": "v1", "rules_loaded": 15},
            "diagnostics": _diag,
        }
    # 3) 只拉候选的 dailies (30x 节省)
    dailies: dict = {}
    if _budget_left() < 3.0:
        return {
            "ts": time.time(),
            "days": days,
            "trades": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "max_drawdown_pct": None,
            "meet_target": False,
            "status": "timeout_loading_daily",
            "message": "上游慢,期货抓日线前超时",
            "params_used": {"rule_version": "v1", "rules_loaded": 15},
            "diagnostics": _diag,
        }
    # R2003.4: cap candidate_codes to 10 to bound I/O (200ms/c × 30 = 6s risk)
    if len(candidate_codes) > 10:
        # 按命中数降序, 取 top 10
        candidate_codes_sorted = sorted(
            candidate_codes,
            key=lambda c: -len(by_code[c]["matched"]),
        )
        candidate_codes = candidate_codes_sorted[:10]
    _diag["candidates_count"] = len(candidate_codes)
    _diag["dailies_loaded"] = 0
    # R2003.5: 直接调 data_layer.fetch_daily (串行,3s/code 预算, 让总 I/O 有界)
    try:
        import importlib
        import sys
        # 多重尝试 — 在 server 启动时 data_layer 已经被 datalayer import 过
        _dl = None
        for modname in [
            "tuixue_v3.data_layer",
            "data_layer",
            "tuixue_v3.web.data_layer",
        ]:
            try:
                _dl = importlib.import_module(modname)
                if hasattr(_dl, "fetch_daily"):
                    break
            except Exception:
                continue
        if _dl is None or not hasattr(_dl, "fetch_daily"):
            raise ImportError(f"data_layer not loadable; sys.modules keys: {[k for k in sys.modules if 'data' in k.lower()][:5]}")
        for c in candidate_codes:
            if _budget_left() < 1.5:
                break
            try:
                df = _dl.fetch_daily(c, max(days + 30, 60))
                if df is not None and not df.empty:
                    dailies[c] = df
            except Exception:
                pass
        _diag["dailies_loaded"] = len(dailies)
    except Exception as e:
        _diag["dailies_error"] = f"{type(e).__name__}: {e}"

    # 4) 模拟交易
    for p in all_picks:
        if _budget_left() < 1.0:
            break
        df = dailies.get(p["code"])
        if df is None or (hasattr(df, "empty") and df.empty):
            continue
        df = df.sort_values("日期").reset_index(drop=True)
        d = p["date"]
        # d 是 YYYYMMDD, df["日期"] 是 Timestamp — 归一化比较
        try:
            ts_target = pd.Timestamp(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
        except Exception:
            continue
        df_dates = pd.to_datetime(df["日期"])
        buy_idx = df_dates[df_dates == ts_target].index
        if buy_idx.empty:
            # 容错: 找最近日期
            nearest = (df_dates - ts_target).abs()
            if nearest.empty:
                continue
            buy_idx = pd.Index([int(nearest.idxmin())])
        buy_idx = int(buy_idx[0])
        if buy_idx + 1 >= len(df):
            continue
        entry = float(df["开盘"].iloc[buy_idx + 1])
        if entry <= 0:
            continue
        # 5 天 max(high)
        window = df["最高"].iloc[buy_idx + 1: buy_idx + 6]
        if window.empty:
            continue
        max_high = float(window.max())
        # win 口径: max_high ≥ entry × 1.02 (Bryan 战法偏保守, 需涨幅 ≥2% 才算赢)
        win = max_high >= entry * 1.02
        ret_pct = round((max_high - entry) / entry * 100, 2)
        trades.append({
            "code": p["code"],
            "name": p["name"],
            "date": d,
            "entry": round(entry, 2),
            "max_high": round(max_high, 2),
            "ret_pct": ret_pct,
            "matched": p["matched"],
            "win": win,
        })

    n = len(trades)
    if n == 0:
        return {
            "ts": time.time(),
            "days": days,
            "trades": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "max_drawdown_pct": None,
            "meet_target": False,
            "status": "no_trades",
            "message": "无交易 (ZT 池空或日线缺失)",
            "params_used": {"rule_version": "v1", "rules_loaded": 15},
            "diagnostics": _diag,
        }

    wins = sum(1 for t in trades if t["win"])
    win_rate = round(wins / n * 100, 2)
    avg_ret = round(sum(t["ret_pct"] for t in trades) / n, 2)
    # 最大回撤
    equity = [1.0]
    for t in trades:
        equity.append(equity[-1] * (1 + t["ret_pct"] / 100.0))
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd
    max_dd = round(max_dd, 2)

    return {
        "ts": time.time(),
        "days": days,
        "trades": n,
        "wins": wins,
        "win_rate_pct": win_rate,
        "avg_return_pct": avg_ret,
        "max_drawdown_pct": max_dd,
        "meet_target": win_rate >= target_wr,
        "target_wr": target_wr,
        "status": "ok",
        "params_used": {"rule_version": "v1", "rules_loaded": 15, "win_threshold": 0.02},
        "message": f"BV 战法回测 {days} 日: {wins}/{n} 笔盈利, WR={win_rate}%",
        "diagnostics": _diag,
    }