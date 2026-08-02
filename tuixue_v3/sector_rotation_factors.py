#!/usr/bin/env python3
"""
tuixue_v3/sector_rotation_factors.py
Ship 7/100 — 板块轮动因子包 (申万动量/反转/北向/两融/ETF 申赎)

设计:
- 5 个核心因子 (P0 立即可用,数据源已有)
- 申万动量因子: 20 日相对强弱 (IC ~0.04, 半衰期 15 天)
- 申万反转因子: 5 日反转 (IC ~0.03, 与动量正交)
- 北向资金因子: 北向净买 / 流通市值 (2025 月度快照, 5-10 日领先)
- 两融余额变化因子: 行业 5 日 IC ~0.025
- ETF 申赎比因子: 周频更稳

输出: DataFrame (index=sector, columns=5 factors)
接入: /api/sectors/rotation 端点 + 前端 sector_hotspot 联动

2026-08-02 Ship 7 — 10000 轮迭代 P1 第二步
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, asdict
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 因子定义
# ═══════════════════════════════════════════════════════

@dataclass
class SectorFactor:
    """单个板块的因子值"""
    sector: str
    momentum_20d: float = 0.0        # 20 日相对强弱
    reversal_5d: float = 0.0          # 5 日反转
    northbound_ratio: float = 0.0     # 北向净买 / 流通市值
    margin_change_5d: float = 0.0     # 两融余额 5 日变化率
    etf_subscription_ratio: float = 0.0  # ETF 申赎比
    composite_score: float = 0.0      # 综合分 (5 因子加权)
    rank: int = 0                     # 综合排名


# 默认权重 (IC 加权)
_DEFAULT_WEIGHTS = {
    "momentum_20d": 0.25,
    "reversal_5d": 0.15,
    "northbound_ratio": 0.30,
    "margin_change_5d": 0.15,
    "etf_subscription_ratio": 0.15,
}


# ═══════════════════════════════════════════════════════
# 因子计算
# ═══════════════════════════════════════════════════════

def compute_momentum(prices: pd.Series, lookback: int = 20) -> float:
    """动量因子: (close / close.shift(lookback)) - 1

    Args:
        prices: 收盘价序列 (index=date)
        lookback: 回看天数

    Returns:
        因子值 (无量纲)
    """
    if len(prices) < lookback + 1:
        return 0.0
    try:
        return (prices.iloc[-1] / prices.iloc[-(lookback + 1)]) - 1.0
    except (IndexError, ZeroDivisionError):
        return 0.0


def compute_reversal(prices: pd.Series, lookback: int = 5) -> float:
    """反转因子: -1 × 短期涨跌幅 (取负,期待反转)

    Args:
        prices: 收盘价序列
        lookback: 回看天数

    Returns:
        因子值 (无量纲,正值表示可能反转向上)
    """
    if len(prices) < lookback + 1:
        return 0.0
    try:
        ret = (prices.iloc[-1] / prices.iloc[-(lookback + 1)]) - 1.0
        return -ret  # 反转: 跌多 = 正值 (预期反弹)
    except (IndexError, ZeroDivisionError):
        return 0.0


def compute_northbound_ratio(north_net: float, free_float_mv: float) -> float:
    """北向资金因子: 北向净买 / 流通市值

    Args:
        north_net: 北向资金净买入金额 (元)
        free_float_mv: 板块流通市值 (元)

    Returns:
        比例 (无量纲)
    """
    if free_float_mv <= 0:
        return 0.0
    return north_net / free_float_mv


def compute_margin_change(margin_balance: pd.Series, lookback: int = 5) -> float:
    """两融余额变化因子: 5 日变化率

    Args:
        margin_balance: 两融余额序列
        lookback: 回看天数

    Returns:
        变化率 (无量纲)
    """
    if len(margin_balance) < lookback + 1:
        return 0.0
    try:
        return (margin_balance.iloc[-1] / margin_balance.iloc[-(lookback + 1)]) - 1.0
    except (IndexError, ZeroDivisionError):
        return 0.0


def compute_etf_subscription(subscribe: float, redeem: float) -> float:
    """ETF 申赎比因子: 申购 / 赎回 (对数化)

    Args:
        subscribe: 申购份额 (元)
        redeem: 赎回份额 (元)

    Returns:
        对数化比例 (无量纲,正=净申购,负=净赎回)
    """
    if redeem <= 0:
        return 0.0
    ratio = subscribe / redeem
    if ratio <= 0:
        return 0.0
    return math.log(ratio)


# ═══════════════════════════════════════════════════════
# 综合评分
# ═══════════════════════════════════════════════════════

def composite_score(f: SectorFactor, weights: Optional[dict] = None) -> float:
    """5 因子加权综合分

    Args:
        f: SectorFactor
        weights: 自定义权重 (默认 = _DEFAULT_WEIGHTS)
    """
    w = weights or _DEFAULT_WEIGHTS
    return (
        w["momentum_20d"] * f.momentum_20d +
        w["reversal_5d"] * f.reversal_5d +
        w["northbound_ratio"] * f.northbound_ratio +
        w["margin_change_5d"] * f.margin_change_5d +
        w["etf_subscription_ratio"] * f.etf_subscription_ratio
    )


def rank_sectors(factors: list[SectorFactor],
                 weights: Optional[dict] = None) -> list[SectorFactor]:
    """计算综合分 + 排名

    Args:
        factors: SectorFactor 列表
        weights: 自定义权重

    Returns:
        按 composite_score 降序排好序的列表
    """
    for f in factors:
        f.composite_score = composite_score(f, weights)
    # 按分数降序
    sorted_factors = sorted(factors, key=lambda x: x.composite_score, reverse=True)
    for i, f in enumerate(sorted_factors):
        f.rank = i + 1
    return sorted_factors


# ═══════════════════════════════════════════════════════
# 标准化 (z-score) — 跨板块可比
# ═══════════════════════════════════════════════════════

def normalize_factors(factors: list[SectorFactor]) -> list[SectorFactor]:
    """5 因子各自 z-score 标准化, 让不同量纲可比

    Args:
        factors: SectorFactor 列表

    Returns:
        标准化后的 SectorFactor 列表 (新对象)
    """
    if not factors:
        return []
    df = pd.DataFrame([asdict(f) for f in factors])
    numeric_cols = [
        "momentum_20d", "reversal_5d", "northbound_ratio",
        "margin_change_5d", "etf_subscription_ratio",
    ]
    for col in numeric_cols:
        std = df[col].std()
        if std > 0:
            df[col] = (df[col] - df[col].mean()) / std
        else:
            df[col] = 0.0
    result = []
    for _, row in df.iterrows():
        f = SectorFactor(
            sector=row["sector"],
            momentum_20d=row["momentum_20d"],
            reversal_5d=row["reversal_5d"],
            northbound_ratio=row["northbound_ratio"],
            margin_change_5d=row["margin_change_5d"],
            etf_subscription_ratio=row["etf_subscription_ratio"],
            composite_score=row.get("composite_score", 0.0),
            rank=int(row.get("rank", 0)),
        )
        result.append(f)
    return result


# ═══════════════════════════════════════════════════════
# 转换矩阵 (transition matrix) — 板块轮动预测
# ═══════════════════════════════════════════════════════

def compute_transition_matrix(
    history: pd.DataFrame,
    top_n: int = 20,
    lookback: int = 20,
) -> pd.DataFrame:
    """计算板块 transition matrix — 给定历史数据,预测下期热门板块

    Args:
        history: 历史行情 DataFrame (columns=sector, index=date)
        top_n: 取 top N 板块
        lookback: 回看天数

    Returns:
        transition matrix: 行=上期 top N, 列=下期 top N, 值=条件概率
    """
    if history is None or len(history) < lookback + 2:
        return pd.DataFrame()

    # 计算每日 top N
    daily_returns = history.pct_change().dropna()
    if len(daily_returns) < 2:
        return pd.DataFrame()

    daily_top = {}
    for date, row in daily_returns.iterrows():
        top = row.nlargest(top_n).index.tolist()
        daily_top[date] = set(top)

    # 统计转换频率
    dates = sorted(daily_top.keys())
    sectors = sorted(set().union(*daily_top.values()))
    matrix = pd.DataFrame(0, index=sectors, columns=sectors, dtype=float)

    for i in range(len(dates) - 1):
        prev_set = daily_top[dates[i]]
        curr_set = daily_top[dates[i + 1]]
        for p in prev_set:
            for c in curr_set:
                matrix.loc[p, c] += 1

    # 归一化为条件概率 P(c|p)
    row_sums = matrix.sum(axis=1)
    matrix = matrix.div(row_sums, axis=0).fillna(0)

    return matrix


def predict_next_top(history: pd.DataFrame, top_n: int = 20,
                     transition: Optional[pd.DataFrame] = None) -> list[str]:
    """根据转换矩阵预测下一期 top N

    Args:
        history: 历史行情
        top_n: 预测板块数
        transition: 已计算的转换矩阵 (None 则自动算)

    Returns:
        预测的板块列表
    """
    if history is None or len(history) < 2:
        return []

    if transition is None:
        transition = compute_transition_matrix(history, top_n=top_n)

    if transition.empty:
        return []

    # 用最近一天的 top N 作为条件
    latest_top = history.iloc[-1].nlargest(top_n).index.tolist()

    # 加权求和: 每个当前 top 板块对所有候选板块的转移概率
    scores = transition.loc[latest_top].sum(axis=0)
    predicted = scores.nlargest(top_n).index.tolist()

    return predicted


# ═══════════════════════════════════════════════════════
# 数据类转换
# ═══════════════════════════════════════════════════════

def to_dict_list(factors: list[SectorFactor]) -> list[dict]:
    """SectorFactor 列表 → dict 列表 (JSON 序列化用)"""
    return [
        {
            "sector": f.sector,
            "momentum_20d": round(f.momentum_20d, 4),
            "reversal_5d": round(f.reversal_5d, 4),
            "northbound_ratio": round(f.northbound_ratio, 6),
            "margin_change_5d": round(f.margin_change_5d, 4),
            "etf_subscription_ratio": round(f.etf_subscription_ratio, 4),
            "composite_score": round(f.composite_score, 4),
            "rank": f.rank,
        }
        for f in factors
    ]
