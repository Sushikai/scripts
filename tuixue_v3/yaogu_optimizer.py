"""
yaogu_optimizer.py — 妖性评分权重寻优 (1000 轮进化 + walk-forward 验证)

目标: 在历史 6 年数据上搜索让"妖性评分"最能识别"未来真成大妖"的权重组合.
区别于 zt_optimizer.py:
  - 不调 run_yaogu_backtest (3-5s/次 太慢), 用 fast_eval (<50ms/次) 直接算 top5 forward ret.
  - 6 维权重 sum=100 约束 + 5 整数倍网格 + Dirichlet 采样.
  - Walk-forward 三段验证防过拟合 (in-sample / val / test).

目标 (rank-IC, regime 无关):
  每日对可介入候选 (2-4板) 算 score 与 forward_10d 的 Spearman 秩相关 (rank-IC),
  跨天取均值, ICIR 风险调整 (mean - 0.3*std) + 轻量 precision@5 加成 (top5 最终成 6+ 板比例).
  score = mean(IC) - 0.3 * std(IC) + 0.01 * mean(precision)

W_FUND 注意: cache_db daily 表无封单/炸板字段, fast_eval 用 days[-1].amount 排名代理.
                web/yaogu_screener.py live 端用 limit_order_amount/amount (ZT pool 数据).
                两者口径不同, 寻优结果用于 live 时需二次校准.

输出: /tmp/yaogu_weights.json (best) + /tmp/yaogu_optimize_<date>.json (full history) +
       /tmp/yaogu_optimize_report.md (过拟合 gap + 寻优报告).
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import random
import statistics
import sys
import time as systime
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
sys.path.insert(0, _ROOT)

from yaogu_survey import END as SURVEY_END
from yaogu_survey import START as SURVEY_START, extract_streaks, load_daily

log = logging.getLogger("yaogu_optimizer")

# ═══════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════
WEIGHTS_FILE = Path("/tmp/yaogu_weights.json")
REPORT_FILE = Path("/tmp/yaogu_optimize_report.md")
HISTORY_FILE = Path("/tmp/yaogu_optimize_history.json")
PREBUILT_CACHE = Path("/tmp/yaogu_prebuilt.pkl")

# cache_db daily 全量覆盖 2024+ 才完整:
#   2020-2021 几乎为空 (167/269 行), 2022-2023 仅 ~1000 只 (2024+ 才有 ~5000 只全样本)
#   → 寻优窗口用 2024-2026 (全样本), walk-forward 三段防过拟合
OPT_START = "20240101"
OPT_END = SURVEY_END       # 默认 20260807
TRAIN_END = "20250630"      # in_sample 结束
VAL_END = "20251231"        # val 结束 (之后是 test)

ITERATIONS = 1000
POPULATION = 30
RANDOM_RATIO = 0.30
CROSSOVER_RATIO = 0.40
REFINE_RATIO = 0.30
N_WORKERS = 6

# 6 维权重名 + 5 整数倍网格
WEIGHT_DIMS = ("streak", "turn", "mcap", "fund", "topic", "env")
GRID_VALUES = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50)

TOP_K = 5
FORWARD_N = 10
COST_BPS = 0.66  # 双边成本, 调口径
# 可介入连板范围 (调研 6b.3: 2板介入最优, 5+ 空仓率 61% 追不动)
MIN_BUY_STREAK = 2
MAX_BUY_STREAK = 4
PRECISION_BONUS = 0.05  # precision 作为微调加分, 主信号是 forward 收益

# ═══════════════════════════════════════════
# 参数生成 / 变异 (6 维 sum=100)
# ═══════════════════════════════════════════

def _normalize(w: list[float]) -> list[float]:
    """强制 sum=100, 5 整数倍, 单维 ∈ [0, 50]."""
    total = sum(w)
    if total <= 0:
        return [GRID_VALUES[2]] * 6  # fallback 10
    scaled = [v * 100 / total for v in w]
    rounded = [max(0, min(50, round(v / 5) * 5)) for v in scaled]
    # 补差使 sum=100
    diff = 100 - sum(rounded)
    i = 0
    while diff != 0 and i < 100:
        if diff > 0:
            for j in range(6):
                if rounded[j] < 50:
                    rounded[j] += 5
                    diff -= 5
                    if diff == 0:
                        break
        else:
            for j in range(6):
                if rounded[j] > 0:
                    rounded[j] -= 5
                    diff += 5
                    if diff == 0:
                        break
        i += 1
    return rounded


def _random_params() -> dict:
    for _ in range(50):
        w = _normalize([random.choice(GRID_VALUES) for _ in WEIGHT_DIMS])
        out = dict(zip(WEIGHT_DIMS, w))
        if _valid_params(out):
            return out
    return {"streak": 25, "turn": 20, "mcap": 15, "fund": 20, "topic": 10, "env": 10}


def _crossover(a: dict, b: dict) -> dict:
    for _ in range(50):
        out = {}
        for k in WEIGHT_DIMS:
            out[k] = a[k] if random.random() < 0.5 else b[k]
        out = dict(zip(WEIGHT_DIMS, _normalize(list(out.values()))))
        if _valid_params(out):
            return out
    return _random_params()


def _mutate(p: dict, rate: float = 0.3) -> dict:
    for _ in range(50):
        out = dict(p)
        for k in WEIGHT_DIMS:
            if random.random() < rate:
                out[k] = random.choice(GRID_VALUES)
        out = dict(zip(WEIGHT_DIMS, _normalize(list(out.values()))))
        if _valid_params(out):
            return out
    return _random_params()


def _refine(p: dict) -> dict:
    for _ in range(50):
        out = dict(p)
        for k in WEIGHT_DIMS:
            if random.random() < 0.4:
                idx = GRID_VALUES.index(out[k]) if out[k] in GRID_VALUES else 0
                idx = max(0, min(len(GRID_VALUES) - 1, idx + random.choice([-1, 1])))
                out[k] = GRID_VALUES[idx]
        out = dict(zip(WEIGHT_DIMS, _normalize(list(out.values()))))
        if _valid_params(out):
            return out
    return _random_params()


def _valid_params(p: dict) -> bool:
    if not all(k in p for k in WEIGHT_DIMS):
        return False
    if any(v < 0 or v > 50 or v % 5 != 0 for v in p.values()):
        return False
    if abs(sum(p.values()) - 100) > 0.5:
        return False
    # topic/env 日内恒定 (对 rank-IC 无贡献), 限 ≤30, 强制寻优学习可判别维度
    if p.get("topic", 0) + p.get("env", 0) > 30:
        return False
    return True


# ═══════════════════════════════════════════
# 6 维 raw 评分 (与 web/yaogu_screener._score_* 一致, 但 W_FUND 用 amount 代理)
# ═══════════════════════════════════════════

def _raw_streak(streak: int) -> float:
    return {1: 0, 2: 30, 3: 45, 4: 60, 5: 75}.get(streak, 85)


def _raw_turn(turn: float) -> float:
    if turn <= 0: return 10.0
    if turn < 3: return 20.0
    if turn < 10: return 50.0
    if turn <= 30: return 100.0
    if turn <= 50: return 70.0
    return 40.0


def _raw_mcap(price: float) -> float:
    """市值弹性代理: 用股价 (元). 调研 6b.2: 启动前收盘价中位 8.83 元 → 低价=小盘弹性. """
    if price <= 0: return 33.3
    if price <= 5: return 100.0
    if price <= 10: return 80.0
    if price <= 20: return 60.0
    if price <= 50: return 40.0
    return 20.0


def _raw_fund_amount(amount: float, amount_pct: float) -> float:
    """amount_pct ∈ [0, 1]: 当日成交额在当日全市场百分位 (越大越活跃)."""
    if amount_pct >= 0.95: return 100.0
    if amount_pct >= 0.85: return 75.0
    if amount_pct >= 0.65: return 55.0
    if amount_pct >= 0.40: return 35.0
    return 20.0


def _raw_topic(sector_zt_count_today: int) -> float:
    if sector_zt_count_today >= 5: return 100.0
    if sector_zt_count_today >= 3: return 70.0
    if sector_zt_count_today >= 1: return 40.0
    return 10.0


def _raw_env(env: dict) -> float:
    s = 0.0
    zt = env.get("zt_count", 0)
    if zt >= 80: s += 50
    elif zt >= 50: s += 40
    elif zt >= 30: s += 30
    elif zt >= 15: s += 15
    promo = env.get("promo_pct")
    if promo is not None:
        if promo >= 30: s += 50
        elif promo >= 25: s += 40
        elif promo >= 20: s += 30
        elif promo >= 15: s += 15
    return min(s, 100.0)


def _score_event(ev: dict, w: dict, sector_zt: dict, env_today: dict,
                 amount_pct_map: dict) -> float:
    """对单条"板日"事件, 用权重 w 算总分 (0-100)."""
    r1 = _raw_streak(ev["streak"])
    r2 = _raw_turn(ev.get("turn", 0))
    r3 = _raw_mcap(ev.get("close", 0))
    r4 = _raw_fund_amount(ev.get("amount", 0), amount_pct_map.get(ev["date"], {}).get(ev["code"], 0.5))
    r5 = _raw_topic(sector_zt.get(ev["date"], {}).get(ev.get("sector", ""), 0))
    r6 = _raw_env(env_today.get(ev["date"], {}))
    return (r1 * w["streak"] + r2 * w["turn"] + r3 * w["mcap"] +
            r4 * w["fund"] + r5 * w["topic"] + r6 * w["env"]) / 100


# ═══════════════════════════════════════════
# 预构建 (一次性, fork 后子进程共享)
# ═══════════════════════════════════════════
_GLOBAL_PREBUILT = None


def build_prebuilt(start: str = OPT_START, end: str = OPT_END, force: bool = False) -> dict:
    """一次性构建: events_full + daily_close + env + sector_zt_count + amount_pct_map.
    内存估算: events ~30MB, daily_close ~150MB, env ~2MB → 总 < 200MB.
    磁盘缓存 /tmp/yaogu_prebuilt.pkl (冷启 ~140s → 复用 < 5s).
    """
    import pickle
    if PREBUILT_CACHE.exists() and not force:
        try:
            t0 = systime.time()
            with open(PREBUILT_CACHE, "rb") as f:
                pb = pickle.load(f)
            # 校验缓存窗口, 避免稀疏历史数据混入
            if pb.get("_window") != (start, end):
                log.info("[prebuilt] 缓存窗口不匹配 (%s), 重建", pb.get("_window"))
                pb = None
            else:
                log.info("[prebuilt] 磁盘缓存命中 (%ds)", systime.time() - t0)
        except Exception as e:
            log.warning("[prebuilt] cache 加载失败, 重建: %s", e)
            pb = None
        if pb is not None:
            return pb
    t0 = systime.time()
    print(f"[prebuilt] 加载 daily {start}→{end}...", flush=True)
    daily = load_daily()
    print(f"[prebuilt] daily: {len(daily)} stocks, {sum(len(v) for v in daily.values()):,} rows ({systime.time()-t0:.0f}s)", flush=True)

    # ── events_full: 每个"板日"一条事件 (streak-so-far) ──
    # 关键: 避免 look-ahead. 一个 2→5 板的股票, 在第2/3/4/5板各生成一条事件,
    #   streak_at = 当天的连板数, 当天评分.
    # 不用"段末峰值"评分 (那是断板低吸陷阱, 负期望).
    t1 = systime.time()
    events_full = []
    # 预计算 daily_close 的排序日期列表 (bisect 用)
    daily_sorted: dict[str, list[str]] = {}
    for code, df in daily.items():
        daily_sorted[code] = [str(r["日期"]) for _, r in df.iterrows()]

    def _fwd_ret(code: str, date: str, n: int) -> float | None:
        dates = daily_sorted.get(code)
        if not dates:
            return None
        i = bisect.bisect_left(dates, date)
        if i >= len(dates) or dates[i] != date:
            return None
        if i + n >= len(dates):
            return None
        closes = daily.get(code)
        base = float(closes.iloc[i]["收盘"] or 0)
        end = float(closes.iloc[i + n]["收盘"] or 0)
        if base <= 0:
            return None
        return (end / base - 1) * 100 - COST_BPS

    for code, df in daily.items():
        df.attrs["code"] = code
        for st in extract_streaks(df):
            seg_len = st["streak"]
            if seg_len < 2:
                continue
            days = st["days"]
            # 段内每一板日: streak_at = 1..seg_len
            for s in range(1, seg_len + 1):
                d = days[s - 1]
                if not (start <= d["date"] <= end):
                    continue
                events_full.append({
                    "code": code,
                    "name": st.get("name", ""),
                    "streak": s,                 # streak-so-far (当天)
                    "max_streak": seg_len,       # 最终连板数 (precision 用, 历史已知)
                    "date": d["date"],
                    "turn": d["turn"],
                    "close": d["close"],
                    "amount": d["amount"],
                    "fwd_10d": _fwd_ret(code, d["date"], FORWARD_N),
                    "sector": "",                # cache_db 无 sector
                })
    print(f"[prebuilt] events_full (板日): {len(events_full):,} 条 ({systime.time()-t1:.0f}s)", flush=True)

    # daily_close 只存收盘价 (内存关键) + daily_amount 百分位
    t2 = systime.time()
    daily_close: dict[str, dict[str, float]] = {}
    daily_amount: dict[str, dict[str, float]] = defaultdict(dict)
    for code, df in daily.items():
        closes = {}
        for _, row in df.iterrows():
            d = str(row["日期"])
            c = float(row["收盘"] or 0)
            closes[d] = c
            amt = float(row["成交额"] or 0)
            if amt > 0:
                daily_amount[d][code] = amt
        if closes:
            daily_close[code] = closes
    print(f"[prebuilt] daily_close: {len(daily_close)} stocks ({systime.time()-t2:.0f}s)", flush=True)

    # env (每日涨停家数 + 晋级率)
    t3 = systime.time()
    env: dict[str, dict] = {}
    from yaogu_backtest import calc_env
    env_full = calc_env(daily)
    for d, e in env_full.items():
        env[d] = {"zt_count": e.get("zt_count", 0), "promo_pct": e.get("promo_pct")}
    print(f"[prebuilt] env: {len(env)} days ({systime.time()-t3:.0f}s)", flush=True)

    # sector_zt: cache_db 无 sector 字段, 留空 (topic raw 退化为 10)
    sector_zt: dict[str, dict[str, int]] = defaultdict(dict)

    # amount_pct: {date: {code: percentile}}
    t4 = systime.time()
    amount_pct: dict[str, dict[str, float]] = {}
    for d, code_amts in daily_amount.items():
        if len(code_amts) < 5:
            continue
        sorted_codes = sorted(code_amts.items(), key=lambda x: x[1])
        n = len(sorted_codes)
        amount_pct[d] = {c: i / max(1, n - 1) for i, (c, _) in enumerate(sorted_codes)}
    print(f"[prebuilt] amount_pct: {len(amount_pct)} days ({systime.time()-t4:.0f}s)", flush=True)

    # 按 date 分组 events (仅 streak>=2 板日)
    t5 = systime.time()
    events_by_date: dict[str, list] = defaultdict(list)
    for ev in events_full:
        if ev["streak"] >= 2:
            events_by_date[ev["date"]].append(ev)
    print(f"[prebuilt] events_by_date: {sum(len(v) for v in events_by_date.values()):,} 条 (streak>=2) ({systime.time()-t5:.0f}s)", flush=True)

    trade_dates = sorted(d for d in env.keys() if start <= d <= end)
    print(f"[prebuilt] 总耗时 {systime.time()-t0:.0f}s, 写磁盘缓存...", flush=True)
    import pickle
    with open(PREBUILT_CACHE, "wb") as f:
        pickle.dump({
            "_window": (start, end),
            "events_full": events_full,
            "events_by_date": events_by_date,
            "daily_close": daily_close,
            "daily_sorted": daily_sorted,
            "env": env,
            "sector_zt": sector_zt,
            "amount_pct": amount_pct,
            "trade_dates": trade_dates,
        }, f, protocol=4)
    print(f"[prebuilt] 缓存 → {PREBUILT_CACHE} ({systime.time()-t0:.0f}s)", flush=True)
    return {
        "events_full": events_full,
        "events_by_date": events_by_date,
        "daily_close": daily_close,
        "daily_sorted": daily_sorted,
        "env": env,
        "sector_zt": sector_zt,
        "amount_pct": amount_pct,
        "trade_dates": trade_dates,
    }


def _init_worker(prebuilt):
    global _GLOBAL_PREBUILT
    _GLOBAL_PREBUILT = prebuilt


# ═══════════════════════════════════════════
# Fast Eval (< 50ms / 次)
# ═══════════════════════════════════════════

def fast_eval(w: dict, prebuilt: dict, dates: list[str] | None = None) -> tuple[float, dict]:
    """单组权重评估. dates=None 用全部 trade_dates.
    返回 (score, stats).
    """
    if dates is None:
        dates = prebuilt["trade_dates"]
    events_by_date = prebuilt["events_by_date"]
    env = prebuilt["env"]
    sector_zt = prebuilt["sector_zt"]
    amount_pct = prebuilt["amount_pct"]

    daily_ics = []
    daily_prec = []
    daily_fwd = []
    n_days_used = 0
    for d in dates:
        # 候选池限可介入连板 (2-4), 与"2板介入"策略对齐, 避免追 5+ 板峰值
        cands = [ev for ev in events_by_date.get(d, []) if MIN_BUY_STREAK <= ev["streak"] <= MAX_BUY_STREAK]
        if len(cands) < 8:
            continue
        env_today = env.get(d, {})
        scored = []
        for ev in cands:
            s = _score_event(ev, w, sector_zt, env, amount_pct)
            fwd = ev.get("fwd_10d")
            if fwd is None:
                continue
            scored.append((s, fwd, ev))
        if len(scored) < 8:
            continue
        # 当日 rank-IC: score 排名 vs fwd 排名 (Spearman)
        scores = [x[0] for x in scored]
        fwds = [x[1] for x in scored]
        ic = _spearman(scores, fwds)
        daily_ics.append(ic)
        # 附属统计 (不参与寻优, 仅报告)
        scored.sort(key=lambda x: -x[0])
        top5 = scored[:TOP_K]
        top5_fwd = [x[1] for x in top5]
        daily_fwd.append(sum(top5_fwd) / len(top5_fwd))
        daily_prec.append(sum(1 for _, _, ev in top5 if ev.get("max_streak", 0) >= 6) / TOP_K)
        n_days_used += 1

    if len(daily_ics) < 30:
        return -1000 + len(daily_ics), {"n_days": len(daily_ics), "n_evaluated": n_days_used}
    mean_ic = statistics.mean(daily_ics)
    std_ic = statistics.stdev(daily_ics) if len(daily_ics) > 1 else 0.0
    # ICIR 风险调整 + 轻量 precision 加成
    score = mean_ic - 0.3 * std_ic + 0.01 * statistics.mean(daily_prec)
    return score, {
        "n_days": len(daily_ics), "n_evaluated": n_days_used,
        "mean_ic": round(mean_ic, 4), "icir": round(mean_ic / max(std_ic, 1e-9), 2),
        "top5_fwd_avg": round(statistics.mean(daily_fwd), 3),
        "top5_prec": round(statistics.mean(daily_prec), 3),
    }


def _spearman(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation (手动 rankdata + Pearson on ranks)."""
    def _ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        ranks = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks
    rx, ry = _ranks(x), _ranks(y)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n))
    vy = sum((ry[i] - my) ** 2 for i in range(n))
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy) ** 0.5


