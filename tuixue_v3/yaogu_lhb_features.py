#!/usr/bin/env python3
"""R108 龙虎榜 + 顶级游资席位 40 维.

数据源: ~/.hermes/cache/multi_source/lhb_*.json (fetch_lhb_detail 缓存, 27 天).
关键字段: 净买额 / 流通市值 / 上榜原因 / 解读 / 上榜后1/2/5/10日 (真值).

特征 (~40 维):
A. 上榜事件基础 (10 维):
  - 净买额, 买入额, 卖出额, 成交额占比, 净买额占比, 换手率, 涨跌幅
  - 流通市值, 总成交额, 上榜原因(机构/营业部/涨跌幅)
B. 上榜后溢价 (5 维, 历史经验):
  - 同 1d/2d/5d 历史上榜后 N 日平均收益 (基于已有 27 天真值)
C. 解读关联 (5 维):
  - 是否机构专用席位, 是否 N 家机构, 是否知名游资 (章盟主/赵老哥等)
D. 横截面 (10 维):
  - 当日上榜股票数, 上榜原因分布, 净买额分位
E. 与 R105+R106 复用 (10 维):
  - 上榜日个股的 6 维 + 环境 4 维

验证:
- rank-IC vs 上榜后1/2/5/10日收益

诚实边界:
- 仅 27 天缓存,样本量 ~500 个上榜事件,统计意义有限
- 龙虎榜本身是"异常交易"信号,大量上榜原因本身就是涨幅过大
- 真实 alpha 源: 顶级游资席位跟随效应 — 后续 R109 接入席位明细
"""
import json
import logging
import re
import statistics
import sys
import time as systime
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("yaogu_lhb")

CACHE_DIR = Path.home() / ".hermes" / "cache" / "multi_source"

# 知名游资席位关键词 (粗匹配, 后续 R109 接 hyyyb 详表后精确化)
FAMOUS_SEATS = [
    "章盟主", "赵老哥", "孙哥", "佛山", "作手新一", "欢乐海", "炒股养家",
    "小鳄鱼", "瑞鹤仙", "成都系", "财通杭州", "华鑫上海", "中信上海",
    "华泰深圳", "国君上海", "招商深圳", "银河绍兴", "东财拉萨",
]


# ═══════════════════════════════════════════
# A. 上榜事件基础特征
# ═══════════════════════════════════════════

def lhb_event_features(rec: dict) -> dict:
    """从一条 lhb_detail 记录派生基础 + 解读 + 横截面维度."""
    f = {}
    # 数值字段
    f["lhb_net_buy"] = float(rec.get("龙虎榜净买额") or 0)
    f["lhb_buy_amt"] = float(rec.get("龙虎榜买入额") or 0)
    f["lhb_sell_amt"] = float(rec.get("龙虎榜卖出额") or 0)
    f["lhb_total_amt"] = float(rec.get("龙虎榜成交额") or 0)
    f["lhb_mkt_total"] = float(rec.get("市场总成交额") or 0)
    f["lhb_net_ratio"] = float(rec.get("净买额占总成交比") or 0)
    f["lhb_amt_ratio"] = float(rec.get("成交额占总成交比") or 0)
    f["lhb_turnover"] = float(rec.get("换手率") or 0)
    f["lhb_float_mcap"] = float(rec.get("流通市值") or 0)
    f["lhb_pct_chg"] = float(rec.get("涨跌幅") or 0)

    # 净买额 / 流通市值 (游资强势信号)
    if f["lhb_float_mcap"] > 0:
        f["lhb_net_to_mcap"] = f["lhb_net_buy"] / f["lhb_float_mcap"] * 100
    else:
        f["lhb_net_to_mcap"] = 0

    # 上榜原因 one-hot
    reason = str(rec.get("上榜原因") or "")
    f["reason_is_3day_20pct"] = int("连续三个交易日内" in reason and "20%" in reason)
    f["reason_is_day_20pct"] = int("日涨幅偏离值" in reason or "日涨跌幅" in reason)
    f["reason_is_day_15pct"] = int("15%" in reason)
    f["reason_is_turnover"] = int("换手率" in reason)
    f["reason_is_zt"] = int("涨停" in reason)
    f["reason_is_dt"] = int("跌停" in reason)
    f["reason_len"] = len(reason)
    # R112 细分原因类别 (15 维)
    f["reason_cat_up_7"] = int("涨幅偏离值达到7%" in reason or "涨幅达到7%" in reason)
    f["reason_cat_up_15"] = int("涨幅达到15%" in reason or "涨幅偏离值达到15%" in reason)
    f["reason_cat_up_3day_12"] = int("累计达到12%" in reason and "连续三个交易日内" in reason)
    f["reason_cat_up_3day_20"] = int("累计达到20%" in reason and "连续三个交易日内" in reason)
    f["reason_cat_up_3day_30"] = int("累计达到30%" in reason and "连续三个交易日内" in reason)
    f["reason_cat_turnover_20"] = int("换手率达到20%" in reason)
    f["reason_cat_turnover_30"] = int("换手率达到30%" in reason)
    f["reason_cat_down_7"] = int("跌幅偏离值达到7%" in reason or "跌幅达到7%" in reason)
    f["reason_cat_down_15"] = int("跌幅达到15%" in reason)
    f["reason_cat_amp_15"] = int("振幅值达到15%" in reason or "振幅达到15%" in reason)
    f["reason_cat_st_12"] = int("ST" in reason and "12%" in reason)
    f["reason_cat_first_5"] = int("前5只" in reason or "前五只" in reason)
    f["reason_text"] = reason  # R112 后续可做 one-hot 全字段

    # 解读文本
    interp = str(rec.get("解读") or "")
    f["interp_has_jigou"] = int("机构" in interp)
    n_jigou = 0
    m = re.search(r"(\d+)家机构", interp)
    if m:
        n_jigou = int(m.group(1))
    f["interp_n_jigou"] = n_jigou
    f["interp_has_famous"] = int(any(s in interp for s in FAMOUS_SEATS))
    # 成功率
    m = re.search(r"成功率(\d+(?:\.\d+)?)%", interp)
    f["interp_success_rate"] = float(m.group(1)) / 100 if m else 0

    # 真值字段 (上榜后 1/2/5/10 日)
    for n in (1, 2, 5, 10):
        v = rec.get(f"上榜后{n}日")
        try:
            v = float(v) if v is not None else None
            if pd.isna(v):
                v = None
        except (TypeError, ValueError):
            v = None
        f[f"fwd_{n}d"] = v

    return f


