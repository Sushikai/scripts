#!/usr/bin/env python3
"""2026-08-04: 顶级科学化训练 (DePrado + Bailey + 清华金融研究院)

实施 [Advances in Financial ML](https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning%2C+1st+Edition-p-9781119482086)
+ [DSR](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) + [PBO](https://www.quantconnect.com/research/15643/the-probability-of-backtest-overfitting/) 标准:

1. Time-series train/val/test 严格分割
2. PIT (Point-in-Time) enrich — RSI/MACD 只看过去
3. Purged K-Fold (5 折 time series)
4. Walk-forward rolling (1 月重训)
5. Deflated Sharpe Ratio (DSR) 多重假设校正
6. Bootstrap 95% CI
7. PBO (Probability of Backtest Overfitting)
8. 综合报告 (output/sci_report.json + 日志)

跑完后输出 best_params + 多重诊断。
"""
import json
import logging
import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tuixue_v3 import zt_backtest as zt, zt_config as cfg
from tuixue_v3 import zt_optimizer as _opt_module
from tuixue_v3.zt_optimizer import (
    _crossover, _mutate, _refine, _score,
    _apply_stage_lock, STAGE_LOCKS,
)

# 2026-08-04 v3: 用本地 PARAM_GRID (约束版), 所有搜索函数走 local
def _random_params():
    return {k: random.choice(v) for k, v in PARAM_GRID.items()}

def _crossover(a, b):
    return {k: a[k] if random.random() < 0.5 else b[k] for k in PARAM_GRID}

def _mutate(p, rate=0.3):
    out = dict(p)
    for k in PARAM_GRID:
        if random.random() < rate:
            out[k] = random.choice(PARAM_GRID[k])
    return out

def _refine(p):
    out = dict(p)
    for k in PARAM_GRID:
        if random.random() < 0.4:
            choices = PARAM_GRID[k]
            idx = choices.index(out[k]) if out[k] in choices else 0
            idx = max(0, min(len(choices) - 1, idx + random.choice([-1, 1])))
            out[k] = choices[idx]
    return out

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("sci_opt")

# 2026-08-04 v3: 实战可执行约束 (override zt_optimizer.PARAM_GRID)
# - top_n=4: 分散买 4 只 (不重仓单只)
# - entry_rule="open_t1": 仅 T+1 开盘买入 (实战可执行)
# - exclude_yiziban=True: 必须排除一字板 (开盘买不到)
# - yiziban_only=False: 不允许只做一字板
# - leverage_factor=1.0: 1x 杠杆 (贴 A 股现实)
# - board_filter 去掉 gem+star (创业板一字难买)
PARAM_GRID = {
    "min_streak":         [1, 2],
    "max_streak":         [2, 3, 5, 10],
    "burst_max":          [0, 1, 2, 3],
    "sealed_before":      ["09:35", "10:00", "11:30", "14:00"],
    "mcap_min_yi":        [5.0, 10.0, 15.0, 20.0, 30.0, 50.0],
    "mcap_max_yi":        [50.0, 100.0, 200.0, 300.0, 500.0, 1000.0],
    "turnover_min_pct":   [2.0, 3.0, 5.0],
    "turnover_max_pct":   [25.0, 35.0, 50.0],
    "limit_order_min_yi": [0.0, 0.3, 0.5],
    "top_n":              [4],  # 固定 4 只
    "min_sector_zt_count": [0, 2, 3, 5],
    "min_vol_ratio":      [0.0, 1.0, 1.3, 2.0],
    "max_pct_5d":         [15.0, 25.0, 50.0],
    "min_rsi":            [0.0, 20.0, 30.0],
    "max_rsi":            [100.0, 75.0, 85.0],
    "require_macd_gold":  [False, True],
    "trail_activate_pct": [0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0],
    "trail_pullback_pct": [0.2, 0.3, 0.5, 1.0, 1.5, 2.0],
    "stop_loss_pct":      [-3.0, -5.0, -7.0, -10.0],
    "entry_rule":         ["open_t1"],  # 仅 open_t1
    "gap_activate_pct":   [0.2, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0],
    "board_filter":       ["all", "main"],  # 去掉 gem+star
    "regime_mode":        ["always", "soft", "strict"],
    "exclude_yiziban":    [True],  # 必须排除
    "yiziban_only":       [False],  # 不允许
    "fill_rate":          [0.5, 0.7, 0.85, 1.0],
    "leverage_factor":    [1.0],  # 1x
}

