"""
yaogu_features.py — 妖股特征工程 v1 (R105 · 150 维)

类别:
  A. 单股时序 (~40 维): 均线/动量/量能/形态/妖股专属
  B. 横截面 (~50 维): 相对强弱/板块联动/同行业/全市场
  C. 大盘环境 (~30 维): 大盘/北向/政策/情绪面

输入: cache_db daily (dict[str, DataFrame])
输出: events_full[i]["features"] 字典 (150 keys)

用法:
  from yaogu_features import compute_features, eval_single_dim_ic
  compute_features(daily_dict) -> dict[code, features_dict]   # 离线特征生成
  eval_single_dim_ic(daily, prebuilt) -> [(dim_name, ic), ...]
"""
from __future__ import annotations

import logging
import statistics
import time as systime
from collections import defaultdict

import numpy as np
import pandas as pd

log = logging.getLogger("yaogu_features")

# ═══════════════════════════════════════════
# A. 单股时序特征 (40 维)
# ═══════════════════════════════════════════

def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).mean()

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(n, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(n, min_periods=1).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["最高"], df["最低"], df["收盘"]
    tr = pd.concat([
        h - l,
        (h - c.shift(1)).abs(),
        (l - c.shift(1)).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()

def _macd_hist(s: pd.Series) -> pd.Series:
    ema12 = _ema(s, 12)
    ema26 = _ema(s, 26)
    diff = ema12 - ema26
    dea = _ema(diff, 9)
    return (diff - dea) * 2

def _obv(df: pd.DataFrame, n: int = 14) -> pd.Series:
    sign = np.sign(df["收盘"].diff()).fillna(0)
    return (sign * df["成交量"]).rolling(n, min_periods=1).sum()


def single_features(df: pd.DataFrame) -> dict:
    """40 维单股特征 (基于当日行 i 的历史)."""
    if df is None or len(df) < 30:
        return {}
    c = df["收盘"]
    o = df["开盘"]
    h = df["最高"]
    l = df["最低"]
    v = df["成交量"]
    amt = df["成交额"]
    turn = df["换手率"]
    pct = df["涨跌幅"]

    f = {}
    # === 均线/趋势 (10 维) ===
    ma5 = _sma(c, 5)
    ma10 = _sma(c, 10)
    ma20 = _sma(c, 20)
    ma60 = _sma(c, 60)
    f["ma5_dev"] = float((c.iloc[-1] - ma5.iloc[-1]) / max(ma5.iloc[-1], 1e-6) * 100)
    f["ma10_dev"] = float((c.iloc[-1] - ma10.iloc[-1]) / max(ma10.iloc[-1], 1e-6) * 100)
    f["ma20_dev"] = float((c.iloc[-1] - ma20.iloc[-1]) / max(ma20.iloc[-1], 1e-6) * 100)
    f["ma_bull_aligned"] = int(bool(ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1]))
    f["close_above_ma20_pct_20d"] = float((c > ma20).tail(20).mean())
    f["macd_hist"] = float(_macd_hist(c).iloc[-1])
    f["trend_strength_20d"] = float(abs(c.iloc[-1] - ma20.iloc[-1]) / max(ma20.iloc[-1], 1e-6) * 100)
    f["bbi"] = float((ma5.iloc[-1] + ma10.iloc[-1] + ma20.iloc[-1] + ma60.iloc[-1]) / 4)
    f["bbi_dev"] = float((c.iloc[-1] - f["bbi"]) / max(f["bbi"], 1e-6) * 100)
    f["ema12_ema26_diff_pct"] = float((_ema(c, 12).iloc[-1] - _ema(c, 26).iloc[-1]) / max(_ema(c, 26).iloc[-1], 1e-6) * 100)

    # === 动量/反转 (8 维) ===
    f["roc_5d"] = float((c.iloc[-1] - c.iloc[-6]) / max(c.iloc[-6], 1e-6) * 100) if len(c) >= 6 else 0
    f["roc_10d"] = float((c.iloc[-1] - c.iloc[-11]) / max(c.iloc[-11], 1e-6) * 100) if len(c) >= 11 else 0
    f["roc_20d"] = float((c.iloc[-1] - c.iloc[-21]) / max(c.iloc[-21], 1e-6) * 100) if len(c) >= 21 else 0
    f["rsi_14"] = float(_rsi(c, 14).iloc[-1])
    f["momentum_3d"] = float((c.iloc[-1] - c.iloc[-4]) / max(c.iloc[-4], 1e-6) * 100) if len(c) >= 4 else 0
    f["reversal_6d"] = float((c.tail(6).min() / max(c.tail(6).max(), 1e-6) - 1) * 100)
    f["williams_r_14"] = float((h.tail(14).max() - c.iloc[-1]) / max(h.tail(14).max() - l.tail(14).min(), 1e-6) * -100)
    f["mfi_14"] = float(((c.diff().clip(lower=0) * amt).tail(14).sum() /
                          max((abs(c.diff()) * amt).tail(14).sum(), 1e-6)) * 100)

    # === 量能 (10 维) ===
    vol_ma5 = _sma(v, 5).iloc[-1]
    vol_ma20 = _sma(v, 20).iloc[-1]
    f["vol_ratio_5d"] = float(v.iloc[-1] / max(vol_ma5, 1))
    f["vol_ratio_20d"] = float(v.iloc[-1] / max(vol_ma20, 1))
    f["vol_burst_3x_ma20"] = int(v.iloc[-1] > 3 * vol_ma20)
    f["vol_uptick_days_5d"] = int(sum(1 for i in range(-5, 0)
                                       if c.iloc[i] > c.iloc[i-1] and v.iloc[i] > v.iloc[i-1]))
    f["amt_pct_ma5"] = float(_sma(amt, 5).iloc[-1])
    f["amt_zscore_20d"] = float((amt.iloc[-1] - _sma(amt, 20).iloc[-1]) /
                                  max(amt.tail(20).std(), 1e-6))
    f["turn_zscore_20d"] = float((turn.iloc[-1] - _sma(turn, 20).iloc[-1]) /
                                   max(turn.tail(20).std(), 1e-6))
    f["obv_14"] = float(_obv(df, 14).iloc[-1])
    f["obv_ma14_dev"] = float((_obv(df, 14).iloc[-1] - _sma(_obv(df, 14), 14).iloc[-1]) /
                                max(abs(_sma(_obv(df, 14), 14).iloc[-1]), 1e-6))
    f["vpt_20d"] = float((pct.fillna(0) * v / max(v.sum(), 1) * 100).tail(20).sum())

    # === 形态 (10 维) ===
    f["atr_14"] = float(_atr(df, 14).iloc[-1])
    f["volatility_20d"] = float(pct.tail(20).std())
    f["upper_shadow_ratio"] = float((h.iloc[-1] - max(c.iloc[-1], o.iloc[-1])) /
                                     max(abs(c.iloc[-1] - o.iloc[-1]), 1e-6))
    f["body_ratio"] = float(abs(c.iloc[-1] - o.iloc[-1]) / max(h.iloc[-1] - l.iloc[-1], 1e-6))
    f["yang_prob_20d"] = float((c > o).tail(20).mean())
    f["zt_count_20d"] = int(df["涨停"].tail(20).sum())
    f["yizi_count_20d"] = int(df["一字"].tail(20).sum()) if "一字" in df.columns else 0
    f["gap_open_pct"] = float((o.iloc[-1] - c.iloc[-2]) / max(c.iloc[-2], 1e-6) * 100) if len(c) >= 2 else 0
    f["drawdown_from_20d_high"] = float((h.tail(20).max() - c.iloc[-1]) / max(h.tail(20).max(), 1e-6) * 100)
    f["break_high_20d"] = int(c.iloc[-1] >= h.tail(20).max() * 0.998)

    # === 妖股专属 (5 维) ===
    f["streak_now"] = 0  # 调用方传入/重算
    f["burst_3d"] = int(df["涨停"].tail(3).sum() == 3)
    f["burst_rate_5d"] = float((df["涨停"] & (c < o * 1.01)).tail(5).sum() / 5.0)
    f["intraday_bargain"] = float((h.iloc[-1] - l.iloc[-1]) / max(o.iloc[-1], 1e-6))
    f["intraday_bargain_change"] = 0  # 调用方填

    return f


# ═══════════════════════════════════════════
# B. 横截面特征 (50 维) — 全市场快照视角
# ═══════════════════════════════════════════

def _pct_rank(arr: np.ndarray, v: float) -> float:
    """v 在 arr 中的百分位排名 (0-100)."""
    if len(arr) == 0 or np.isnan(v):
        return 50.0
    return float((arr < v).mean() * 100)


def cross_section_features(df: pd.DataFrame, all_daily: dict[str, pd.DataFrame]) -> dict:
    """50 维横截面: 当日 vs 全市场."""
    if df is None or len(df) < 30:
        return {}

    # === 全市场快照 (10 维) ===
    c_now = df["收盘"].iloc[-1]
    c1 = df["收盘"].iloc[-2] if len(df) >= 2 else c_now
    ret_1d = (c_now - c1) / max(c1, 1e-6) * 100

    # 全市场当日涨幅中位数 + 涨停率 (基于所有 daily 最后一日)
    all_ret_1d = []
    all_amt = []
    zt_count_today = 0
    total_count = 0
    for code, df2 in all_daily.items():
        if df2 is None or len(df2) < 2:
            continue
        c0 = df2["收盘"].iloc[-1]
        c1_ = df2["收盘"].iloc[-2]
        if c1_ > 0:
            all_ret_1d.append((c0 - c1_) / c1_ * 100)
            all_amt.append(df2["成交额"].iloc[-1])
            total_count += 1
            if df2["涨停"].iloc[-1]:
                zt_count_today += 1
    if not all_ret_1d:
        return {}
    all_ret_arr = np.array(all_ret_1d)

    f = {}
    f["mkt_median_ret_1d"] = float(np.median(all_ret_arr))
    f["mkt_zt_count_today"] = int(zt_count_today)
    f["mkt_zt_rate"] = float(zt_count_today / max(total_count, 1))
    f["stock_rps_1d"] = _pct_rank(all_ret_arr, ret_1d)
    f["stock_alpha_vs_median_1d"] = float(ret_1d - np.median(all_ret_arr))

    # 5/10/20 日 alpha vs 全市场中位数 (基于 ret_1d 历史均值)
    if len(df) >= 21:
        ret_5d = (df["收盘"].iloc[-1] - df["收盘"].iloc[-6]) / max(df["收盘"].iloc[-6], 1e-6) * 100
        ret_10d = (df["收盘"].iloc[-1] - df["收盘"].iloc[-11]) / max(df["收盘"].iloc[-11], 1e-6) * 100
        ret_20d = (df["收盘"].iloc[-1] - df["收盘"].iloc[-21]) / max(df["收盘"].iloc[-21], 1e-6) * 100
        # 全市场 5/10/20 日中位数 (用 ret_1d 历史样本估算)
        f["stock_alpha_vs_median_5d"] = float(ret_5d - np.median(all_ret_arr) * 5)
        f["stock_alpha_vs_median_10d"] = float(ret_10d - np.median(all_ret_arr) * 10)
        f["stock_alpha_vs_median_20d"] = float(ret_20d - np.median(all_ret_arr) * 20)
        f["stock_rps_5d"] = _pct_rank(all_ret_arr * 5, ret_5d)
        f["stock_rps_10d"] = _pct_rank(all_ret_arr * 10, ret_10d)
        f["stock_rps_20d"] = _pct_rank(all_ret_arr * 20, ret_20d)

    # === 相对强弱 (5 维) ===
    f["stock_rps_3d"] = _pct_rank(all_ret_arr * 3, ret_1d * 3)
    f["amt_pct_market"] = _pct_rank(np.array(all_amt), float(df["成交额"].iloc[-1]))
    f["close_pct_market"] = 50.0  # 占位: 不算价格分位 (量纲问题)
    f["vol_pct_market"] = _pct_rank(np.array([d["成交量"].iloc[-1] for d in all_daily.values() if d is not None and len(d) >= 1]),
                                     float(df["成交量"].iloc[-1]))

    # === 同行业 / 板块 (10 维) — 占位 (需 sector map, R105 阶段先用通用代理) ===
    # 用同一价格区间 (5 元一档) 作为行业代理, 简化版
    price_bucket = int(c_now // 5) * 5
    bucket_returns = []
    for code, df2 in all_daily.items():
        if df2 is None or len(df2) < 2:
            continue
        c0 = df2["收盘"].iloc[-1]
        c1_ = df2["收盘"].iloc[-2]
        c0_p = df2["收盘"].iloc[-1]
        if c0_p == 0:
            continue
        bucket = int(c0_p // 5) * 5
        if bucket == price_bucket and c1_ > 0:
            bucket_returns.append((c0 - c1_) / c1_ * 100)
    if bucket_returns:
        bucket_arr = np.array(bucket_returns)
        f["bucket_median_ret_1d"] = float(np.median(bucket_arr))
        f["stock_alpha_vs_bucket_1d"] = float(ret_1d - np.median(bucket_arr))
        f["bucket_rps"] = _pct_rank(bucket_arr, ret_1d)
        f["bucket_zt_count_today"] = 0  # 占位
        f["bucket_size"] = len(bucket_returns)
    else:
        f["bucket_median_ret_1d"] = 0
        f["stock_alpha_vs_bucket_1d"] = 0
        f["bucket_rps"] = 50
        f["bucket_zt_count_today"] = 0
        f["bucket_size"] = 0

    # === 全市场聚合 (5 维) ===
    f["mkt_amt_total"] = float(np.sum(all_amt))
    f["mkt_amt_5d_avg"] = f["mkt_amt_total"]  # 占位, 需历史快照
    f["mkt_amt_change_1d"] = 0  # 占位
    f["mkt_zt_vs_total"] = f["mkt_zt_count_today"] / max(total_count, 1)
    f["mkt_dt_count_proxy"] = 0  # 跌停数: 占位

    # === 自身在板块中的相对地位 (基于价格/量能) ===
    f["stock_vs_market_amt_ratio"] = float(df["成交额"].iloc[-1] / max(np.mean(all_amt), 1e-6))
    f["stock_vs_market_vol_ratio"] = float(df["成交量"].iloc[-1] / max(np.mean([d["成交量"].iloc[-1] for d in all_daily.values() if d is not None and len(d) >= 1]), 1e-6))

    return f


# ═══════════════════════════════════════════
# C. 大盘/外部环境特征 (30 维)
# ═══════════════════════════════════════════

def macro_features_for_date(daily: dict[str, pd.DataFrame], asof_date: str) -> dict:
    """30 维环境: 大盘/北向/政策/情绪面.
    按 asof_date 切片: 用 daily[code] 截止 asof_date 的横截面派生当日 macro.
    北向 + 政策维度因数据源未接入, 用占位 (None)."""
    if not daily:
        return {}

    f = {}
    all_ret_1d = []
    zt_count = 0
    dt_count = 0
    burst_count = 0
    streak_max = 0

    for code, df in daily.items():
        if df is None or len(df) < 2:
            continue
        df = df[df["日期"] <= asof_date]
        if len(df) < 2:
            continue
        c0 = df["收盘"].iloc[-1]
        c1_ = df["收盘"].iloc[-2]
        if c1_ > 0:
            all_ret_1d.append((c0 - c1_) / c1_ * 100)
        if df["涨停"].iloc[-1]:
            zt_count += 1
        if c0 < c1_ * 0.95:
            dt_count += 1
        last_n = 0
        for x in df["涨停"].iloc[::-1].values:
            if x:
                last_n += 1
            else:
                break
        if last_n > streak_max:
            streak_max = last_n
        if df["涨停"].iloc[-1]:
            o = df["开盘"].iloc[-1]
            l = df["最低"].iloc[-1]
            if l < o * 0.99:
                burst_count += 1

    if not all_ret_1d:
        return {}
    arr = np.array(all_ret_1d)
    total = len(all_ret_1d)

    # === 大盘代理 (10 维) — 用全市场中位数/均值作为大盘代理 ===
    f["mkt_proxy_ret_1d"] = float(np.median(arr))
    f["mkt_proxy_ret_5d"] = float(np.median(arr) * 5)
    f["mkt_proxy_ret_10d"] = float(np.median(arr) * 10)
    f["mkt_proxy_vol_5d"] = float(arr.std() * 5)
    f["mkt_proxy_pct_above_ma20"] = 50.0
    f["mkt_proxy_rsi_14"] = 50.0
    f["mkt_proxy_20d_high_dist"] = 0.0
    f["mkt_proxy_zt_rate"] = float(zt_count / max(total, 1))
    f["mkt_proxy_dt_rate"] = float(dt_count / max(total, 1))
    f["mkt_proxy_burst_rate"] = float(burst_count / max(total, 1))

    # === 北向资金 (5 维 — 占位, 未接入) ===
    f["north_flow_1d"] = None
    f["north_flow_5d_cum"] = None
    f["north_flow_10d_cum"] = None
    f["north_flow_sh_1d"] = None
    f["north_flow_sz_1d"] = None

    # === 政策/汇率 (5 维 — 占位) ===
    f["cny_rate_chg_1d"] = None
    f["usdcny_5d_chg"] = None
    f["treasury_10y_chg"] = None
    f["treasury_30y_chg"] = None
    f["pboc_omo_net"] = None

    # === 情绪面 (10 维) ===
    f["env_zt_count"] = int(zt_count)
    f["env_dt_count"] = int(dt_count)
    f["env_burst_count"] = int(burst_count)
    f["env_max_streak"] = int(streak_max)
    f["env_zt_rate"] = float(zt_count / max(total, 1))
    f["env_dt_rate"] = float(dt_count / max(total, 1))
    f["env_median_ret_1d"] = float(np.median(arr))
    f["env_p25_ret_1d"] = float(np.percentile(arr, 25))
    f["env_p75_ret_1d"] = float(np.percentile(arr, 75))
    f["env_total_stocks"] = int(total)

    return f


# ═══════════════════════════════════════════
# 整合入口
# ═══════════════════════════════════════════

def compute_features(daily: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """对每只票派生 (single + cross_section) 两类特征.
    返回 {code: {dim_name: value, ...}} 共 ~90 维. macro 按 date 另算."""
    t0 = systime.time()
    out = {}
    for code, df in daily.items():
        f = {}
        sf = single_features(df)
        cf = cross_section_features(df, daily)
        f.update(sf)
        f.update(cf)
        out[code] = f
    n_dim = len(out[list(out.keys())[0]]) if out else 0
    log.info("compute_features: %d stocks × %d dims (%.1fs)", len(out), n_dim, systime.time() - t0)
    return out


# ═══════════════════════════════════════════
# 单维 rank-IC 评估 (R105 验证入口)
# ═══════════════════════════════════════════

def eval_single_dim_ic(daily: dict[str, pd.DataFrame],
                        events_full: list[dict],
                        forward_n: int = 10) -> list[tuple[str, float, int]]:
    """对每个维度算 rank-IC (Spearman vs forward N 日收益).
    返回 [(dim, ic, n_samples), ...] 按 |IC| 降序.
    macro 维度按 ev.date 索引, 不再混入单股快照."""
    fwd_map = {}
    for ev in events_full:
        fr = ev.get("fwd_10d") if forward_n == 10 else ev.get(f"fwd_{forward_n}d")
        if fr is not None:
            fwd_map[(ev["code"], ev["date"])] = fr

    feats = compute_features(daily)

    # 收集每个维度的 (value, fwd_ret) 配对
    pairs_per_dim: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for ev in events_full:
        code = ev["code"]
        date = ev["date"]
        if (code, date) not in fwd_map:
            continue
        f = feats.get(code, {})
        if not f:
            continue
        fwd = fwd_map[(code, date)]
        for dim, val in f.items():
            if val is None:
                continue
            try:
                v = float(val)
                if np.isnan(v) or np.isinf(v):
                    continue
                pairs_per_dim[dim].append((v, fwd))
            except (TypeError, ValueError):
                continue

    # macro 维度按 date 缓存
    macro_cache: dict[str, dict] = {}
    for ev in events_full:
        if (ev["code"], ev["date"]) not in fwd_map:
            continue
        date = ev["date"]
        if date not in macro_cache:
            macro_cache[date] = macro_features_for_date(daily, date)
        macro = macro_cache[date]
        fwd = fwd_map[(ev["code"], ev["date"])]
        for dim, val in macro.items():
            if val is None:
                continue
            try:
                v = float(val)
                if np.isnan(v) or np.isinf(v):
                    continue
                pairs_per_dim[dim].append((v, fwd))
            except (TypeError, ValueError):
                continue

    # 算 Spearman
    results = []
    for dim, pairs in pairs_per_dim.items():
        if len(pairs) < 30:
            continue
        xs = np.array([p[0] for p in pairs])
        ys = np.array([p[1] for p in pairs])
        ic = _spearman(xs, ys)
        results.append((dim, ic, len(pairs)))
    results.sort(key=lambda x: -abs(x[1]))
    return results


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation."""
    def _ranks(v):
        order = np.argsort(v)
        ranks = np.empty_like(order, dtype=float)
        ranks[order] = np.arange(1, len(v) + 1, dtype=float)
        return ranks
    rx, ry = _ranks(x), _ranks(y)
    n = len(rx)
    mx, my = rx.mean(), ry.mean()
    cov = ((rx - mx) * (ry - my)).sum()
    vx = ((rx - mx) ** 2).sum()
    vy = ((ry - my) ** 2).sum()
    if vx == 0 or vy == 0:
        return 0.0
    return float(cov / np.sqrt(vx * vy))


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="R105 妖股特征工程评估")
    ap.add_argument("--eval", action="store_true", help="单维 rank-IC 评估")
    ap.add_argument("--start", default="20240101")
    ap.add_argument("--end", default="20260807")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--forward", type=int, default=10)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if args.eval:
        from yaogu_survey import load_daily
        from yaogu_optimizer import build_prebuilt
        log.info("load daily...")
        daily = load_daily()
        log.info("build prebuilt (events + fwd_ret)...")
        # build_prebuilt 内部已算 fwd_10d
        pb = build_prebuilt(start=args.start, end=args.end)
        events = pb["events_full"]
        log.info("eval %d dims × %d events", 150, len(events))
        results = eval_single_dim_ic(daily, events, forward_n=args.forward)
        print(f"\n=== Top {args.top} 单维 rank-IC (forward {args.forward}d) ===")
        for dim, ic, n in results[:args.top]:
            mark = "★" if ic > 0.02 else ("✗" if ic < -0.02 else " ")
            print(f"  {mark} {dim:30s}  IC={ic:+.4f}  n={n}")
        print(f"\n=== Bottom {args.top} 单维 rank-IC ===")
        for dim, ic, n in results[-args.top:]:
            mark = "★" if ic > 0.02 else ("✗" if ic < -0.02 else " ")
            print(f"  {mark} {dim:30s}  IC={ic:+.4f}  n={n}")


if __name__ == "__main__":
    _cli()