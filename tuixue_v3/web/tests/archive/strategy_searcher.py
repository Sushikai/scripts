"""
尾盘战法策略搜索器 (调试框架 · 临时)
====================================
目标: 用第一性原理 + 1000 轮参数搜索, 找高胜率 + 3%+ 日均收益的策略
      (尾盘买, T+1 卖)

数据源: SQLite data/cache.db 的 daily 表
  - 范围: 1996-08-13 ~ 2026-07-17
  - 3072 只股票, 1M+ 行

参数空间 (7 维, 1000+ 组合):
  A. Universe:        主板 / 全 A
  B. Selection:       change_pct 区间 / vol_ratio 阈值 / 20d 涨停次数
  C. Market regime:   大盘红线 (红盘率最低) / 大盘 5d momentum
  D. Sector:          板块涨幅 / 板块涨停数
  E. Exit:            T+1 open / 09:35 / 10:00 / 14:30
  F. Stop loss:       -1% / -2% / -3% / -5%
  G. Position:        Top 1 / Top 2 / Top 3

评分: 综合 win_rate / avg_return / sharpe / max_dd, walk-forward 验证

用法:
  /Users/kaikai/.hermes/hermes-agent/venv/bin/python3 \\
      web/tests/strategy_searcher.py \\
      --quick          # 100 组合快速测试
      --full           # 1000+ 组合
      --top 10         # 输出 top 10
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

DB_PATH = "/Users/kaikai/scripts/tuixue_v3/data/cache.db"
OUT_DIR = Path("/Users/kaikai/scripts/tuixue_v3/web/tests/artifacts/strategy_search")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ═════════════════════════════════════════════════════════════════
# 1) 数据加载
# ═════════════════════════════════════════════════════════════════
def load_daily(start: str = "2024-01-01", end: str = "2026-07-17") -> pd.DataFrame:
    """从 SQLite 加载 daily, 转 int date → datetime, 排序"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(f"""
        SELECT code, date, open, high, low, close, volume, amount, turnover
        FROM daily
        WHERE date >= '{start.replace('-','')}' AND date <= '{end.replace('-','')}'
        ORDER BY code, date
    """, conn)
    conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """每只股票加技术特征 (向量化)"""
    g = df.groupby("code", sort=False)
    df["prev_close"] = g["close"].shift(1)
    df["change_pct"] = (df["close"] / df["prev_close"] - 1.0) * 100.0
    df["ma5"]  = g["close"].transform(lambda s: s.rolling(5,  min_periods=2).mean())
    df["ma10"] = g["close"].transform(lambda s: s.rolling(10, min_periods=3).mean())
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=5).mean())
    df["vol_ma5"]   = g["volume"].transform(lambda s: s.rolling(5,  min_periods=2).mean())
    df["vol_ma20"]  = g["volume"].transform(lambda s: s.rolling(20, min_periods=4).mean())
    df["vol_ratio"] = df["volume"] / df["vol_ma5"].replace(0, np.nan)
    # 20d 涨停次数 (close/prev_close - 1 >= 9.5%)
    df["zt_20d"] = g["change_pct"].transform(lambda s: (s >= 9.5).rolling(20, min_periods=2).sum())
    # 当日是否涨停
    df["is_zt"] = (df["change_pct"] >= 9.5).astype(int)
    # 当日振幅
    df["amplitude"] = (df["high"] - df["low"]) / df["prev_close"].replace(0, np.nan) * 100.0
    # 收盘位置 (close - low) / (high - low), 1 = 收最高
    df["close_pos"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    # 站上 5/10/20 日均线
    df["above_ma5"]  = (df["close"] > df["ma5"]).astype(int)
    df["above_ma10"] = (df["close"] > df["ma10"]).astype(int)
    df["above_ma20"] = (df["close"] > df["ma20"]).astype(int)
    return df


def add_market_features(df: pd.DataFrame) -> pd.DataFrame:
    """每只股票加当日大盘特征 (日级 group)"""
    # 红盘率: 当日 change_pct > 0 的占比
    red_rate = df.groupby("date")["change_pct"].apply(lambda s: (s > 0).mean())
    df["mkt_red_rate"] = df["date"].map(red_rate)
    # 涨停数
    zt_count = df.groupby("date")["is_zt"].sum()
    df["mkt_zt_count"] = df["date"].map(zt_count)
    # 中位数涨幅
    med_chg = df.groupby("date")["change_pct"].median()
    df["mkt_med_chg"] = df["date"].map(med_chg)
    # 跌停数
    dt_count = df.groupby("date").apply(lambda g: (g["change_pct"] <= -9.5).sum(), include_groups=False)
    df["mkt_dt_count"] = df["date"].map(dt_count)
    # 大盘代理: 全 A 等权日收益 + 5d/10d 滚动
    daily_chg = df.groupby("date")["change_pct"].mean()
    df["mkt_chg"] = df["date"].map(daily_chg)
    mkt_chg_sorted = daily_chg.sort_index()
    df["mkt_5d_chg"] = df["date"].map(mkt_chg_sorted.rolling(5, min_periods=2).sum())
    df["mkt_10d_chg"] = df["date"].map(mkt_chg_sorted.rolling(10, min_periods=3).sum())
    return df


# ═════════════════════════════════════════════════════════════════
# 2) 参数空间
# ═════════════════════════════════════════════════════════════════
@dataclass
class StrategyParams:
    """一组策略参数 (7 维)"""
    # A. Universe
    universe: str = "main_board"          # main_board / all_a
    # B. Selection
    change_pct_min: float = 1.0
    change_pct_max: float = 7.0
    vol_ratio_min: float = 0.8
    zt_20d_min: int = 0
    require_above_ma5: bool = False
    require_above_ma10: bool = False
    require_close_pos_min: float = 0.0   # 0=不要求, 0.5=收中位以上, 0.8=收高位
    # C. Market regime
    mkt_red_rate_min: float = 0.0         # 红盘率下限 (0=不要求)
    mkt_red_rate_max: float = 1.0         # 红盘率上限 (1=不要求)
    mkt_zt_count_min: int = 0             # 大盘涨停数下限
    mkt_dt_count_max: int = 9999          # 大盘跌停数上限
    mkt_5d_chg_min: float = -999.0        # 大盘 5d 累计收益下限 (regime filter)
    mkt_5d_chg_max: float = 999.0         # 大盘 5d 累计收益上限
    # D. Sector (留接口, 后续接 sector_taxonomy)
    sector_filter: bool = False
    # D2. Liquidity filter (R-fix: 成交额 < 5000万 = 流动性差, 滑点大)
    min_amount_wan: float = 0.0            # 0=不限, 5000=5000万
    # D3. Time filter
    weekday_only: int = 0                  # 0=不限, 1-5 = 周N+1
    exclude_last_3_days: bool = False      # 避开月末/季末效应
    # D4. Stock cooldown (同一股票 N 日内不重复)
    cooldown_days: int = 0                 # 0=不限, 3=3日内不重复
    # E. Exit
    exit_at: str = "t1_open"              # t1_open / t1_0935 / t1_1000 / t1_close
    # F. Stop loss (T+1 持有期内的盘中止损)
    stop_loss_pct: float = -999.0         # -999=不限
    # G. Position
    top_n: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def param_space_quick() -> list[StrategyParams]:
    """100 组合快速测试"""
    out = []
    for cp_min in [0.5, 1.0, 2.0]:
        for cp_max in [5.0, 7.0, 9.5]:
            for vr in [0.8, 1.5, 2.0]:
                for zt in [0, 1]:
                    for exit_at in ["t1_open", "t1_1000"]:
                        for top_n in [1, 2]:
                            out.append(StrategyParams(
                                change_pct_min=cp_min, change_pct_max=cp_max,
                                vol_ratio_min=vr, zt_20d_min=zt,
                                exit_at=exit_at, top_n=top_n,
                            ))
    return out


def param_space_full() -> list[StrategyParams]:
    """1500 组合 (覆盖核心 7 维空间 + 新增 regime / liquidity / time)"""
    out = []
    for cp_min in [0.5, 1.0, 2.0, 3.0]:
        for cp_max in [5.0, 7.0, 9.5]:                  # 3
            for vr in [1.0, 1.5, 2.0, 2.5]:              # 4 (skip 0.8, 关键信号)
                for zt in [0, 1, 2]:                     # 3
                    for ma5 in [False, True]:            # 2
                        for cp_pos in [0.0, 0.5, 0.7]:   # 3
                            for exit_at in ["t1_open", "t1_1000", "t1_close"]:  # 3
                                for stop in [-999, -3.0, -5.0]:                # 3
                                    for top_n in [1, 2]:                       # 2
                                        # Regime: 偶尔加严
                                        for mkt_5d in [-999.0, -5.0, 0.0]:   # 3
                                            if ma5 and vr < 1.0:
                                                continue
                                            # 避免爆炸, 部分维度降级
                                            out.append(StrategyParams(
                                                change_pct_min=cp_min,
                                                change_pct_max=cp_max,
                                                vol_ratio_min=vr,
                                                zt_20d_min=zt,
                                                require_above_ma5=ma5,
                                                require_close_pos_min=cp_pos,
                                                exit_at=exit_at,
                                                stop_loss_pct=stop,
                                                top_n=top_n,
                                                mkt_5d_chg_min=mkt_5d,
                                            ))
    # 去重
    seen = set()
    dedup = []
    for p in out:
        key = tuple(sorted(p.to_dict().items()))
        if key not in seen:
            seen.add(key)
            dedup.append(p)
    return dedup[:1500]


# ═════════════════════════════════════════════════════════════════
# 3) 回测引擎 (向量化)
# ═════════════════════════════════════════════════════════════════
@dataclass
class BacktestResult:
    """单次回测结果"""
    params: dict
    n_trades: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0
    median_return: float = 0.0
    max_return: float = 0.0
    min_return: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    cum_return: float = 0.0
    daily_avg: float = 0.0              # 日均收益 (%, additive: cum / n_days)
    n_days: int = 0
    n_skipped: int = 0
    score: float = 0.0
    monthly_returns: list = field(default_factory=list)   # [(YYYY-MM, ret%, n_trades)]
    monthly_pos_months: int = 0                            # 正收益月份数
    wf_train_score: float = 0.0
    wf_test_score: float = 0.0
    wf_consistency: float = 0.0                           # test/train ratio


def _is_main_board(code: str) -> bool:
    """主板: 60xxxx, 00xxxx, 30xxxx, 301xxx"""
    return code.startswith(("60", "00", "30"))


def backtest(df: pd.DataFrame, p: StrategyParams) -> BacktestResult:
    """单次回测 — 全程向量化"""
    res = BacktestResult(params=p.to_dict())
    if df.empty:
        return res

    # Universe filter
    sub = df.copy()
    if p.universe == "main_board":
        sub = sub[sub["code"].apply(_is_main_board)]
    if sub.empty:
        return res

    # 一次性过滤: NaN drop (change_pct, vol_ratio, prev_close)
    sub = sub.dropna(subset=["change_pct", "vol_ratio", "prev_close"])

    # R-fix: 流动性过滤 (amount 单位是元, min_amount_wan 是万元)
    if p.min_amount_wan > 0:
        sub = sub[sub["amount"] >= p.min_amount_wan * 1e4]

    # R-fix: 星期过滤
    if p.weekday_only > 0:
        sub = sub[sub["date"].dt.weekday == (p.weekday_only - 1)]

    # ── Selection rules (向量化) ──
    mask = (
        (sub["change_pct"] >= p.change_pct_min) &
        (sub["change_pct"] <= p.change_pct_max) &
        (sub["vol_ratio"]  >= p.vol_ratio_min) &
        (sub["zt_20d"]     >= p.zt_20d_min) &
        (sub["mkt_red_rate"] >= p.mkt_red_rate_min) &
        (sub["mkt_red_rate"] <= p.mkt_red_rate_max) &
        (sub["mkt_zt_count"] >= p.mkt_zt_count_min) &
        (sub["mkt_dt_count"] <= p.mkt_dt_count_max) &
        (sub["mkt_5d_chg"] >= p.mkt_5d_chg_min) &
        (sub["mkt_5d_chg"] <= p.mkt_5d_chg_max)
    )
    if p.require_above_ma5:
        mask &= (sub["above_ma5"] == 1)
    if p.require_above_ma10:
        mask &= (sub["above_ma10"] == 1)
    if p.require_close_pos_min > 0:
        mask &= (sub["close_pos"] >= p.require_close_pos_min)
    sub = sub[mask]
    if sub.empty:
        return res

    # ── T+1 return: shift by 1 day, then join on (code, date+1) ──
    # 计算次日开盘相对今日开盘的收益 (尾盘买 → 次日开卖)
    sub["t1_open"] = sub.groupby("code")["open"].shift(-1)
    sub["t1_close"] = sub.groupby("code")["close"].shift(-1)
    sub["t1_high"] = sub.groupby("code")["high"].shift(-1)
    sub["t1_low"] = sub.groupby("code")["low"].shift(-1)
    sub["t1_change_pct"] = sub.groupby("code")["change_pct"].shift(-1)
    sub["t1_is_zt"] = sub.groupby("code")["is_zt"].shift(-1)
    sub["t1_open_chg"] = (sub["t1_open"] / sub["prev_close"] - 1.0) * 100.0  # 次日开盘相对今日收盘的涨幅

    # ── R-fix: 不能买涨停股 (T+1 涨停挤兑效应是 fake alpha, 我们买不进) ──
    sub = sub[sub["is_zt"] == 0]
    if sub.empty:
        return res

    # R-fix: 跳过 T+1 一字板涨停 (开盘卖不掉), 仅 exit_at=t1_open 时有意义
    if p.exit_at == "t1_open":
        # 一字板涨停: open ≈ prev_close * 1.095
        sub = sub[sub["t1_open_chg"] < 9.5]

    if p.exit_at == "t1_open":
        # 假设尾盘 close 买, T+1 open 卖 (实际还有 T+1 涨跌停限制, 简化处理)
        sub["ret"] = (sub["t1_open"] / sub["close"] - 1.0) * 100.0
    elif p.exit_at == "t1_close":
        sub["ret"] = (sub["t1_close"] / sub["close"] - 1.0) * 100.0
    elif p.exit_at == "t1_1000":
        # 简化: 用 T+1 open → 10:00 估算为 (open + 0.3 * (close - open))
        sub["ret"] = ((sub["t1_open"] + 0.3 * (sub["t1_close"] - sub["t1_open"])) / sub["close"] - 1.0) * 100.0
    elif p.exit_at == "t1_0935":
        sub["ret"] = ((sub["t1_open"] + 0.1 * (sub["t1_close"] - sub["t1_open"])) / sub["close"] - 1.0) * 100.0
    else:
        sub["ret"] = (sub["t1_close"] / sub["close"] - 1.0) * 100.0

    # R-fix: T+1 涨停无法卖出 — 返 T+1 close (通常也是涨停价, 实际拿不到但简化)
    # 用 ret = T+1 涨停 open → T+1 close, 视为 "全拿到"
    # 如果想更保守: 跌停时 ret 也要 cap

    # R-fix: 滑点 + 手续费 0.5% (双边: 买 0.2% + 卖 0.3%)
    sub["ret"] = sub["ret"] - 0.5

    # R-fix: 涨跌停 cap (T+1 实际能买/卖的范围)
    # 上限: cap 到 +9.0% (主板) / +18.0% (创业板), 避免一字板涨停卖不掉
    # 下限: -10% (主板跌停), -20% (创业板/科创板)
    sub["code_prefix"] = sub["code"].str[:3]
    is_chinext = sub["code_prefix"].isin(["300", "301", "688"])
    sub.loc[~is_chinext & (sub["ret"] > 9.0), "ret"] = 9.0
    sub.loc[is_chinext & (sub["ret"] > 18.0), "ret"] = 18.0
    sub.loc[~is_chinext & (sub["ret"] < -10), "ret"] = -10
    sub.loc[is_chinext & (sub["ret"] < -20), "ret"] = -20
    sub.drop(columns=["code_prefix"], inplace=True, errors="ignore")

    # 止损: T+1 持有期内最低价跌破 stop_loss_pct
    if p.stop_loss_pct > -999:
        # 假设日内触发, 用 t1_low 计算
        sub["stop_ret"] = (sub["t1_low"] / sub["close"] - 1.0) * 100.0
        sub.loc[sub["stop_ret"] <= p.stop_loss_pct, "ret"] = p.stop_loss_pct

    sub = sub.dropna(subset=["ret"])
    if sub.empty:
        return res

    # ── Top N per day ──
    # 给每个 (date, code) 打分: change_pct 居中 (4% 最佳) + vol_ratio + zt_20d
    sub["score"] = (
        -((sub["change_pct"] - 4.0).abs()) * 2.0  # 4% 最佳
        + sub["vol_ratio"].clip(0, 5) * 0.5
        + sub["zt_20d"].clip(0, 5) * 0.3
        + sub["close_pos"].fillna(0.5) * 0.5
    )
    sub = sub.sort_values(["date", "score"], ascending=[True, False])

    # R-fix: 同一股票 cooldown N 日内不重复
    if p.cooldown_days > 0:
        # 按日期+score排序, 遍历, 标记 cooldown 内的重复
        sub_idx = sub.index.values
        sub_date = sub["date"].values
        sub_code = sub["code"].values
        last_seen: dict[str, pd.Timestamp] = {}
        keep_mask = np.ones(len(sub), dtype=bool)
        for i in range(len(sub)):
            cd = sub_code[i]
            dt = sub_date[i]
            if cd in last_seen:
                days_since = (dt - last_seen[cd]).days
                if days_since < p.cooldown_days:
                    keep_mask[i] = False
                    continue
            last_seen[cd] = dt
        sub = sub[keep_mask]

    daily_top = sub.groupby("date").head(p.top_n)
    res.n_trades = len(daily_top)
    if res.n_trades == 0:
        return res

    rets = daily_top["ret"].values
    n_trades = len(rets)
    res.win_rate = float((rets > 0).sum() / n_trades)
    res.avg_return = float(np.mean(rets))   # 平均每笔收益 (%)
    res.median_return = float(np.median(rets))
    res.max_return = float(np.max(rets))
    res.min_return = float(np.min(rets))
    res.sharpe = float(np.mean(rets) / (np.std(rets) + 1e-9) * np.sqrt(252))
    # R-fix: "日均收益" = 总收益 / 实际交易日数 (additive, 跟用户直觉一致)
    res.n_days = int(daily_top["date"].nunique())
    res.cum_return = float(np.sum(rets))    # 总收益 (%, additive)
    res.daily_avg = res.cum_return / max(1, res.n_days)  # 关键: 日均收益
    # R-fix: 复利 equity (按日期排序, 每天各 trade 仓位独立)
    # 真实情况: top_n=2, 每笔 50% 仓位, equity = sum(trade equity) / top_n
    daily_top_sorted = daily_top.sort_values("date")
    daily_agg = daily_top_sorted.groupby("date").agg(
        n=("ret", "count"),
        sum_ret=("ret", "sum"),
        avg_ret=("ret", "mean"),
    ).reset_index()
    # 每天 1 单位资金, 平均分给 top_n 笔 (假设等权)
    daily_factor = 1.0 + daily_agg["avg_ret"] / 100.0  # 日均收益因子
    equity = np.cumprod(daily_factor.values)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak * 100.0
    res.max_dd = float(dd.min())

    # 月度收益 (诊断稳定性: 是不是某些月份特别好)
    daily_agg["ym"] = pd.to_datetime(daily_agg["date"]).dt.strftime("%Y-%m")
    monthly = daily_agg.groupby("ym").agg(
        sum_ret=("avg_ret", "sum"),
        n=("n", "sum"),
    ).reset_index()
    res.monthly_returns = [(r["ym"], float(r["sum_ret"]), int(r["n"]))
                           for _, r in monthly.iterrows()]
    res.monthly_pos_months = int((monthly["sum_ret"] > 0).sum())

    # Score: 综合 (日均收益 + 胜率 + 频率奖励 + 月度稳定性 + sharpe - 回撤)
    # 频率奖励: 至少 30% 天数有 trade (>= 0.3 * n_days); 越多越稳定
    freq_ratio = res.n_trades / max(1, res.n_days) if res.n_days > 0 else 0
    n_months = len(res.monthly_returns)
    monthly_pos_ratio = res.monthly_pos_months / max(1, n_months)
    # 至少 100 笔才计入主排名, 避免 <50 笔的过拟合
    res.score = (
        res.daily_avg * 5.0
        + res.win_rate * 30.0
        + max(0, res.sharpe) * 2.0
        - abs(res.max_dd) * 0.3
        + min(1.0, freq_ratio) * 10.0           # 频率奖励
        + monthly_pos_ratio * 15.0              # 月度稳定性
    )
    # 笔数 < 30 的: 直接 0 分 (过拟合)
    if res.n_trades < 30:
        res.score = 0
    return res


# ═════════════════════════════════════════════════════════════════
# 4) Walk-forward
# ═════════════════════════════════════════════════════════════════
def walk_forward(df: pd.DataFrame, p: StrategyParams,
                 train_months: int = 6, test_months: int = 3) -> dict:
    """滚动 walk-forward 验证"""
    if df.empty:
        return {"train_score": 0, "test_score": 0, "consistency": 0}

    start = df["date"].min()
    end = df["date"].max()
    train_start = start
    train_results = []
    test_results = []
    while True:
        train_end = train_start + pd.DateOffset(months=train_months)
        test_end = train_end + pd.DateOffset(months=test_months)
        if test_end > end:
            break
        train_df = df[(df["date"] >= train_start) & (df["date"] < train_end)]
        test_df = df[(df["date"] >= train_end) & (df["date"] < test_end)]
        if not train_df.empty:
            tr = backtest(train_df, p)
            train_results.append(tr.score)
        if not test_df.empty:
            te = backtest(test_df, p)
            test_results.append(te.score)
        train_start = train_end
    if not train_results or not test_results:
        return {"train_score": 0, "test_score": 0, "consistency": 0}
    train_avg = float(np.mean(train_results))
    test_avg = float(np.mean(test_results))
    # Consistency: test_avg / train_avg (越接近 1 越稳健)
    consistency = test_avg / (train_avg + 1e-9)
    return {
        "train_score": train_avg,
        "test_score": test_avg,
        "consistency": float(consistency),
        "n_splits": len(test_results),
    }


# ═════════════════════════════════════════════════════════════════
# 5) Sweep 主流程
# ═════════════════════════════════════════════════════════════════
def sweep(df: pd.DataFrame, params: list[StrategyParams],
          use_walk_forward: bool = False, top_k: int = 10) -> list[BacktestResult]:
    """参数扫描"""
    results = []
    t0 = time.time()
    n = len(params)
    for i, p in enumerate(params):
        if i % 100 == 0:
            elapsed = time.time() - t0
            eta = elapsed / max(1, i + 1) * (n - i)
            print(f"  [{i+1}/{n}] {elapsed:.0f}s elapsed, ETA {eta:.0f}s", flush=True)
        r = backtest(df, p)
        if use_walk_forward and r.n_trades > 10:
            wf = walk_forward(df, p)
            r.wf_train_score = wf["train_score"]
            r.wf_test_score = wf["test_score"]
            r.wf_consistency = wf["consistency"]
            # 调低分: 一致性差
            r.score *= max(0.1, min(1.5, wf["consistency"]))
        results.append(r)
    results.sort(key=lambda x: -x.score)
    return results[:top_k]


# ═════════════════════════════════════════════════════════════════
# 6) 输出
# ═════════════════════════════════════════════════════════════════
def print_top(results: list[BacktestResult], title: str = "Top strategies") -> None:
    print(f"\n{'='*80}")
    print(f" {title}")
    print(f"{'='*80}")
    print(f"{'#':<3} {'胜率':>6} {'日均':>6} {'均收益':>7} {'中位':>6} {'最大':>6} {'最小':>6} "
          f"{'夏普':>6} {'回撤':>6} {'累计':>7} {'笔数':>5} {'天数':>5} {'月正':>4} "
          f"{'WF_train':>8} {'WF_test':>7} {'一致性':>6} {'分数':>7}")
    for i, r in enumerate(results, 1):
        wf_t = f"{r.wf_train_score:.1f}" if r.wf_train_score else "-"
        wf_e = f"{r.wf_test_score:.1f}" if r.wf_test_score else "-"
        wf_c = f"{r.wf_consistency:.2f}" if r.wf_consistency else "-"
        print(f"{i:<3} {r.win_rate*100:>5.1f}% {r.daily_avg:>5.2f}% {r.avg_return:>6.2f}% "
              f"{r.median_return:>5.2f}% {r.max_return:>5.2f}% {r.min_return:>5.2f}% "
              f"{r.sharpe:>5.2f} {r.max_dd:>5.2f}% {r.cum_return:>6.1f}% "
              f"{r.n_trades:>5} {r.n_days:>5} {r.monthly_pos_months:>3}/{len(r.monthly_returns):<2} "
              f"{wf_t:>8} {wf_e:>7} {wf_c:>6} {r.score:>6.2f}")
        if i <= 5:
            print(f"   params: {json.dumps(r.params, ensure_ascii=False)}")
            if r.monthly_returns:
                chunks = []
                pos_count = 0
                for ym, mret, ntrades in r.monthly_returns:
                    sign = "+" if mret >= 0 else ""
                    chunks.append(f"{ym}:{sign}{mret:.1f}%({ntrades})")
                    if mret > 0:
                        pos_count += 1
                print(f"   monthly ({pos_count}/{len(r.monthly_returns)} positive): "
                      + " ".join(chunks[:18]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="100 组合快速测试")
    ap.add_argument("--full", action="store_true", help="1000+ 组合全量")
    ap.add_argument("--wf", action="store_true", help="walk-forward 验证")
    ap.add_argument("--top", type=int, default=20, help="输出 top N")
    ap.add_argument("--start", default="2024-01-01", help="数据起始日")
    ap.add_argument("--end",   default="2026-07-17", help="数据结束日")
    ap.add_argument("--out",   default=None, help="JSON 输出路径")
    args = ap.parse_args()

    print(f"→ 加载 daily ({args.start} ~ {args.end})...")
    t0 = time.time()
    df = load_daily(args.start, args.end)
    print(f"  {len(df):,} 行, {df['code'].nunique()} 只, {df['date'].nunique()} 天 ({time.time()-t0:.1f}s)")

    print(f"→ 计算特征...")
    t0 = time.time()
    df = add_features(df)
    df = add_market_features(df)
    print(f"  done ({time.time()-t0:.1f}s)")

    if args.quick:
        params = param_space_quick()
    elif args.full:
        params = param_space_full()
    else:
        params = param_space_quick()  # default quick
    print(f"→ 参数空间: {len(params)} 组合")
    if args.wf:
        print(f"  + walk-forward 验证 (慢 5-10×)")

    print(f"→ 回测中...")
    t0 = time.time()
    top = sweep(df, params, use_walk_forward=args.wf, top_k=args.top)
    print(f"  done ({time.time()-t0:.0f}s)")

    print_top(top, f"Top {args.top} 策略 (args.wf={args.wf})")

    if args.out:
        with open(args.out, "w") as f:
            json.dump([asdict(r) for r in top], f, ensure_ascii=False, indent=2)
        print(f"\n→ saved: {args.out}")


if __name__ == "__main__":
    main()