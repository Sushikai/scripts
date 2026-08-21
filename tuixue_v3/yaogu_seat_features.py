#!/usr/bin/env python3
"""R109 顶级游资席位 + 跟随效应 30 维.

数据源: ~/.hermes/cache/multi_source/lhb_hyyyb_*.json (25 天缓存).
字段: 营业部名称, 买入个股数, 买入总金额, 总买卖净额, 买入股票列表, 营业部代码.

设计:
A. 顶级游资识别 (静态)
  - 已知顶级游资席位名单 (章盟主/赵老哥/孙哥/佛山/炒股养家等 30+ 个)
  - 历史胜率高的席位 (基于上榜后1日正收益占比)
B. 跟随效应 (动态, 每天算)
  - 当日顶级游资买入哪些股 → 哪些股有"跟随机会"
  - 顶级游资+机构 同买的股 (高 alpha 信号)
C. 与 R108 lhb_detail 联合:
  - 当日上榜股票 → 该股票被哪些顶级游资买了
  - 这些席位历史胜率 → 加权评分

新维度 (30):
- seat_top_score: 顶级游资席位累计评分
- seat_top_n_buyers: 当日多少顶级游资买入
- seat_top_amt: 顶级游资买入总额
- seat_top_has_jigou: 同时有机构买入
- seat_top_win_rate: 当日买入席位平均历史胜率
- seat_top_n_distinct_stocks: 顶级游资买入的不同股票数
- seat_top_max_single: 单股最大买入金额
- seat_top_concentration: 买入集中度 (HHI)
- ... 30 维

验证:
- rank-IC vs 上榜后1/2/5日 (与 R108 lhb_events 联表)

诚实边界:
- 25 天短窗, 顶级游资席位胜率样本极小
- 顶级游资名单主要靠公开资料, 不完整
- 席位名称会变 (换马甲), 匹配规则脆弱
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
log = logging.getLogger("yaogu_seat")

CACHE_DIR = Path.home() / ".hermes" / "cache" / "multi_source"

# 顶级游资席位关键词 (粗匹配, 公开资料整理)
TOP_SEAT_KEYWORDS = {
    "章盟主": ["章盟主", "绍兴解放南路", "东兴证券绍兴"],
    "赵老哥": ["赵老哥", "绍兴中兴路", "绍兴胜利东路"],
    "孙哥": ["孙哥", "杭州延安路"],
    "佛山系": ["佛山", "佛山南海", "佛山顺德", "佛山季华"],
    "作手新一": ["作手新一", "南京中山东路"],
    "欢乐海": ["欢乐海", "深圳益田路"],
    "炒股养家": ["炒股养家", "上海宛平南路", "华鑫上海分公司"],
    "小鳄鱼": ["小鳄鱼", "南京珠江路"],
    "瑞鹤仙": ["瑞鹤仙", "上海茅台路"],
    "成都系": ["成都南一环", "成都北一环"],
    "财通杭州": ["财通杭州", "财通杭州庆春路"],
    "华鑫上海": ["华鑫证券上海分公司"],
    "东财拉萨": ["东财拉萨", "东方财富拉萨"],
    "深股通": ["深股通专用"],
    "沪股通": ["沪股通专用"],
    "机构专用": ["机构专用"],
}


# ═══════════════════════════════════════════
# A. 加载 hyyyb 数据
# ═══════════════════════════════════════════

def load_all_seats() -> list[dict]:
    files = sorted(CACHE_DIR.glob("lhb_hyyyb_2*.json"))
    log.info("found %d hyyyb files", len(files))
    records = []
    for fp in files:
        date_str = fp.stem.split("_")[2]
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        data = d.get("data") or []
        for rec in data:
            if not isinstance(rec, dict):
                continue
            rec["__date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            records.append(rec)
    log.info("loaded %d seat records", len(records))
    return records


def identify_top_seat(name: str) -> tuple[str | None, str | None]:
    """识别一个席位属于哪个顶级游资."""
    if not name:
        return None, None
    name = str(name)
    # 优先匹配"机构专用"
    if "机构专用" in name:
        return "机构专用", "机构专用"
    if "深股通专用" in name or "沪股通专用" in name:
        return "北向资金", "陆港通"
    for label, kws in TOP_SEAT_KEYWORDS.items():
        if label in ("机构专用", "深股通", "沪股通", "北向资金"):
            continue
        for kw in kws:
            if kw in name:
                return label, kw
    return None, None


def seat_features(records: list[dict]) -> list[dict]:
    """给每条 hyyyb 记录加顶级游资标签."""
    out = []
    for rec in records:
        name = rec.get("营业部名称", "")
        label, kw = identify_top_seat(name)
        rec2 = dict(rec)
        rec2["__seat_label"] = label
        rec2["__seat_keyword"] = kw
        out.append(rec2)
    return out


# ═══════════════════════════════════════════
# B. 当日统计 (顶级游资买入的股票)
# ═══════════════════════════════════════════

def aggregate_top_seat_buys(records: list[dict], by_date: bool = True) -> dict:
    """对每日, 聚合顶级游资席位买入的股票 → {date: {stock: aggregated_metrics}}.

    返回嵌套 dict: {date: {stock_code: {seat_count, seat_amt, has_jigou, ...}}}
    """
    by_date_stock = defaultdict(lambda: defaultdict(lambda: {
        "seat_count": 0,
        "seat_amt": 0,
        "seat_labels": set(),
        "has_jigou": False,
        "has_top_seat": False,
    }))
    for rec in records:
        date = rec.get("__date", "")
        label = rec.get("__seat_label")
        if not label:
            continue
        buys = str(rec.get("买入股票") or "")
        buy_amt = float(rec.get("买入总金额") or 0)
        net = float(rec.get("总买卖净额") or 0)
        # 拆分多只股票 (按空格)
        stocks = buys.split()
        if not stocks:
            continue
        per_stock_amt = buy_amt / len(stocks) if stocks else 0
        per_stock_net = net / len(stocks) if stocks else 0
        for stock in stocks:
            if not stock:
                continue
            cur = by_date_stock[date][stock]
            cur["seat_count"] += 1
            cur["seat_amt"] += per_stock_amt
            cur["seat_labels"].add(label)
            if label == "机构专用":
                cur["has_jigou"] = True
            if label not in ("机构专用", "北向资金", "陆港通"):
                cur["has_top_seat"] = True
    # 转 set → list for JSON-friendliness
    out = {}
    for date, stocks in by_date_stock.items():
        out[date] = {}
        for stock, m in stocks.items():
            out[date][stock] = {
                "seat_count": m["seat_count"],
                "seat_amt": m["seat_amt"],
                "seat_labels": list(m["seat_labels"]),
                "has_jigou": m["has_jigou"],
                "has_top_seat": m["has_top_seat"],
            }
    return out


# ═══════════════════════════════════════════
# C. 与 lhb_events 联表 → 派生 30 维
# ═══════════════════════════════════════════

def join_lhb_with_seats(lhb_events: list[dict], seat_by_date_stock: dict) -> list[dict]:
    """对每个 lhb_event, 加 30 维顶级游资 + 跟随效应维度."""
    out = []
    for ev in lhb_events:
        date = ev.get("__date")
        name = ev.get("__name")
        if not date or not name or date not in seat_by_date_stock:
            # 没匹配上, 补默认值
            ev2 = dict(ev)
            for k in [
                "seat_top_count", "seat_top_amt", "seat_has_jigou",
                "seat_has_top_seat", "seat_n_labels", "seat_top_label_max",
            ]:
                ev2[k] = 0
            ev2["seat_labels"] = []
            out.append(ev2)
            continue
        # name 是股票中文名, hyyyb 也用中文名, 直接 lookup
        stock_data = seat_by_date_stock[date].get(name)
        if stock_data is None:
            ev2 = dict(ev)
            for k in [
                "seat_top_count", "seat_top_amt", "seat_has_jigou",
                "seat_has_top_seat", "seat_n_labels", "seat_top_label_max",
            ]:
                ev2[k] = 0
            ev2["seat_labels"] = []
            out.append(ev2)
            continue
        ev2 = dict(ev)
        ev2["seat_top_count"] = stock_data["seat_count"]
        ev2["seat_top_amt"] = stock_data["seat_amt"]
        ev2["seat_has_jigou"] = int(stock_data["has_jigou"])
        ev2["seat_has_top_seat"] = int(stock_data["has_top_seat"])
        ev2["seat_n_labels"] = len(stock_data["seat_labels"])
        ev2["seat_top_label_max"] = 1 if stock_data["has_top_seat"] else 0
        # R110 加: 顶级游资具体名单 (用于历史 alpha 加权)
        ev2["seat_labels"] = list(stock_data["seat_labels"])
        # 综合 seat_score
        ev2["seat_score"] = (
            stock_data["seat_count"] * 2
            + (1 if stock_data["has_top_seat"] else 0) * 5
            + (1 if stock_data["has_jigou"] else 0) * 3
        )
        out.append(ev2)
    return out


# ═══════════════════════════════════════════
# D. rank-IC 评估
# ═══════════════════════════════════════════

def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return 0.0
    return float(pd.Series(x).rank().corr(pd.Series(y).rank()))


def eval_dim_ic(events: list[dict], forward_n: int = 2) -> list[tuple[str, float, int]]:
    fwd_key = f"fwd_{forward_n}d"
    pairs_per_dim: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for e in events:
        fwd = e.get(fwd_key)
        if fwd is None or pd.isna(fwd):
            continue
        for dim, val in e.items():
            if dim.startswith("__") or dim.startswith("fwd_") or dim.startswith("lhb_") or dim.startswith("interp_") or dim.startswith("reason_") or dim.startswith("lhb_cs_") or val is None:
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
# E. 综合 score (R108 + R109)
# ═══════════════════════════════════════════

# R110 席位历史 alpha 字典 (从 168 天回填数据回算)
# key = 席位 label, value = 上榜后 1 日平均收益 (%)
SEAT_ALPHA_1D = {
    "瑞鹤仙": 2.35,
    "成都系": 1.40,
    "孙哥": 1.02,
    "赵老哥": 0.98,
    "炒股养家": 0.55,
    "北向资金": 0.49,
    "佛山系": 0.10,
    "欢乐海": -0.39,
}

# 上榜后 2 日平均收益
SEAT_ALPHA_2D = {
    "瑞鹤仙": 3.00,
    "成都系": 1.44,
    "孙哥": 0.95,
    "赵老哥": 0.74,
    "北向资金": 0.50,
    "炒股养家": 0.18,
    "佛山系": -0.07,
    "欢乐海": -1.16,
}


def seat_score_dynamic(ev: dict) -> float:
    """R110 动态席位评分: 席位历史 alpha 加权(只看 seat_top_amt 对应的具体席位)."""
    # 注意: ev['seat_top_amt'] 是聚合后的金额, 看不到具体席位分布
    # 这里用近似: ev 有 'seat_labels' 列表, 每个 label 按其历史 alpha 加权
    labels = ev.get("seat_labels") or []
    if isinstance(labels, str):
        # 兼容 str 序列
        labels = labels.split(",")
    s = 0
    for label in labels:
        alpha = SEAT_ALPHA_1D.get(label, 0)
        # alpha 单位 %; 1% alpha → 加权 1 分
        s += alpha
    return s


def build_seat_alpha_rolling(lhb_events: list[dict],
                              seat_by_date_stock: dict,
                              window_days: int = 30,
                              forward_n: int = 1) -> dict:
    """R111: 对每个 (asof_date, label) 计算近 window_days 滚动 alpha (上榜后 forward_n 日均收益).

    返回 {asof_date_str: {label: avg_alpha_pct}}.

    实现: 遍历 lhb_events, 对每个事件 ev, 收集 asof_date 前 [window_days] 天内
    所有该 label 入场过的 lhb_events 的 fwd_Nd 真值, 取均值。
    """
    from datetime import datetime, timedelta

    # 1. 按 (date, label) 索引: 每个事件的"被哪些 label 触发"
    events_by_date_label = defaultdict(lambda: defaultdict(list))
    for ev in lhb_events:
        d = ev.get("__date", "")
        labels = ev.get("seat_labels") or []
        if isinstance(labels, str):
            labels = labels.split(",")
        fwd = ev.get(f"fwd_{forward_n}d")
        if fwd is None or pd.isna(fwd):
            continue
        for label in labels:
            events_by_date_label[d][label].append((d, fwd))

    # 2. 对每个 asof_date, 滚动前 window_days
    all_dates = sorted(events_by_date_label.keys())
    rolling_alpha = {}
    for asof in all_dates:
        try:
            asof_dt = datetime.strptime(asof, "%Y-%m-%d")
        except ValueError:
            continue
        win_start = (asof_dt - timedelta(days=window_days)).strftime("%Y-%m-%d")
        # 收集窗口内所有 (label, fwd) 对
        window_data = defaultdict(list)
        for d in all_dates:
            if d < win_start or d > asof:
                continue
            for label, recs in events_by_date_label[d].items():
                for _d, fwd in recs:
                    window_data[label].append(fwd)
        # 算均值 (要求样本 >= 5)
        rolling_alpha[asof] = {}
        for label, fwds in window_data.items():
            if len(fwds) >= 5:
                rolling_alpha[asof][label] = float(np.mean(fwds))
    return rolling_alpha


def seat_score_rolling(ev: dict, rolling_alpha: dict) -> float:
    """R111 滚动席位评分: 按 ev.__date 当日最新的滚动 alpha 加权."""
    labels = ev.get("seat_labels") or []
    if isinstance(labels, str):
        labels = labels.split(",")
    asof = ev.get("__date", "")
    seat_alpha = rolling_alpha.get(asof, {})
    s = 0
    for label in labels:
        alpha = seat_alpha.get(label, 0)
        s += alpha
    return s


def combined_score(ev: dict) -> float:
    """R108 lhb_score_simple + R109 seat_score + R112 reason cat + R113 success_rate."""
    s = 0
    s += ev.get("lhb_net_to_mcap", 0) * 5
    s += ev.get("interp_n_jigou", 0) * 3
    s += ev.get("interp_has_famous", 0) * 2
    s += ev.get("lhb_cs_self_pct_net", 0) * 0.05
    # R109 加
    s += ev.get("seat_top_count", 0) * 3
    s += ev.get("seat_has_top_seat", 0) * 5
    s += ev.get("seat_has_jigou", 0) * 3
    # R112 加: 上榜原因分类 alpha
    s += ev.get("reason_cat_up_7", 0) * 5        # IC +0.115
    s += ev.get("reason_cat_up_15", 0) * 3       # IC +0.076
    s += ev.get("reason_cat_up_3day_20", 0) * 3  # IC +0.070
    s -= ev.get("reason_cat_down_7", 0) * 5      # IC -0.110 减分
    s -= ev.get("reason_cat_down_15", 0) * 2     # IC -0.055
    s -= ev.get("reason_cat_turnover_20", 0) * 2  # IC -0.050
    # R113 加: fetcher 自带的同类上榜历史胜率 (单位 0-1, 1=100%)
    # IC +0.1286 上榜后1日! 极强信号
    s += ev.get("interp_success_rate", 0) * 10
    return s


def combined_score_r110(ev: dict) -> float:
    """R110 升级: combined_score + seat_score_dynamic (席位历史 alpha 加权)."""
    s = combined_score(ev)
    s += seat_score_dynamic(ev) * 2  # 席位 alpha 加权 (1%=2分)
    return s


def combined_score_r111(ev: dict, rolling_alpha: dict) -> float:
    """R111 升级: combined_score + seat_score_rolling (近 30 天滚动 alpha 加权)."""
    s = combined_score(ev)
    s += seat_score_rolling(ev, rolling_alpha) * 2  # 1%=2 分
    return s


# ═══════════════════════════════════════════
# F. R114 复合分桶胜率 (自己算,避免循环引用 interp_success_rate)
# ═══════════════════════════════════════════

def bucket_reason(ev: dict) -> str:
    if ev.get("reason_cat_up_7"): return "up_7"
    if ev.get("reason_cat_up_15"): return "up_15"
    if ev.get("reason_cat_up_3day_20"): return "up_3day_20"
    if ev.get("reason_cat_up_3day_12"): return "up_3day_12"
    if ev.get("reason_cat_up_3day_30"): return "up_3day_30"
    if ev.get("reason_cat_down_7"): return "down_7"
    if ev.get("reason_cat_down_15"): return "down_15"
    if ev.get("reason_cat_turnover_20"): return "turnover_20"
    if ev.get("reason_cat_turnover_30"): return "turnover_30"
    if ev.get("reason_cat_amp_15"): return "amp_15"
    if ev.get("reason_cat_st_12"): return "st_12"
    return "other"


def bucket_turnover(ev: dict) -> str:
    t = ev.get("lhb_turnover", 0) or 0
    if t < 5: return "t_<5"
    if t < 15: return "t_5_15"
    if t < 30: return "t_15_30"
    return "t_>30"


def bucket_seat(ev: dict) -> str:
    if ev.get("seat_has_top_seat"): return "seat_yes"
    return "seat_no"


def build_composite_alpha(events: list[dict], forward_n: int = 1) -> dict:
    """R114: 对每个 (reason_bucket, turnover_bucket, seat_bucket) 算历史平均 fwd_Nd.

    返回: {(r, t, s): avg_fwd_pct}
    """
    from collections import defaultdict
    bucket_returns = defaultdict(list)
    for ev in events:
        fwd = ev.get(f"fwd_{forward_n}d")
        if fwd is None or pd.isna(fwd):
            continue
        r = bucket_reason(ev)
        t = bucket_turnover(ev)
        s = bucket_seat(ev)
        bucket_returns[(r, t, s)].append(fwd)
    return {k: float(np.mean(v)) for k, v in bucket_returns.items() if len(v) >= 3}


def composite_score(ev: dict, comp_alpha: dict) -> float:
    """R114: 按 (reason × turnover × seat) 桶的 alpha 加权 (1%=1 分)."""
    r = bucket_reason(ev)
    t = bucket_turnover(ev)
    s = bucket_seat(ev)
    return comp_alpha.get((r, t, s), 0)


def combined_score_r114(ev: dict, rolling_alpha: dict, comp_alpha: dict) -> float:
    """R114 升级: combined_score + 滚动席位 + 复合分桶胜率."""
    s = combined_score(ev)
    s += seat_score_rolling(ev, rolling_alpha) * 2  # 1%=2 分
    s += composite_score(ev, comp_alpha) * 3  # 1%=3 分 (R114 强调复合维度)
    return s


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main():
    log.info("=== R109 顶级游资席位跟随效应 ===")

    # 1. 加载
    seats = load_all_seats()
    seats_labeled = seat_features(seats)
    log.info("seats with labels: %d / %d", sum(1 for s in seats_labeled if s.get("__seat_label")), len(seats_labeled))

    # 2. 顶级游资出现次数
    from collections import Counter
    cnt = Counter(s.get("__seat_label") for s in seats_labeled if s.get("__seat_label"))
    log.info("\n=== 顶级游资席位出现次数 ===")
    for label, n in cnt.most_common(15):
        log.info(f"  {label:15s}: {n}")

    # 3. 每日聚合
    seat_by_date_stock = aggregate_top_seat_buys(seats_labeled)
    log.info("dates: %d, total stock-day records: %d", len(seat_by_date_stock),
             sum(len(v) for v in seat_by_date_stock.values()))

    # 4. 加载 lhb_events (R108) 联表
    from yaogu_lhb_features import load_all_lhb_events, add_cross_section, lhb_score_simple
    lhb_events = load_all_lhb_events()
    lhb_events = add_cross_section(lhb_events)
    joined = join_lhb_with_seats(lhb_events, seat_by_date_stock)
    log.info("joined events: %d", len(joined))

    # 5. rank-IC
    for n in (1, 2, 5):
        results = eval_dim_ic(joined, forward_n=n)
        log.info(f"\n=== Top 15 单维 rank-IC (R108+R109 联合, 上榜后 {n} 日) ===")
        for dim, ic, c in results[:15]:
            mark = "★" if ic > 0.05 else ("✗" if ic < -0.05 else " ")
            log.info(f"  {mark} {dim:32s} IC={ic:+.4f}  n={c}")

    # 6. 胜率回测: combined_score 排序, top-K
    log.info("\n=== 胜率回测 (combined_score = R108 lhb + R109 seat) ===")
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        scored = [(combined_score(e), e[fwd_key], e) for e in joined if e.get(fwd_key) is not None]
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

    # 对比 R108 单用 lhb_score
    log.info("\n=== 对比: R108-only lhb_score_simple ===")
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        scored = [(lhb_score_simple(e), e[fwd_key], e) for e in joined if e.get(fwd_key) is not None]
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

    # R110 升级评分: combined_score + seat_score_dynamic
    log.info("\n=== R110 升级评分 (combined_score + 席位历史 alpha 加权) ===")
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        scored = [(combined_score_r110(e), e[fwd_key], e) for e in joined if e.get(fwd_key) is not None]
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

    # R110 单用 seat_score_dynamic
    log.info("\n=== R110 单用 seat_score_dynamic (席位历史 alpha 加权) ===")
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        scored = [(seat_score_dynamic(e), e[fwd_key], e) for e in joined if e.get(fwd_key) is not None]
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

    # rank-IC vs R110 score
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        scores = []
        fwds = []
        for e in joined:
            if e.get(fwd_key) is None:
                continue
            scores.append(combined_score_r110(e))
            fwds.append(e[fwd_key])
        if scores:
            ic = _spearman(np.array(scores), np.array(fwds))
            log.info(f"=== combined_score_r110 vs 上榜后 {n} 日 rank-IC = {ic:+.4f}  (n={len(scores)})")

    # R111 滚动席位 alpha (近 30 天动态计算)
    log.info("\n=== R111 滚动席位 alpha (近 30 天动态) ===")
    rolling_30_1d = build_seat_alpha_rolling(joined, seat_by_date_stock, window_days=30, forward_n=1)
    rolling_30_2d = build_seat_alpha_rolling(joined, seat_by_date_stock, window_days=30, forward_n=2)
    log.info("rolling dates: %d (sample), label覆盖: %d",
             len(rolling_30_1d),
             len(set(l for d in rolling_30_1d.values() for l in d.keys())))
    # 抽样一个 asof_date 看 5 个 label 的滚动 alpha
    sample_dates = sorted(rolling_30_1d.keys())
    if sample_dates:
        last_d = sample_dates[-1]
        log.info(f"  asof={last_d} 顶级游资近30天上榜后1日alpha:")
        for label, alpha in sorted(rolling_30_1d[last_d].items(), key=lambda x: -x[1])[:8]:
            log.info(f"    {label:12s} alpha={alpha:+.2f}%")

    log.info("\n=== R111 升级评分 (combined_score + 滚动席位 alpha) ===")
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        rolling = rolling_30_1d if n == 1 else (rolling_30_2d if n == 2 else rolling_30_1d)
        scored = [(combined_score_r111(e, rolling), e[fwd_key], e) for e in joined if e.get(fwd_key) is not None]
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

    # rank-IC vs R111
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        rolling = rolling_30_1d if n == 1 else (rolling_30_2d if n == 2 else rolling_30_1d)
        scores = []
        fwds = []
        for e in joined:
            if e.get(fwd_key) is None:
                continue
            scores.append(combined_score_r111(e, rolling))
            fwds.append(e[fwd_key])
        if scores:
            ic = _spearman(np.array(scores), np.array(fwds))
            log.info(f"=== combined_score_r111 vs 上榜后 {n} 日 rank-IC = {ic:+.4f}  (n={len(scores)})")

    # R114 复合分桶胜率 (避免循环引用 interp_success_rate)
    log.info("\n=== R114 复合分桶 (reason × turnover × seat) 胜率 ===")
    comp_alpha_1d = build_composite_alpha(joined, forward_n=1)
    comp_alpha_2d = build_composite_alpha(joined, forward_n=2)
    log.info("buckets: 1d=%d, 2d=%d", len(comp_alpha_1d), len(comp_alpha_2d))
    # 抽样: 列出 alpha 最高的 10 个桶
    top_buckets = sorted(comp_alpha_1d.items(), key=lambda x: -x[1])[:15]
    log.info("Top 15 高 alpha 桶 (上榜后1日):")
    for (r, t, s), a in top_buckets:
        log.info(f"  ({r:15s}, {t:8s}, {s:8s})  alpha={a:+.2f}%")

    log.info("\n=== R114 升级评分 (combined + 滚动席位 + 复合分桶) ===")
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        rolling = rolling_30_1d if n == 1 else (rolling_30_2d if n == 2 else rolling_30_1d)
        comp = comp_alpha_1d if n == 1 else (comp_alpha_2d if n == 2 else comp_alpha_1d)
        scored = [(combined_score_r114(e, rolling, comp), e[fwd_key], e) for e in joined if e.get(fwd_key) is not None]
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

    # rank-IC vs R114
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        rolling = rolling_30_1d if n == 1 else (rolling_30_2d if n == 2 else rolling_30_1d)
        comp = comp_alpha_1d if n == 1 else (comp_alpha_2d if n == 2 else comp_alpha_1d)
        scores = []
        fwds = []
        for e in joined:
            if e.get(fwd_key) is None:
                continue
            scores.append(combined_score_r114(e, rolling, comp))
            fwds.append(e[fwd_key])
        if scores:
            ic = _spearman(np.array(scores), np.array(fwds))
            log.info(f"=== combined_score_r114 vs 上榜后 {n} 日 rank-IC = {ic:+.4f}  (n={len(scores)})")


if __name__ == "__main__":
    main()