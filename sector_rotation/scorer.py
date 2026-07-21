#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scorer.py - 板块综合评分（基于行业板块）
"""

from datetime import datetime
from pathlib import Path
import warnings

warnings.filterwarnings("ignore")

import pandas as pd
import yaml

from data_fetcher import (
    fetch_industry_fund_flow,
    fetch_zt_pool,
    fetch_index_daily,
    safe_call,
)


def load_config(path: str = None) -> dict:
    path = path or Path(__file__).parent / "sectors.yaml"
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# ============================================================
# 工具
# ============================================================
def _normalize(x: float, vmin: float, vmax: float) -> float:
    if vmax == vmin:
        return 50.0
    norm = (x - vmin) / (vmax - vmin) * 100
    return max(0.0, min(100.0, norm))


# ============================================================
# 单板块评分
# ============================================================
def score_sector(
    sector_cfg: dict,
    industry_flow_map: dict[str, dict],
    zt_industry_map: dict[str, list[dict]],
    hs300_change: float,
    weights: dict,
) -> dict:
    """单板块单日评分
    industry_flow_map: {行业名: 资金流 dict}
    zt_industry_map: {行业名: [涨停股列表]}
    """
    matched_industries = []
    for kw in sector_cfg["industry_keywords"]:
        for ind_name in industry_flow_map.keys():
            if kw in ind_name and ind_name not in matched_industries:
                matched_industries.append(ind_name)

    # 1. 主力净流入（亿元）
    inflow_today = sum(
        float(industry_flow_map.get(n, {}).get("净额", 0) or 0)
        for n in matched_industries
    )
    # 板块涨跌幅（按资金流加权）
    sector_change = sum(
        float(industry_flow_map.get(n, {}).get("行业-涨跌幅", 0) or 0)
        for n in matched_industries
    ) / max(len(matched_industries), 1) if matched_industries else 0.0

    # 2. 涨停数（按涨停股的所属行业匹配）
    sector_zt = []
    for ind in matched_industries:
        sector_zt.extend(zt_industry_map.get(ind, []))
    sector_limit_up = len(sector_zt)

    # 3. 连板
    consecutive_max = 0
    for zt in sector_zt:
        try:
            lb = int(float(zt.get("连板数", 0) or 0))
        except (TypeError, ValueError):
            lb = 0
        consecutive_max = max(consecutive_max, lb)

    # 4. 相对强弱
    rel_strength = sector_change - hs300_change

    # 归一化
    s_fund = _normalize(inflow_today, vmin=-30, vmax=50)
    s_zt = _normalize(sector_limit_up, vmin=0, vmax=20)
    s_chain = _normalize(consecutive_max, vmin=0, vmax=8)
    s_rel = _normalize(rel_strength, vmin=-3, vmax=5)

    total = (
        s_fund * weights["fund_flow"]
        + s_zt * weights["limit_up_count"]
        + s_chain * weights["limit_chain"]
        + s_rel * weights["relative_strength"]
    )

    return {
        "板块": sector_cfg["name"],
        "代码": sector_cfg["code_alias"],
        "匹配行业": matched_industries,
        "主力净流入_亿": round(inflow_today, 2),
        "板块涨跌幅": round(sector_change, 2),
        "涨停数": sector_limit_up,
        "最高连板": consecutive_max,
        "相对强弱": round(rel_strength, 2),
        "评分": round(total, 2),
        "资金维度分": round(s_fund, 1),
        "涨停维度分": round(s_zt, 1),
        "连板维度分": round(s_chain, 1),
        "强弱维度分": round(s_rel, 1),
        "涨停明细": sector_zt[:10],  # 保留前 10
    }


# ============================================================
# 主线硬性认定
# ============================================================
def is_mainline(row: dict, thresholds: dict) -> bool:
    if row["主力净流入_亿"] < thresholds["single_day_inflow_min"]:
        return False
    if row["涨停数"] < thresholds["limit_up_count_min"]:
        return False
    if row["最高连板"] < thresholds["min_consecutive_boards"]:
        return False
    return True


# ============================================================
# 龙头/中军分层（基于涨停池）
# ============================================================
def classify_stocks(zt_list: list[dict], config: dict) -> dict:
    """情绪龙头 vs 趋势中军 vs 杂毛
    注：趋势中军此处用流通市值近似（涨停池字段里有）
    """
    sentiment_cfg = config["leader_filters"]["sentiment_leader"]
    sentiment_leaders = []
    junk = []

    for zt in zt_list:
        try:
            turnover = float(zt.get("换手率", 0) or 0)
        except (TypeError, ValueError):
            turnover = 0
        try:
            lb = int(float(zt.get("连板数", 0) or 0))
        except (TypeError, ValueError):
            lb = 0
        try:
            seal_amt = float(zt.get("封板资金", 0) or 0) / 1e8  # 元 → 亿
            amount = float(zt.get("成交额", 0) or 0) / 1e8
            seal_ratio = seal_amt / amount if amount > 0 else 0
        except Exception:
            seal_ratio = 0

        try:
            cap = float(zt.get("流通市值", 0) or 0) / 1e8
        except Exception:
            cap = 0

        rec = {
            "代码": zt.get("代码"),
            "名称": zt.get("名称"),
            "连板": lb,
            "封成比": round(seal_ratio, 2),
            "换手率": round(turnover, 2),
            "流通市值_亿": round(cap, 1),
        }

        if lb >= sentiment_cfg["consecutive_boards_min"] and \
           turnover >= sentiment_cfg["turnover_min"]:
            sentiment_leaders.append(rec)
        elif turnover < 3:  # 低换手跟风
            junk.append({**rec, "理由": "低换手跟风"})

    sentiment_leaders.sort(key=lambda x: (-x["连板"], -x["封成比"]))
    # 趋势中军：市值 Top 5
    trend = sorted(zt_list, key=lambda x: float(x.get("流通市值", 0) or 0), reverse=True)[:5]
    trend_leaders = [
        {
            "代码": t.get("代码"),
            "名称": t.get("名称"),
            "流通市值_亿": round(float(t.get("流通市值", 0) or 0) / 1e8, 1),
        }
        for t in trend
    ]

    return {
        "情绪龙头": sentiment_leaders[:5],
        "趋势中军": trend_leaders,
        "后排杂毛": junk[:10],
    }


# ============================================================
# 主流程
# ============================================================
def score_all_sectors(date_str: str, config: dict = None) -> dict:
    """对所有 4 大赛道打分"""
    if config is None:
        config = load_config()

    print(f"\n[{date_str}] 开始板块评分...")
    weights = config["scoring_weights"]
    thresholds = config["mainline_thresholds"]

    # 1. 行业资金流
    print("  [1] 行业资金流...")
    flows = fetch_industry_fund_flow("即时")
    if not flows:
        print("  ❌ 行业资金流获取失败")
        return {}
    industry_flow_map = {r["行业"]: r for r in flows}

    # 2. 涨停池
    print("  [2] 涨停池...")
    zt_pool = fetch_zt_pool(date_str)
    if zt_pool is None:
        print("  ❌ 涨停池获取失败")
        return {}
    # 按所属行业分组
    zt_industry_map: dict[str, list[dict]] = {}
    for zt in zt_pool:
        ind = zt.get("所属行业", "")
        if ind:
            zt_industry_map.setdefault(ind, []).append(zt)

    # 3. 大盘
    print("  [3] 沪深 300...")
    idx = fetch_index_daily("sh000300", days=5)
    hs300_change = 0.0
    if idx and len(idx) >= 2:
        try:
            prev = float(idx[-2]["close"])
            curr = float(idx[-1]["close"])
            hs300_change = (curr - prev) / prev * 100
        except Exception:
            pass

    # 4. 评分
    results = []
    for sector in config["sectors"]:
        score = score_sector(sector, industry_flow_map, zt_industry_map, hs300_change, weights)
        score["是否主线"] = is_mainline(score, thresholds)
        score["个股分层"] = classify_stocks(score["涨停明细"], config)
        results.append(score)
        print(f"  [{sector['name']}] 评分={score['评分']} 流入={score['主力净流入_亿']}亿 涨停={score['涨停数']} 最高连板={score['最高连板']} {'⭐ 主线' if score['是否主线'] else ''}")

    results.sort(key=lambda x: x["评分"], reverse=True)

    # 5. 整体涨停晋级率（情绪高潮判定）
    zt_total = len(zt_pool)
    continuous = sum(1 for zt in zt_pool if int(float(zt.get("连板数", 0) or 0)) >= 2)
    upgrade_rate = continuous / max(zt_total, 1) * 100

    return {
        "date": date_str,
        "hs300_change": round(hs300_change, 2),
        "zt_total": zt_total,
        "zt_continuous": continuous,
        "upgrade_rate": round(upgrade_rate, 1),
        "high_sentiment": upgrade_rate > 50,  # 情绪高潮
        "sectors": results,
        "mainlines": [r["板块"] for r in results if r["是否主线"]],
        "config": config,
    }


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import json
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-07-11"
    result = score_all_sectors(date_str)
    if not result:
        print("评分失败")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"板块评分结果 ({date_str})")
    print("=" * 60)
    print(f"沪深300: {result['hs300_change']}%")
    print(f"涨停总数: {result['zt_total']}  连板: {result['zt_continuous']}  晋级率: {result['upgrade_rate']}%")
    if result["high_sentiment"]:
        print("⚠️ 情绪高潮，禁止新开仓")
    print()
    for s in result["sectors"]:
        print(f"\n【{s['板块']}】 评分={s['评分']} {'⭐ 主线' if s['是否主线'] else ''}")
        print(f"  匹配行业: {s['匹配行业']}")
        print(f"  主力净流入: {s['主力净流入_亿']}亿  板块涨跌: {s['板块涨跌幅']}%  涨停: {s['涨停数']}只  最高连板: {s['最高连板']}")
        print(f"  维度分: 资金={s['资金维度分']} 涨停={s['涨停维度分']} 连板={s['连板维度分']} 强弱={s['强弱维度分']}")
        layer = s.get("个股分层", {})
        if layer.get("情绪龙头"):
            print(f"  情绪龙头: {[x['名称'] for x in layer['情绪龙头']]}")
        if layer.get("趋势中军"):
            print(f"  趋势中军: {[x['名称'] for x in layer['趋势中军']]}")
    print(f"\n✅ 主线板块: {result['mainlines']}")

    # 保存 JSON
    out_file = Path(__file__).parent / "reports" / f"score_{date_str}.json"
    out_file.parent.mkdir(exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 结果已保存: {out_file}")