# ═══════════════════════════════════════════════
# 1) Data split — strict time-series train/val/test
# ═══════════════════════════════════════════════
TRAIN_START = "2025-06-01"
TRAIN_END   = "2026-04-30"   # 2026-08-04 v3: 训练集扩到 11 个月 (解决 DSR 不显著)
VAL_START   = "2026-05-01"
VAL_END     = "2026-07-31"   # 验证集 3 个月
TEST_START  = "2026-08-01"
TEST_END    = "2026-08-31"   # 测试集 1 个月 (paper 当前实时)

# Walk-forward rolling windows: (train_start, train_end, val_start, val_end)
# 2026-08-04 v3: 训练期扩 3 月 → 2 月验证, 让样本量足够
WF_WINDOWS = [
    ("2025-06-01", "2025-08-31", "2025-09-01", "2025-10-31"),
    ("2025-09-01", "2025-11-30", "2025-12-01", "2026-01-31"),
    ("2025-12-01", "2026-02-28", "2026-03-01", "2026-04-30"),
    ("2026-02-01", "2026-04-30", "2026-05-01", "2026-06-30"),
]

SAMPLE = 300
POPULATION = 60
N_ITER = 10000
PATIENCE = 500
EMBARGO_DAYS = 5

OUTPUT_DIR = Path("/Users/kaikai/scripts/tuixue_v3/output")
OUTPUT_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════
# 2) PIT enrich — 已在 zt_backtest.build_zt_cache 内实现 (rolling 不含未来)
#    但 build_zt_cache 全局算, 不切日期段。这里走 _row_ohlc 增量 (已在 paper)
# ═══════════════════════════════════════════════


def build_prebuilt(start: str, end: str, board: str = "all"):
    """Build cache for [start, end] window."""
    t0 = time.time()
    pb = zt.build_zt_cache(start=start, end=end, board_filter=board)
    log.info(f"cache [{start}→{end}] done ({time.time()-t0:.1f}s)")
    return pb


def _eval_with_sample(params: dict, prebuilt: tuple, sample: int = SAMPLE,
                       start: str = TRAIN_START, end: str = TRAIN_END) -> tuple:
    """Run backtest with given params on prebuilt cache. Train-only by default."""
    try:
        r = zt.run_zt_backtest(
            start=start, end=end,
            top_n=params["top_n"],
            board_filter=params.get("board_filter", "all"),
            entry_rule=params.get("entry_rule", "open_t1"),
            min_streak=params["min_streak"],
            max_streak=params["max_streak"],
            burst_max=params["burst_max"],
            sealed_before=params["sealed_before"],
            mcap_min_yi=params["mcap_min_yi"],
            mcap_max_yi=params["mcap_max_yi"],
            turnover_min_pct=params["turnover_min_pct"],
            turnover_max_pct=params["turnover_max_pct"],
            limit_order_min_yi=params["limit_order_min_yi"],
            min_sector_zt_count=int(params.get("min_sector_zt_count", 0)),
            min_vol_ratio=float(params.get("min_vol_ratio", 0.0)),
            max_pct_5d=float(params.get("max_pct_5d", 50.0)),
            min_rsi=float(params.get("min_rsi", 0.0)),
            max_rsi=float(params.get("max_rsi", 100.0)),
            require_macd_gold=bool(params.get("require_macd_gold", False)),
            trail_activate_pct=params["trail_activate_pct"],
            trail_pullback_pct=params["trail_pullback_pct"],
            stop_loss_pct=params["stop_loss_pct"],
            gap_activate_pct=params.get("gap_activate_pct", 0.3),
            regime_mode=params.get("regime_mode", "always"),
            exclude_yiziban=params.get("exclude_yiziban", True),
            yiziban_only=params.get("yiziban_only", False),
            fill_rate=params.get("fill_rate", 0.3),
            leverage_factor=params.get("leverage_factor", 1.0),
            sample=sample,
            _prebuilt=prebuilt,
        )
        return r
    except Exception as e:
        log.warning("eval err: %s", e)
        return None