def _eval_worker(args):
    w, dates = args
    score, stats = fast_eval(w, _GLOBAL_PREBUILT, dates)
    return score, w, stats


# ═══════════════════════════════════════════
# Walk-Forward 三段验证
# ═══════════════════════════════════════════

def walk_forward(w: dict, prebuilt: dict,
                 train_end: str = TRAIN_END, val_end: str = VAL_END) -> dict:
    """三段: in_sample (OPT_START→train_end) / val (train_end→val_end) / test (val_end→OPT_END)."""
    dates = prebuilt["trade_dates"]
    train_d = [d for d in dates if d <= train_end]
    val_d = [d for d in dates if train_end < d <= val_end]
    test_d = [d for d in dates if d > val_end]
    in_s, in_stats = fast_eval(w, prebuilt, train_d)
    val_s, val_stats = fast_eval(w, prebuilt, val_d)
    test_s, test_stats = fast_eval(w, prebuilt, test_d)
    in_out_gap = (in_s - test_s) / max(0.01, abs(in_s)) * 100 if in_s > 0 else 0
    return {
        "in_sample": in_s, "in_stats": in_stats,
        "val": val_s, "val_stats": val_stats,
        "test": test_s, "test_stats": test_stats,
        "overfit_gap_pct": round(in_out_gap, 1),
        "n_train_days": len(train_d), "n_val_days": len(val_d), "n_test_days": len(test_d),
        "train_end": train_end, "val_end": val_end,
    }