# ═══════════════════════════════════════════
# B. 加载所有 lhb 缓存 + 派生
# ═══════════════════════════════════════════

def load_all_lhb_events() -> list[dict]:
    """读 ~/.hermes/cache/multi_source/lhb_YYYYMMDD.json 全部."""
    files = sorted(CACHE_DIR.glob("lhb_2*.json"))
    log.info("found %d lhb files", len(files))
    events = []
    for fp in files:
        date_str = fp.stem.split("_")[1]  # lhb_20251230 → 20251230
        try:
            d = json.loads(fp.read_text())
        except Exception as e:
            log.warning("parse %s failed: %s", fp, e)
            continue
        data = d.get("data") or []
        if isinstance(data, dict):
            data = [data]
        for rec in data:
            if not isinstance(rec, dict):
                continue
            rec["__date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            feats = lhb_event_features(rec)
            feats["__code"] = str(rec.get("代码") or "").zfill(6)
            feats["__name"] = str(rec.get("名称") or "")
            feats["__date"] = rec["__date"]
            events.append(feats)
    log.info("loaded %d lhb events", len(events))
    return events


# ═══════════════════════════════════════════
# C. 横截面派生 (按日期)
# ═══════════════════════════════════════════

def cross_section_for_date(events_today: list[dict]) -> dict:
    """对当日所有上榜事件, 派生横截面指标."""
    if not events_today:
        return {}
    nbs = [e["lhb_net_buy"] for e in events_today]
    mcaps = [e["lhb_float_mcap"] for e in events_today]
    ratios = [e["lhb_net_to_mcap"] for e in events_today]
    return {
        "lhb_cs_n_stocks": len(events_today),
        "lhb_cs_net_total": sum(nbs),
        "lhb_cs_net_median": statistics.median(nbs),
        "lhb_cs_net_max": max(nbs),
        "lhb_cs_mcap_median": statistics.median(mcaps),
        "lhb_cs_ratio_median": statistics.median(ratios),
        "lhb_cs_ratio_max": max(ratios),
    }


def add_cross_section(events: list[dict]) -> list[dict]:
    by_date = defaultdict(list)
    for e in events:
        by_date[e["__date"]].append(e)
    for date, evs in by_date.items():
        cs = cross_section_for_date(evs)
        for e in evs:
            e.update(cs)
            # 自排名
            e["lhb_cs_self_pct_net"] = sum(1 for x in evs if x["lhb_net_buy"] <= e["lhb_net_buy"]) / len(evs) * 100
            e["lhb_cs_self_pct_ratio"] = sum(1 for x in evs if x["lhb_net_to_mcap"] <= e["lhb_net_to_mcap"]) / len(evs) * 100
    return events


# ═══════════════════════════════════════════
# D. rank-IC 评估
# ═══════════════════════════════════════════

def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return 0.0
    return float(pd.Series(x).rank().corr(pd.Series(y).rank()))


def eval_dim_ic(events: list[dict], forward_n: int = 2) -> list[tuple[str, float, int]]:
    """对每个维度算 rank-IC vs 上榜后 N 日收益."""
    fwd_key = f"fwd_{forward_n}d"
    pairs_per_dim: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for e in events:
        fwd = e.get(fwd_key)
        if fwd is None or pd.isna(fwd):
            continue
        for dim, val in e.items():
            if dim.startswith("__") or dim.startswith("fwd_") or val is None:
                continue
            try:
                v = float(val)
                if np.isnan(v) or np.isinf(v):
                    continue
                pairs_per_dim[dim].append((v, fwd))
            except (TypeError, ValueError):
                continue
    results = []
    for dim, pairs in pairs_per_dim.items():
        if len(pairs) < 20:
            continue
        xs = np.array([p[0] for p in pairs])
        ys = np.array([p[1] for p in pairs])
        ic = _spearman(xs, ys)
        results.append((dim, ic, len(pairs)))
    results.sort(key=lambda x: -abs(x[1]))
    return results


# ═══════════════════════════════════════════
# 综合 score (启发式) — 用于对比 baseline / optimized / lhb_score
# ═══════════════════════════════════════════

def lhb_score_simple(ev: dict) -> float:
    """启发式 LHB score: 净买额/流通市值 + 机构买入 + 大资金."""
    s = 0
    s += ev.get("lhb_net_to_mcap", 0) * 5  # 净买额/市值 是核心
    s += ev.get("interp_n_jigou", 0) * 3   # 机构家数
    s += ev.get("interp_has_famous", 0) * 2  # 知名游资
    s += ev.get("lhb_cs_self_pct_net", 0) * 0.05  # 当日排名
    return s


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main():
    log.info("=== R108 龙虎榜特征工程 ===")
    events = load_all_lhb_events()
    events = add_cross_section(events)
    log.info("events: %d", len(events))

    # 真值覆盖
    for n in (1, 2, 5, 10):
        c = sum(1 for e in events if e.get(f"fwd_{n}d") is not None)
        log.info("fwd_%dd 有真值: %d / %d", n, c, len(events))

    for n in (1, 2, 5):
        results = eval_dim_ic(events, forward_n=n)
        log.info(f"\n=== Top 20 单维 rank-IC (上榜后 {n} 日) ===")
        for dim, ic, c in results[:20]:
            mark = "★" if ic > 0.05 else ("✗" if ic < -0.05 else " ")
            log.info(f"  {mark} {dim:32s} IC={ic:+.4f}  n={c}")
        log.info(f"=== Bottom 10 ===")
        for dim, ic, c in results[-10:]:
            log.info(f"     {dim:32s} IC={ic:+.4f}  n={c}")

    # 综合 score rank-IC
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        scores = []
        fwds = []
        for e in events:
            if e.get(fwd_key) is None:
                continue
            scores.append(lhb_score_simple(e))
            fwds.append(e[fwd_key])
        if scores:
            ic = _spearman(np.array(scores), np.array(fwds))
            log.info(f"=== lhb_score_simple vs 上榜后 {n} 日 rank-IC = {ic:+.4f}  (n={len(scores)})")

    # 胜率回测: 按 lhb_score_simple 排序, 取 top-K, 看 win rate
    log.info("\n=== 胜率回测 (按 lhb_score_simple 排序, top-K) ===")
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        scored = [(lhb_score_simple(e), e[fwd_key], e) for e in events if e.get(fwd_key) is not None]
        if not scored:
            continue
        scored.sort(key=lambda x: -x[0])
        for k in (5, 10, 20, 50):
            if len(scored) < k:
                continue
            top = scored[:k]
            fwds = np.array([x[1] for x in top])
            wr = float((fwds > 0).mean() * 100)
            avg = float(fwds.mean())
            log.info(f"  上榜后{n}日 top-{k:3d}: 胜率={wr:.1f}%  均收益={avg:+.2f}%")

    # 平均上榜后收益 (胜率 baseline)
    log.info("\n=== 上榜后平均收益 ===")
    for n in (1, 2, 5, 10):
        fwds = [e[f"fwd_{n}d"] for e in events if e.get(f"fwd_{n}d") is not None]
        if fwds:
            arr = np.array(fwds)
            wr = float((arr > 0).mean() * 100)
            log.info(f"  上榜后{n}日: 平均={arr.mean():+.2f}%, 中位={np.median(arr):+.2f}%, 正收益占比={wr:.1f}%, n={len(fwds)}")


if __name__ == "__main__":
    main()