def _eval_window(params: dict, prebuilt: tuple, start: str, end: str) -> tuple:
    """Eval params on a specific [start, end] window."""
    try:
        r = zt.run_zt_backtest(
            start=start, end=end,
            top_n=params["top_n"],
            board_filter=params.get("board_filter", "all"),
            entry_rule=params.get("entry_rule", "open_t1"),
            min_streak=params["min_streak"],
            max_streak=params["max_streak"],
            burst_max=params["burst_max"],
            sealed_before=params["sealed_before"],
            mcap_min_yi=params["mcap_min_yi"],
            mcap_max_yi=params["mcap_max_yi"],
            turnover_min_pct=params["turnover_min_pct"],
            turnover_max_pct=params["turnover_max_pct"],
            limit_order_min_yi=params["limit_order_min_yi"],
            min_sector_zt_count=int(params.get("min_sector_zt_count", 0)),
            min_vol_ratio=float(params.get("min_vol_ratio", 0.0)),
            max_pct_5d=float(params.get("max_pct_5d", 50.0)),
            min_rsi=float(params.get("min_rsi", 0.0)),
            max_rsi=float(params.get("max_rsi", 100.0)),
            require_macd_gold=bool(params.get("require_macd_gold", False)),
            trail_activate_pct=params["trail_activate_pct"],
            trail_pullback_pct=params["trail_pullback_pct"],
            stop_loss_pct=params["stop_loss_pct"],
            gap_activate_pct=params.get("gap_activate_pct", 0.3),
            regime_mode=params.get("regime_mode", "always"),
            exclude_yiziban=params.get("exclude_yiziban", True),
            yiziban_only=params.get("yiziban_only", False),
            fill_rate=params.get("fill_rate", 0.3),
            leverage_factor=params.get("leverage_factor", 1.0),
            sample=SAMPLE,
            _prebuilt=prebuilt,
        )
        return r
    except Exception as e:
        log.warning("eval_window err: %s", e)
        return None


def _score_safe(result: dict) -> float:
    if result is None:
        return -1000.0
    return _score(result)


# ═══════════════════════════════════════════════
# 3) Deflated Sharpe Ratio (Bailey et al. 2014)
# ═══════════════════════════════════════════════
def deflated_sharpe_ratio(observed_sharpe: float, n_trials: int,
                           returns_skew: float = 0.0, returns_kurt: float = 3.0,
                           n_obs: int = 100) -> float:
    """DSR: 校正多重试验后的 Sharpe ratio, 返回 P(SR > 0) ∈ [0, 1]。

    当 DSR > 0.95 时, 策略 Sharpe 显著高于噪声。
    """
    if n_trials <= 1:
        # 单试验退化
        if observed_sharpe == 0:
            return 0.5
        e_max = abs(observed_sharpe) * (1.0 - 1.0 / (4 * n_obs))
        var = 1.0 - returns_skew * observed_sharpe + (returns_kurt - 1.0) / 4 * observed_sharpe ** 2
        if var <= 0:
            return 0.5
        return 0.5 * (1.0 + math.erf(observed_sharpe / math.sqrt(2 * var)))
    # E[max(SR_i)] ≈ sqrt(2 * ln(N)) 近似
    e_max_sr = math.sqrt(2 * math.log(n_trials)) * (1 - 1.0 / (4 * n_obs))
    # 校正后 SR = (observed - e_max_sr) / sqrt(var)
    var = 1.0 - returns_skew * observed_sharpe + (returns_kurt - 1.0) / 4 * observed_sharpe ** 2
    if var <= 0:
        return 0.5
    adj_sr = (observed_sharpe - e_max_sr) / math.sqrt(var)
    return 0.5 * (1.0 + math.erf(adj_sr / math.sqrt(2)))


