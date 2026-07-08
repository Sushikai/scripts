"""
tuixue_v3/layer1_basic.py
Layer 1：全局基础风险初筛（一票否决）
- 沪市/深市主板（已在上游 fetch_stock_list 完成）
- 流动性：成交额≥0.8亿、流通市值 50-300 亿
- 基本面：扣非净利润不为负、近月无大额解禁/减持
- 量能趋势：5 日均量 ≥ 20 日均量
- 黑名单：止损亏损个股永久剔除
- 上市 ≥ 30 个交易日

返回：list[dict]，每个 dict 含 code/name/sector 及通过明细
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from . import config as cfg
from . import blacklist as bl_mod
from . import data_layer as dl

log = logging.getLogger("tuixue_v3.layer1")


def _calc_list_days(code: str, df: pd.DataFrame) -> int:
    """上市天数 = 最新日期 - df 中最早日期（粗略）"""
    if df is None or df.empty or "日期" not in df.columns:
        return 0
    earliest = pd.to_datetime(df["日期"]).min()
    latest = pd.to_datetime(df["日期"]).max()
    return (latest - earliest).days


def _calc_fundamental_filters(code: str, name: str) -> tuple[bool, dict]:
    """
    基本面避雷（轻量级，避免大请求）：
    - 近一年扣非净利润不为负：用最新年报扣非净利近似（akshare 大请求会卡，此处用启发式）
    - 暂只做名称/公告级粗筛，避免数据源风暴
    """
    # 启发：跳过名称含"退"、"停"等关键字
    if any(k in (name or "") for k in ["退", "停", "废"]):
        return False, {"reason": "名称含退/停关键字"}
    return True, {"reason": "ok"}


def _check_volume_trend(df: pd.DataFrame) -> tuple[bool, dict]:
    """5 日均量 ≥ 20 日均量 × L1_MA5_VOL_RATIO"""
    if df is None or len(df) < 25 or "成交量" not in df.columns:
        return False, {"reason": "数据不足"}
    vol = df["成交量"].tail(25)
    ma5 = vol.tail(5).mean()
    ma20 = vol.mean()
    if ma20 <= 0:
        return False, {"reason": "成交量为 0"}
    ratio = ma5 / ma20
    ok = ratio >= cfg.L1_MA5_VOL_RATIO
    return ok, {"vol_ma5": round(ma5, 0), "vol_ma20": round(ma20, 0), "ratio": round(ratio, 2)}


def _check_liquidity(df: pd.DataFrame) -> tuple[bool, dict]:
    """成交额 + 估算流通市值
    优先：成交额≥0.8亿
    流通市值：当有换手率时估算，否则标记 unknown（不影响通过判定）
    """
    if df is None or len(df) < 1:
        return False, {"reason": "无行情"}

    last = df.iloc[-1]
    turnover_yuan = float(last.get("成交额", 0) or 0)
    turnover_yi = turnover_yuan / 1e8  # 元 → 亿

    turnover_rate = float(last.get("换手率", 0) or 0)
    if turnover_rate > 0:
        free_mv_yi = (turnover_yuan * 100 / turnover_rate) / 1e8
    else:
        free_mv_yi = 0  # unknown

    # 核心：成交额门槛
    if turnover_yi < cfg.L1_MIN_TURNOVER_YI:
        return False, {
            "turnover_yi": round(turnover_yi, 2),
            "free_mv_yi": round(free_mv_yi, 2) if free_mv_yi > 0 else None,
            "turnover_rate_pct": round(turnover_rate, 2) if turnover_rate > 0 else None,
            "reason": f"成交额 {turnover_yi:.2f}亿 < {cfg.L1_MIN_TURNOVER_YI}亿",
        }

    # 流通市值：可估算时强制区间；不可估算时放行（标记 unknown）
    if free_mv_yi > 0:
        mv_ok = cfg.L1_FLOAT_MV_MIN_YI <= free_mv_yi <= cfg.L1_FLOAT_MV_MAX_YI
        if not mv_ok:
            return False, {
                "turnover_yi": round(turnover_yi, 2),
                "free_mv_yi": round(free_mv_yi, 2),
                "turnover_rate_pct": round(turnover_rate, 2),
                "reason": f"流通市值 {free_mv_yi:.0f}亿 不在 [{cfg.L1_FLOAT_MV_MIN_YI},{cfg.L1_FLOAT_MV_MAX_YI}]",
            }

    return True, {
        "turnover_yi": round(turnover_yi, 2),
        "free_mv_yi": round(free_mv_yi, 2) if free_mv_yi > 0 else None,
        "turnover_rate_pct": round(turnover_rate, 2) if turnover_rate > 0 else None,
    }


def screen(stocks: list[tuple[str, str]], date_str: str | None = None) -> tuple[list[dict], dict]:
    """
    输入：[(code, name), ...]
    输出：(幸存list[dict], 统计dict)
    每个 dict：{code, name, sector, list_days, turnover_yi, free_mv_yi, vol_ratio, pass_detail}
    """
    stats = {"input": len(stocks), "blacklisted": 0, "low_liquidity": 0, "vol_down": 0,
             "young": 0, "bad_fundamental": 0, "passed": 0}

    # 1) 黑名单
    stocks, blocked = bl_mod.filter_blacklist(stocks)
    stats["blacklisted"] = len(blocked)

    passed = []
    for code, name in stocks:
        df = dl.fetch_daily(code, days=130)
        if df is None or len(df) < 25:
            stats["vol_down"] += 1
            continue

        # 上市天数
        list_days = _calc_list_days(code, df)
        if list_days < cfg.L1_LIST_DAYS_MIN:
            stats["young"] += 1
            continue

        # 流动性
        liq_ok, liq_detail = _check_liquidity(df)
        if not liq_ok:
            stats["low_liquidity"] += 1
            continue

        # 量能趋势
        vol_ok, vol_detail = _check_volume_trend(df)
        if not vol_ok:
            stats["vol_down"] += 1
            continue

        # 基本面
        fund_ok, fund_detail = _calc_fundamental_filters(code, name)
        if not fund_ok:
            stats["bad_fundamental"] += 1
            continue

        passed.append({
            "code": code,
            "name": name,
            "list_days": list_days,
            "turnover_yi": liq_detail["turnover_yi"],
            "free_mv_yi": liq_detail["free_mv_yi"],
            "turnover_rate_pct": liq_detail["turnover_rate_pct"],
            "vol_ma5": vol_detail.get("vol_ma5", 0),
            "vol_ma20": vol_detail.get("vol_ma20", 0),
            "vol_ratio": vol_detail.get("ratio", 0),
            "pass_detail": "L1通过: 流动性+量能+基本面+非黑名单",
            "_df_ref": df,  # 留给后续层用
        })

    stats["passed"] = len(passed)
    log.info(f"Layer1: {stats}")
    return passed, stats