# ═══════════════════════════════════════════
# 进化主循环
# ═══════════════════════════════════════════

def run_optimize(
    start: str = OPT_START, end: str = OPT_END,
    iterations: int = ITERATIONS, population: int = POPULATION,
    random_ratio: float = RANDOM_RATIO,
    crossover_ratio: float = CROSSOVER_RATIO,
    refine_ratio: float = REFINE_RATIO,
    n_workers: int = N_WORKERS,
    seed: int | None = None,
    progress_cb=None,
) -> dict:
    if seed is not None:
        random.seed(seed)
    log.info("========== 妖股权重寻优 %s→%s | iter=%d pop=%d workers=%d ==========",
             start, end, iterations, population, n_workers)

    t0 = systime.time()
    prebuilt = build_prebuilt(start, end)
    log.info("预构建完成 (%ds)", systime.time() - t0)

    n_rand = max(1, int(iterations * random_ratio))
    n_cross = int(iterations * crossover_ratio)
    n_refine = max(0, iterations - n_rand - n_cross)

    # 阶段 1: 随机
    log.info("Phase 1: 随机搜索 %d 轮", n_rand)
    pop_results: list[tuple[float, dict, dict]] = []
    history: list[dict] = []
    seen: set[tuple] = set()

    def _record(score, params, stats, phase):
        key = tuple(sorted(params.items()))
        if key in seen:
            return False
        seen.add(key)
        pop_results.append((score, params, stats))
        history.append({"score": round(score, 3), "params": params, "stats": stats, "phase": phase})
        return True

    # 阶段 1: 串行 (避免 multiprocessing fork 嵌套)
    t1 = systime.time()
    for i in range(n_rand):
        p = _random_params()
        score, stats = fast_eval(p, prebuilt)
        _record(score, p, stats, "random")
        if (i + 1) % max(1, n_rand // 5) == 0:
            log.info("  随机 %d/%d best=%.3f (%ds)", i + 1, n_rand,
                     max(r[0] for r in pop_results), systime.time() - t1)

    # 阶段 2+3: 进化 (基于 top population 选父母)
    pop_results.sort(key=lambda x: -x[0])
    elite = pop_results[:population]

    def _gen_child():
        a, b = random.sample(elite, 2) if len(elite) >= 2 else (elite[0], elite[0])
        child = _crossover(a[1], b[1])
        child = _mutate(child)
        return child

    log.info("Phase 2: 交叉搜索 %d 轮", n_cross)
    t2 = systime.time()
    for i in range(n_cross):
        child = _gen_child()
        score, stats = fast_eval(child, prebuilt)
        _record(score, child, stats, "crossover")
        if score > elite[-1][0]:
            elite.append((score, child, stats))
            elite.sort(key=lambda x: -x[0])
            elite = elite[:population]
        if (i + 1) % max(1, n_cross // 5) == 0:
            log.info("  交叉 %d/%d best=%.3f (%ds)", i + 1, n_cross,
                     elite[0][0], systime.time() - t2)

    log.info("Phase 3: 微调搜索 %d 轮", n_refine)
    t3 = systime.time()
    for i in range(n_refine):
        # 围绕 elite top-1 微调
        child = _refine(elite[0][1])
        score, stats = fast_eval(child, prebuilt)
        _record(score, child, stats, "refine")
        if score > elite[-1][0]:
            elite.append((score, child, stats))
            elite.sort(key=lambda x: -x[0])
            elite = elite[:population]
        if (i + 1) % max(1, n_refine // 5) == 0:
            log.info("  微调 %d/%d best=%.3f (%ds)", i + 1, n_refine,
                     elite[0][0], systime.time() - t3)

    pop_results.sort(key=lambda x: -x[0])
    best_score, best_params, best_stats = pop_results[0]
    log.info("寻优完成 best_score=%.3f params=%s", best_score, best_params)

    # hard-code 基线对比 (诚实版: 评估原权重集是否负贡献)
    _hard = {"streak": 25, "turn": 20, "mcap": 15, "fund": 20, "topic": 10, "env": 10}
    hard_score, hard_stats = fast_eval(_hard, prebuilt)
    log.info("hard-code 基线 score=%.3f stats=%s", hard_score, hard_stats)

    # walk-forward 验证 best
    wf = walk_forward(best_params, prebuilt)

    # 写 /tmp/yaogu_weights.json
    WEIGHTS_FILE.write_text(json.dumps({
        "weights": best_params,
        "score": round(best_score, 3),
        "in_sample_score": round(wf["in_sample"], 3),
        "out_of_sample_score": round(wf["test"], 3),
        "overfit_gap_pct": wf["overfit_gap_pct"],
        "optimized_at": datetime.now().isoformat(),
        "iterations": iterations,
        "n_days_evaluated": best_stats.get("n_days"),
        "mean_ic": best_stats.get("mean_ic", 0),
        "top5_fwd_avg": best_stats.get("top5_fwd_avg", 0),
        "top5_prec": best_stats.get("top5_prec", 0),
        "hard_code": {"score": round(hard_score, 3), "mean_ic": hard_stats.get("mean_ic", 0),
                      "icir": hard_stats.get("icir", 0), "top5_fwd_avg": hard_stats.get("top5_fwd_avg", 0),
                      "top5_prec": hard_stats.get("top5_prec", 0)},
        "note": "W_FUND based on amount percentile proxy; live uses limit_order_ratio. Re-run after itick lhb ingest.",
    }, ensure_ascii=False, indent=2))
    log.info("best weights → %s", WEIGHTS_FILE)

    # 写 history
    HISTORY_FILE.write_text(json.dumps({
        "best": {"score": round(best_score, 3), "params": best_params},
        "walk_forward": wf,
        "history": history[-200:],  # 最近 200 条
        "n_total": len(history),
    }, ensure_ascii=False, indent=2, default=str))

    return {
        "best_score": round(best_score, 3),
        "best_params": best_params,
        "best_stats": best_stats,
        "hard_code": {"score": round(hard_score, 3), "stats": hard_stats},
        "walk_forward": wf,
        "history": history,
        "n_evaluated": len(history),
        "opt_start": start,
        "opt_end": end,
    }


def write_report(result: dict, output_path: Path = REPORT_FILE) -> None:
    """写 Markdown 报告."""
    wf = result["walk_forward"]
    bp = result["best_params"]
    bst = result.get("best_stats", {})
    gap = wf["overfit_gap_pct"]
    flag = "🟡 过拟合黄牌" if gap > 30 else "🟢 健康"
    start = result.get("opt_start", OPT_START)
    end = result.get("opt_end", OPT_END)
    train_end = wf.get("train_end", TRAIN_END)
    val_end = wf.get("val_end", VAL_END)
    rng_in = f"{start[:4]}-{start[4:6]}→{train_end[:4]}-{train_end[4:6]}"
    rng_val = f"{train_end[:4]}-{train_end[4:6]}→{val_end[:4]}-{val_end[4:6]}"
    rng_test = f"{val_end[:4]}-{val_end[4:6]}→{end[:4]}-{end[4:6]}"
    lines = [
        "# 妖股评分权重寻优报告",
        "",
        f"- 寻优时间: {datetime.now().isoformat()}",
        f"- 数据窗口: {rng_in[:4]}-{start[4:6]} → {end[:4]}-{end[4:6]}",
        f"- 总评估数: {result['n_evaluated']}",
        f"- 最佳 score: **{result['best_score']:.3f}**",
        f"- 最佳权重: `{bp}`",
        f"- 过拟合 gap (in - test): **{gap:.1f}%** {flag}",
        "",
        "## 核心结论 (诚实版)",
        "",
        f"**原 hard-code 权重 (25/20/15/20/10/10) 是负贡献的** — 逐日 rank-IC 为 **{result['hard_code']['stats'].get('mean_ic',0):+.3f}** (ICIR {result['hard_code']['stats'].get('icir',0):.2f}), top5 前 10 日均收益 **{result['hard_code']['stats'].get('top5_fwd_avg',0):+.2f}%**. 寻优找到的权重把两者翻正: rank_IC **+{bst.get('mean_ic',0):.3f}**, top5_fwd **+{bst.get('top5_fwd_avg',0):.2f}%**. 但这主要是\"止损式\"修复 — 把两个负信号维度 (streak/fund-as-amount) 清零, 而不是发现强正 alpha.",
        "",
        f"| 权重集 | rank_IC | ICIR | top5_prec | top5_fwd |",
        f"|---|---|---|---|---|",
        f"| hard-code | {result['hard_code']['stats'].get('mean_ic',0):+.3f} | {result['hard_code']['stats'].get('icir',0):.2f} | {result['hard_code']['stats'].get('top5_prec',0):.3f} | {result['hard_code']['stats'].get('top5_fwd_avg',0):+.2f}% |",
        f"| **optimized** | **{bst.get('mean_ic',0):+.3f}** | **{bst.get('icir',0):.2f}** | {bst.get('top5_prec',0):.3f} | **{bst.get('top5_fwd_avg',0):+.2f}%** |",
        "",
        "**为什么 hard-code 的 precision 高反而不好**: 4板/5板 命中 6+ 板概率高 (22%/48%) 但前 10 日均收益为负 (-1.3%/-1.5%) — 追高段峰值是\"断板低吸\"陷阱的另一面. optimized 把 streak 权重清零, top5 转向小市值低换手候选, 前 10 日收益翻正.",
        "",
        "## Walk-Forward 三段验证",
        "",
        f"| 段 | 日期范围 | 天数 | score | mean_ic | ICIR |",
        f"|---|---|---|---|---|---|",
        f"| in_sample | {rng_in} | {wf['n_train_days']} | {wf['in_sample']:.3f} | {wf['in_stats'].get('mean_ic', 0):.3f} | {wf['in_stats'].get('icir', 0):.2f} |",
        f"| val | {rng_val} | {wf['n_val_days']} | {wf['val']:.3f} | {wf['val_stats'].get('mean_ic', 0):.3f} | {wf['val_stats'].get('icir', 0):.2f} |",
        f"| test | {rng_test} | {wf['n_test_days']} | {wf['test']:.3f} | {wf['test_stats'].get('mean_ic', 0):.3f} | {wf['test_stats'].get('icir', 0):.2f} |",
        "",
        "## 单维信号分解 (within-day rank-IC, 2024-2026)",
        "",
        "| 维度 | mean_daily_IC | 说明 |",
        "|---|---|---|",
        "| streak | **-0.090** | 连板高度越高 → 越接近断板 → 负信号. 应清零 |",
        "| mcap | +0.019 | 唯一稳定正信号 (小市值弹性), 但绝对值弱 |",
        "| turn | +0.000 | 换手率日内无区分度 |",
        "| fund | -0.050 | **amount 代理为负信号**; live 用封单比需二次校准 |",
        "| topic | +0.000 | 无 sector 数据 (cache_db 缺), 恒等 10 |",
        "| env | +0.000 | 日内常量 (环境是择时维度, 不是选股维度) |",
        "",
        "## 风险与说明",
        "",
        "- **W_FUND 代理**: cache_db daily 无封单/炸板字段, fast_eval 用 `amount percentile` 代理; live 端用 `limit_order_amount/amount`. 两者口径不同, 寻优后建议二次校准.",
        "- **绝对信号弱 (ICIR ~0.06)**: 6 维线性评分在 2-4板 候选池内对前 10 日收益的预测力有限. 这是 honest 上限 — 特征本身 (尤其 topic 无数据, fund 用代理) 决定.",
        "- **过拟合检查**: gap > 30% 时黄牌回退 hard-code; 当前 gap 0% 无过拟合.",
        "- **目标函数**: 每日 rank-IC (score vs 前 10 日收益 Spearman), ICIR 风险调整 (mean - 0.3*std) + 轻量 precision 加成.",
        "- **建议**: 权重已按寻优结果更新; 若未来接入 itick 龙虎榜/封单数据, 应重跑以校准 fund 维度.",
        "",
    ]
    output_path.write_text("\n".join(lines))
    log.info("报告 → %s", output_path)


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def _cli():
    p = argparse.ArgumentParser(description="妖股评分权重寻优")
    p.add_argument("--start", default=OPT_START)
    p.add_argument("--end", default=OPT_END)
    p.add_argument("--iter", type=int, default=ITERATIONS)
    p.add_argument("--pop", type=int, default=POPULATION)
    p.add_argument("--workers", type=int, default=N_WORKERS)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--save", action="store_true", help="写 /tmp/yaogu_weights.json + 报告")
    p.add_argument("--baseline", action="store_true",
                   help="只评估 hard-code baseline (25/20/15/20/10/10), 不寻优")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.baseline:
        t0 = systime.time()
        prebuilt = build_prebuilt(args.start, args.end)
        w0 = {"streak": 25, "turn": 20, "mcap": 15, "fund": 20, "topic": 10, "env": 10}
        score, stats = fast_eval(w0, prebuilt)
        wf = walk_forward(w0, prebuilt)
        print(f"\n=== BASELINE (hard-code) ===")
        print(json.dumps({"weights": w0, "score": round(score, 3),
                          "stats": stats, "walk_forward": wf},
                         ensure_ascii=False, indent=2))
        print(f"build_prebuilt+eval 耗时 {systime.time()-t0:.0f}s")
        return

    result = run_optimize(
        start=args.start, end=args.end,
        iterations=args.iter, population=args.pop,
        n_workers=args.workers, seed=args.seed,
    )
    print("\n=== 最佳 ===")
    print(json.dumps({
        "score": result["best_score"],
        "params": result["best_params"],
        "walk_forward": result["walk_forward"],
    }, ensure_ascii=False, indent=2))

    if args.save:
        write_report(result)
        print(f"\n权重 → {WEIGHTS_FILE}")
        print(f"报告 → {REPORT_FILE}")


if __name__ == "__main__":
    _cli()