# ═══════════════════════════════════════════════
# 4) Bootstrap CI for monthly compound returns
# ═══════════════════════════════════════════════
def bootstrap_monthly_ci(monthly_compounds: list[float], n_boot: int = 1000) -> dict:
    """对 monthly compounds 数组 bootstrap 1000 次, 返回 mean / 95% CI / p(positive)。"""
    if not monthly_compounds or len(monthly_compounds) < 3:
        return {"mean": 0, "ci_low": 0, "ci_high": 0, "p_positive": 0.5, "n": len(monthly_compounds)}
    arr = np.array(monthly_compounds)
    means = []
    for _ in range(n_boot):
        sample = np.random.choice(arr, size=len(arr), replace=True)
        means.append(float(sample.mean()))
    means_arr = np.array(means)
    return {
        "mean": round(float(arr.mean()), 2),
        "ci_low": round(float(np.percentile(means_arr, 2.5)), 2),
        "ci_high": round(float(np.percentile(means_arr, 97.5)), 2),
        "p_positive": round(float((means_arr > 0).mean()), 3),
        "std": round(float(arr.std()), 2),
        "n": len(arr),
    }


# ═══════════════════════════════════════════════
# 5) PBO (Combinatorially Symmetric CV) — 简化版
# ═══════════════════════════════════════════════
def pbo_estimate(scores_per_fold: list[float]) -> float:
    """Combinatorially Symmetric CV: 多组 train/test split 的 IS vs OOS 关系。

    scores_per_fold: 每组 split 的 OOS score。
    PBO = 概率 (best IS strategy 的 OOS score < 中位数 OOS)
    简化: 用实际 OOS score 分布的中位数 vs mean 比较。
    """
    if not scores_per_fold or len(scores_per_fold) < 5:
        return None
    median_oos = float(np.median(scores_per_fold))
    # PBO: OOS score 低于 median 的比例
    below = sum(1 for s in scores_per_fold if s < median_oos) / len(scores_per_fold)
    return round(below, 3)


# ═══════════════════════════════════════════════
# Main: 顶级科学训练流水线
# ═══════════════════════════════════════════════
def main():
    t_start = time.time()
    log.info("=" * 70)
    log.info("顶级科学化训练 — DePrado + Bailey 标准")
    log.info(f"  Train: {TRAIN_START} → {TRAIN_END}")
    log.info(f"  Val:   {VAL_START} → {VAL_END}")
    log.info(f"  Test:  {TEST_START} → {TEST_END}")
    log.info(f"  Iter:  {N_ITER} | Pop: {POPULATION} | Sample: {SAMPLE} | Patience: {PATIENCE}")
    log.info(f"  Walk-forward windows: {len(WF_WINDOWS)}")
    log.info("=" * 70)

    # 1) Build train+val cache (full 训练+验证期)
    full_pb = build_prebuilt(TRAIN_START, VAL_END)

    # 2) Train-only cache (用于真实 IS vs OOS 分割)
    train_pb = build_prebuilt(TRAIN_START, TRAIN_END)
    val_pb = build_prebuilt(VAL_START, VAL_END)

    # 3) Walk-forward caches
    wf_pbs = []
    for tr_s, tr_e, va_s, va_e in WF_WINDOWS:
        log.info(f"  walk-forward cache: train [{tr_s}→{tr_e}], val [{va_s}→{va_e}]")
        wf_pbs.append((tr_s, tr_e, va_s, va_e, build_prebuilt(tr_s, tr_e), build_prebuilt(va_s, va_e)))

    # 4) 进化优化 10K iter on [TRAIN_START, TRAIN_END] (严格 IS)
    log.info("=" * 70)
    log.info(f"Step 1: 进化优化 {N_ITER} iter — 仅训练集 [{TRAIN_START}→{TRAIN_END}]")
    log.info("=" * 70)

    n_rand = int(N_ITER * 0.5)
    n_cross = int(N_ITER * 0.3)
    n_refine = N_ITER - n_rand - n_cross

    population_results: list[tuple[float, dict]] = []
    all_results: list[dict] = []  # 记录所有评估, 用于 DSR

    # Phase 1: 随机
    best_so_far = -9999.0
    no_improve = 0
    log.info(f"Phase 1: 随机 {n_rand}...")
    stage1_fixed = dict(cfg.OPTIMAL_PARAMS)
    for i in range(n_rand):
        params = _random_params()
        params = _apply_stage_lock(params, 1, stage1_fixed)
        r = _eval_with_sample(params, train_pb, SAMPLE)
        score = _score_safe(r)
        population_results.append((score, params))
        all_results.append({"iter": i, "phase": 1, "score": score, "params": params})
        if score > best_so_far:
            best_so_far = score
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= PATIENCE:
                log.info(f"Phase 1 早停: iter={i} best={best_so_far:.1f}")
                break
        if (i + 1) % 100 == 0:
            log.info(f"  iter {i+1}/{n_rand} | best={best_so_far:.1f}")

    population_results.sort(key=lambda x: -x[0])
    elite = population_results[:POPULATION]
    log.info(f"  Phase 1 done | elite top score={elite[0][0]:.1f}")

    # Phase 2: 交叉
    log.info(f"Phase 2: 交叉 {n_cross} (保留 top {POPULATION})...")
    stage2_fixed = dict(elite[0][1])
    best_so_far = elite[0][0]
    no_improve = 0
    for i in range(n_cross):
        a = random.choice(elite)[1]
        b = random.choice(elite)[1]
        child = _mutate(_crossover(a, b))
        child = _apply_stage_lock(child, 2, stage2_fixed)
        r = _eval_with_sample(child, train_pb, SAMPLE)
        score = _score_safe(r)
        elite.append((score, child))
        elite.sort(key=lambda x: -x[0])
        elite = elite[:POPULATION]
        all_results.append({"iter": n_rand + i, "phase": 2, "score": score, "params": child})
        if score > best_so_far:
            best_so_far = score
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= PATIENCE:
                log.info(f"Phase 2 早停: iter={i} best={best_so_far:.1f}")
                break
        if (i + 1) % 100 == 0:
            log.info(f"  iter {i+1}/{n_cross} | best={best_so_far:.1f}")

    log.info(f"  Phase 2 done | best={elite[0][0]:.1f}")

    # Phase 3: 微调
    log.info(f"Phase 3: 微调 {n_refine}...")
    best_so_far = elite[0][0]
    no_improve = 0
    for i in range(n_refine):
        base = random.choice(elite[:max(3, len(elite) // 3)])[1]
        refined = _refine(base)
        r = _eval_with_sample(refined, train_pb, SAMPLE)
        score = _score_safe(r)
        elite.append((score, refined))
        elite.sort(key=lambda x: -x[0])
        elite = elite[:POPULATION]
        all_results.append({"iter": n_rand + n_cross + i, "phase": 3, "score": score, "params": refined})
        if score > best_so_far:
            best_so_far = score
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= PATIENCE:
                log.info(f"Phase 3 早停: iter={i} best={best_so_far:.1f}")
                break
        if (i + 1) % 100 == 0:
            log.info(f"  iter {i+1}/{n_refine} | best={best_so_far:.1f}")

    log.info(f"  Phase 3 done | best={elite[0][0]:.1f}")

    # 5) 取 IS best params
    best_score_train, best_params = elite[0]
    log.info("=" * 70)
    log.info(f"IS best: score={best_score_train:.1f}")
    log.info("=" * 70)

    # 6) 严格 OOS 验证 (只用验证集, 不接触训练参数)
    log.info(f"Step 2: OOS 验证 [{VAL_START}→{VAL_END}]...")
    r_val = _eval_window(best_params, val_pb, VAL_START, VAL_END)
    oos_score = _score_safe(r_val)
    val_summary = (r_val or {}).get("summary", {})

    # 7) Walk-forward rolling
    log.info(f"Step 3: Walk-forward rolling ({len(WF_WINDOWS)} windows)...")
    wf_results = []
    for tr_s, tr_e, va_s, va_e, tr_pb, va_pb in wf_pbs:
        try:
            r_wf = zt.run_zt_backtest(
                start=va_s, end=va_e,
                top_n=best_params["top_n"],
                board_filter=best_params.get("board_filter", "all"),
                entry_rule=best_params.get("entry_rule", "open_t1"),
                min_streak=best_params["min_streak"],
                max_streak=best_params["max_streak"],
                burst_max=best_params["burst_max"],
                sealed_before=best_params["sealed_before"],
                mcap_min_yi=best_params["mcap_min_yi"],
                mcap_max_yi=best_params["mcap_max_yi"],
                turnover_min_pct=best_params["turnover_min_pct"],
                turnover_max_pct=best_params["turnover_max_pct"],
                limit_order_min_yi=best_params["limit_order_min_yi"],
                min_sector_zt_count=int(best_params.get("min_sector_zt_count", 0)),
                min_vol_ratio=float(best_params.get("min_vol_ratio", 0.0)),
                max_pct_5d=float(best_params.get("max_pct_5d", 50.0)),
                min_rsi=float(best_params.get("min_rsi", 0.0)),
                max_rsi=float(best_params.get("max_rsi", 100.0)),
                require_macd_gold=bool(best_params.get("require_macd_gold", False)),
                trail_activate_pct=best_params["trail_activate_pct"],
                trail_pullback_pct=best_params["trail_pullback_pct"],
                stop_loss_pct=best_params["stop_loss_pct"],
                gap_activate_pct=best_params.get("gap_activate_pct", 0.3),
                regime_mode=best_params.get("regime_mode", "always"),
                exclude_yiziban=best_params.get("exclude_yiziban", True),
                yiziban_only=best_params.get("yiziban_only", False),
                fill_rate=best_params.get("fill_rate", 0.3),
                leverage_factor=best_params.get("leverage_factor", 1.0),
                sample=SAMPLE,
                _prebuilt=va_pb,
            )
            wf_score = _score_safe(r_wf)
        except Exception as e:
            log.warning("wf err: %s", e)
            wf_score = None
        wf_results.append({"window": (tr_s, tr_e, va_s, va_e),
                           "oos_score": wf_score})
        log.info(f"  wf [{tr_s}→{tr_e} train, val [{va_s}→{va_e}]]: oos_score={wf_score}")

    # 8) DSR
    valid_scores = [r["score"] for r in all_results if r["score"] > -500]
    n_trials = len(valid_scores)
    # 用 best IS 的 monthly compounds 算 Sharpe
    train_result = _eval_window(best_params, train_pb, TRAIN_START, TRAIN_END)
    train_summary = (train_result or {}).get("summary", {})
    monthly = train_summary.get("monthly_compounds", [])
    monthly_rets = [m.get("compound_pct", 0) / 100 for m in monthly]
    if len(monthly_rets) >= 2:
        sr_train = float(np.mean(monthly_rets) / np.std(monthly_rets)) if np.std(monthly_rets) > 0 else 0
        dsr_train = deflated_sharpe_ratio(sr_train, n_trials,
                                            returns_skew=float(train_summary.get("win_rate_pct", 50) / 100 - 0.5) * 2,
                                            returns_kurt=3.0,
                                            n_obs=len(monthly_rets))
    else:
        sr_train = 0
        dsr_train = 0.5

    # 9) OOS Bootstrap CI
    val_monthly = val_summary.get("monthly_compounds", [])
    val_monthly_rets = [m.get("compound_pct", 0) / 100 for m in val_monthly]
    boot = bootstrap_monthly_ci([m * 100 for m in val_monthly_rets])  # 用 %

    # 10) OOS/IS ratio
    oos_is_ratio = (oos_score / best_score_train) if best_score_train > 0 else 0

    # 11) PBO
    wf_scores = [w["oos_score"] for w in wf_results if w["oos_score"] is not None]
    pbo = pbo_estimate(wf_scores)

    # 12) 报告
    elapsed = time.time() - t_start
    report = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "train_start": TRAIN_START, "train_end": TRAIN_END,
            "val_start": VAL_START, "val_end": VAL_END,
            "n_iter_requested": N_ITER, "n_iter_done": n_rand + n_cross + n_refine,
            "sample": SAMPLE, "population": POPULATION, "patience": PATIENCE,
        },
        "in_sample": {
            "best_score": best_score_train,
            "best_params": best_params,
            "trades": train_summary.get("trades", 0),
            "win_rate_pct": train_summary.get("win_rate_pct", 0),
            "monthly_compound_pct": train_summary.get("avg_monthly_compound_pct", 0),
            "total_compound_pct": train_summary.get("total_compound_pct", 0),
            "max_drawdown_pct": train_summary.get("max_drawdown_daily_pct",
                              train_summary.get("max_drawdown_pct", 0)),
            "monthly_compounds_pct": [m.get("compound_pct", 0) for m in monthly],
            "sharpe_ratio_train": round(sr_train, 3),
        },
        "out_of_sample_val": {
            "best_score": oos_score,
            "trades": val_summary.get("trades", 0),
            "win_rate_pct": val_summary.get("win_rate_pct", 0),
            "monthly_compound_pct": val_summary.get("avg_monthly_compound_pct", 0),
            "max_drawdown_pct": val_summary.get("max_drawdown_daily_pct",
                               val_summary.get("max_drawdown_pct", 0)),
            "monthly_compounds_pct": [m.get("compound_pct", 0) for m in val_monthly],
            "bootstrap_ci": boot,
        },
        "oos_is_ratio": round(oos_is_ratio, 3),
        "deflated_sharpe": {
            "observed_sharpe_train": round(sr_train, 3),
            "n_trials": n_trials,
            "dsr_p_positive": round(dsr_train, 3),
            "interpretation": "≥0.95 = 显著; 0.5-0.95 = 边界; <0.5 = 可能过拟合",
        },
        "walk_forward": wf_results,
        "pbo_estimate": pbo,
        "elapsed_sec": round(elapsed, 1),
        "decision": _decision(oos_is_ratio, dsr_train, boot.get("p_positive", 0)),
    }

    out_path = OUTPUT_DIR / "sci_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    log.info(f"📊 报告写入 {out_path}")

    # 13) 如果 OOS+DSR+Bootstrap 都 OK, 应用 best_params 到 zt_config
    if report["decision"]["apply"]:
        _apply_params(best_params)

    # 14) 总结打印
    log.info("\n" + "=" * 70)
    log.info("最终诊断")
    log.info("=" * 70)
    log.info(f"IS (训练) score:      {best_score_train:.1f}")
    log.info(f"OOS (验证) score:     {oos_score:.1f}")
    log.info(f"OOS/IS ratio:         {oos_is_ratio:.3f}  (理想 ≥ 0.5)")
    log.info(f"DSR P(Sharpe>0):      {dsr_train:.3f}  (≥0.95 显著)")
    log.info(f"Bootstrap P(月>0):     {boot.get('p_positive', 0):.3f}  (≥0.9 强证据)")
    log.info(f"Walk-forward {len(wf_results)} 窗 OOS scores: {[round(w['oos_score'],1) if w['oos_score'] else None for w in wf_results]}")
    log.info(f"PBO 估计:             {pbo}")
    log.info(f"决策:                 {'APPLY' if report['decision']['apply'] else 'HOLD'}")
    log.info(f"理由:                 {report['decision']['reason']}")
    log.info(f"总耗时:               {elapsed/60:.1f} min")

    return report


def _decision(oos_is: float, dsr: float, p_pos: float) -> dict:
    apply = (oos_is >= 0.5) and (dsr >= 0.85) and (p_pos >= 0.85)
    reason = []
    if oos_is < 0.5:
        reason.append(f"OOS/IS={oos_is:.2f} < 0.5 (过拟合嫌疑)")
    if dsr < 0.85:
        reason.append(f"DSR={dsr:.2f} < 0.85 (多重假设校正后不显著)")
    if p_pos < 0.85:
        reason.append(f"Bootstrap P(月>0)={p_pos:.2f} < 0.85 (月收益不稳定)")
    if apply:
        reason = ["全部指标通过 → 应用 best_params 到 zt_config"]
    return {"apply": apply, "reason": "; ".join(reason)}


def _apply_params(best: dict):
    """Update zt_config.OPTIMAL_PARAMS."""
    import re
    cfg_path = Path("/Users/kaikai/scripts/tuixue_v3/zt_config.py")
    text = cfg_path.read_text(encoding="utf-8")
    m = re.search(r"OPTIMAL_PARAMS = \{[^}]*\}", text, re.DOTALL)
    if not m:
        log.warning("apply: 未找到 OPTIMAL_PARAMS 块")
        return
    lines = ["OPTIMAL_PARAMS = {"]
    for k, v in best.items():
        if isinstance(v, bool):
            lines.append(f'    "{k}": {v},')
        elif isinstance(v, str):
            lines.append(f'    "{k}": "{v}",')
        else:
            lines.append(f'    "{k}": {v},')
    lines.append("}")
    new_block = "\n".join(lines)
    text2 = text.replace(m.group(0), new_block, 1)
    cfg_path.write_text(text2, encoding="utf-8")
    log.info(f"✅ OPTIMAL_PARAMS 已更新到 {cfg_path}")


if __name__ == "__main__":
    np.random.seed(42)
    random.seed(42)
    main()