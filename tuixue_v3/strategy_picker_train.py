"""
strategy_picker_train.py — 策略选股 3 大策略评分权重科学训练 (10000 轮进化)。

目标: web/strategy_picker._score_signal 的硬编码权重 → 数据训练产出。
复用 weekly_bull_train 的样本采集 + 进化算法框架, 但特征维度更复杂
(wb count/pattern 命中 + rl 距离/near + ma5 放量倍数)。
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import pickle
import random
import re as _re
import sys
import time as systime
from typing import Any

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("strategy_picker_train")

from tuixue_v3 import cache_db as cdb
from tuixue_v3 import zt_backtest as zt
from tuixue_v3.web import strategy_picker as sp

WEIGHT_KEYS = sp.SP_WEIGHT_KEYS
GRID = sp.SP_WEIGHT_GRID
FWD_DAYS = (5, 10, 20)


def _df_to_daily_dicts(df) -> list[dict]:
    """与 weekly_bull_train 同构。"""
    out = []
    for _, r in df.iterrows():
        d = str(r.get("日期") or "").strip()
        if len(d) == 8 and d.isdigit():
            d = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        if len(d) != 10:
            continue
        out.append({
            "date": d, "open": float(r.get("开盘", 0) or 0),
            "high": float(r.get("最高", 0) or 0),
            "low": float(r.get("最低", 0) or 0),
            "close": float(r.get("收盘", 0) or 0),
            "volume": float(r.get("成交量", 0) or 0),
            "amount": float(r.get("成交额", 0) or 0),
        })
    return out


def collect_samples(stock_sample: int = 0, max_weeks_per_stock: int = 300,
                    samples_path: str | None = "/tmp/sp_samples.pkl") -> list[dict]:
    """从全市场历史日线构建策略选股训练样本。
    每只股票: 滑动历史周末点, 跑 wb + rl + ma5 → 命中特征 + 未来收益。
    """
    import os
    if samples_path and os.path.exists(samples_path):
        with open(samples_path, "rb") as f:
            s = pickle.load(f)
        log.info("从缓存加载策略选股样本: %d 条", len(s))
        return s

    daily_cache = zt._batch_cache_load(cdb)
    codes = list(daily_cache.keys())
    if stock_sample > 0:
        random.seed(42)
        codes = random.sample(codes, min(stock_sample, len(codes)))

    samples: list[dict] = []
    t0 = systime.time()
    for ci, code in enumerate(codes):
        df = daily_cache[code]
        daily = _df_to_daily_dicts(df)
        if len(daily) < 60:
            continue

        def hist_loader(c, days):
            return daily[:hist_end_idx + 1]

        week_count = 0
        # 滑动窗口: 从第 250 天开始到倒数 25 天 (留 20 日 future 收益空间)
        for hist_end_idx in range(259, len(daily) - 21, 5):  # 步长 5 天加速
            hist = daily[:hist_end_idx + 1]
            if len(hist) < 30:
                continue
            try:
                res = sp.analyze_one(code, kline_loader=lambda c, days: hist)
            except Exception:
                continue
            if res.get("_skip"):
                continue

            # 提取特征: wb 命中 + rl 距离/near + ma5 放量
            wb_matched = (res.get("wb") or {}).get("matched", []) if res.get("wb") else []
            rl = res.get("rl") or {}
            ma5 = res.get("ma5") or {}
            ma5_ok = bool(ma5.get("ok"))
            ma5_ratio = 1.0
            if ma5_ok:
                m = _re.search(r"量\s*([\d.]+)x", ma5.get("reason", ""))
                if m:
                    ma5_ratio = float(m.group(1))

            has_wb = len(wb_matched) > 0
            has_rl = rl.get("near_support", False) or bool(rl.get("level_1_3"))
            has_ma5 = ma5_ok

            if not (has_wb or has_rl or has_ma5):
                continue  # 三策略全空, 无信号

            # 未来收益
            base = daily[hist_end_idx]["close"]
            if base <= 0:
                continue
            fwd = {}
            ok = True
            for n in FWD_DAYS:
                fi = hist_end_idx + n
                if fi < len(daily):
                    fwd[f"fwd{n}"] = round((daily[fi]["close"] / base - 1) * 100, 3)
                else:
                    ok = False
                    break
            if not ok:
                continue

            samples.append({
                "code": code,
                "date": daily[hist_end_idx]["date"],
                "wb_matched": wb_matched,
                "rl_near": has_rl,
                "rl_dist": abs(rl.get("distance_to_level_1_3_pct") or 99) if rl else 99,
                "rl_dist_lt1": rl.get("distance_to_level_1_3_pct") is not None
                               and abs(rl.get("distance_to_level_1_3_pct", 99)) < 1,
                "ma5_ok": has_ma5,
                "ma5_ratio": ma5_ratio,
                "fwd5": fwd.get("fwd5", 0),
                "fwd10": fwd.get("fwd10", 0),
                "fwd20": fwd.get("fwd20", 0),
            })
            week_count += 1
            if week_count >= max_weeks_per_stock:
                break

        if (ci + 1) % 500 == 0:
            log.info("  采样 %d/%d, 累计 %d (%ds)", ci + 1, len(codes), len(samples), systime.time() - t0)

    log.info("策略选股样本: %d 条 (%ds)", len(samples), systime.time() - t0)
    if samples_path:
        with open(samples_path, "wb") as f:
            pickle.dump(samples, f)
        log.info("样本缓存到 %s", samples_path)
    return samples


# ── 评分函数 (复用 score_signal 的逻辑, 但用一组 w 参数) ──

def _signal_score(s: dict, w: dict) -> float:
    """给定权重 → 策略选股综合得分。
    复用 _score_signal 的结构, 但全部走参数化权重。
    """
    score = 0.0
    # wb
    if s["wb_matched"]:
        wb_cap = float(w.get("wb", 40))
        cnt = len(s["wb_matched"])
        score += min(cnt * float(w.get("wb", 40)) / 4, wb_cap)
        pats = set(s["wb_matched"])
        if "sanxing_taodi" in pats:
            score += float(w.get("wb_pat_sanxing", 8))
        if "tupo_pingtai" in pats:
            score += float(w.get("wb_pat_tupo", 6))
        if "zhanwen_5w" in pats:
            score += float(w.get("wb_pat_zhanwen", 4))
        if "zhouxian_duiliang" in pats:
            score += float(w.get("wb_pat_zhouxian", 4))
        if "junxian_fangxiang" in pats:
            score += float(w.get("wb_pat_junxian", 2))
        score = min(score, wb_cap)
    # rl
    if s["rl_near"]:
        score += float(w.get("rl_near", 25))
        if s["rl_dist_lt1"]:
            score += float(w.get("rl_near_1", 5))
    elif s["rl_dist"] < 5:
        score += float(w.get("rl_lt5", 18))
    elif s["rl_dist"] < 10:
        score += float(w.get("rl_lt10", 10))
    else:
        score += float(w.get("rl_far", 5))
    score = min(score, 100)  # 全局 cap
    # ma5
    if s["ma5_ok"]:
        score += min(s["ma5_ratio"] * float(w.get("ma5_vol", 10)) + float(w.get("ma5_base", 5)),
                     float(w.get("ma5", 30)))
    return score


# ── Fitness: 月度 beta 中性 + walk-forward ──

def evaluate_weights(w: dict, samples: list[dict], fwd_key: str = "fwd20") -> float:
    """按月分组, 组内 top 20% 超额 (剥离 beta)。"""
    if not samples:
        return 0.0
    by_month: dict[str, list] = {}
    for s in samples:
        by_month.setdefault(str(s.get("date") or "")[:7], []).append(s)
    month_spreads = []
    month_seps = []
    global_unique = set()
    for m, grp in by_month.items():
        n = len(grp)
        if n < 30:
            continue
        rets = np.array([g.get(fwd_key, 0) for g in grp])
        scores = [_signal_score(g, w) for g in grp]
        global_unique.update(scores)
        if len(set(scores)) <= 1:
            continue
        order = np.argsort(-np.array(scores))
        base_mean = float(rets.mean())
        top_n = max(int(n * 0.2), 5)
        top_ret = float(rets[order[:top_n]].mean())
        bot_ret = float(rets[order[-top_n:]].mean())
        month_spreads.append(top_ret - base_mean)
        month_seps.append(top_ret - bot_ret)
    if not month_spreads:
        return -1.0
    if len(global_unique) <= 1:
        return -1.0
    avg_spread = float(np.mean(month_spreads))
    avg_sep = float(np.mean(month_seps))
    return round(avg_spread + 0.3 * avg_sep, 4)


# ── 进化算法 ──

def _random_w() -> dict:
    return {k: random.choice(GRID[k]) for k in WEIGHT_KEYS}


def _crossover(a: dict, b: dict) -> dict:
    return {k: a[k] if random.random() < 0.5 else b[k] for k in WEIGHT_KEYS}


def _mutate(w: dict, rate: float = 0.3) -> dict:
    return {k: random.choice(GRID[k]) if random.random() < rate else w[k] for k in WEIGHT_KEYS}


def _refine(w: dict) -> dict:
    out = dict(w)
    for k in WEIGHT_KEYS:
        if random.random() < 0.4:
            choices = GRID[k]
            idx = choices.index(out[k]) if out[k] in choices else 0
            idx = max(0, min(len(choices) - 1, idx + random.choice([-1, 1])))
            out[k] = choices[idx]
    return out


_GLOBAL_SAMPLES = None
_GLOBAL_FWD = "fwd20"


def _init_worker(samples, fwd_key):
    global _GLOBAL_SAMPLES, _GLOBAL_FWD
    _GLOBAL_SAMPLES = samples
    _GLOBAL_FWD = fwd_key


def _eval_worker(w: dict) -> tuple[float, dict]:
    return evaluate_weights(w, _GLOBAL_SAMPLES, _GLOBAL_FWD), w


def run_train(iterations: int = 10000, population: int = 80, n_workers: int = 8,
              fwd_key: str = "fwd20", seed: int | None = 42,
              samples_path: str = "/tmp/sp_samples.pkl") -> dict:
    if seed is not None:
        random.seed(seed)
    t0 = systime.time()
    log.info("========== strategy_picker 训练 %d 轮 (pop=%d) ==========", iterations, population)
    samples = collect_samples(samples_path=samples_path)
    if len(samples) < 50:
        return {"error": "样本不足", "samples": len(samples)}
    log.info("样本: %d 条, 开始搜索...", len(samples))

    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_workers, initializer=_init_worker,
                  initargs=(samples, fwd_key)) as pool:
        pop = [_random_w() for _ in range(iterations // 3)]
        results = list(pool.imap(_eval_worker, pop))
        results.sort(key=lambda x: -x[0])
        elite = results[:population]
        log.info("Phase1 随机 %d 次 | best=%.4f", len(pop), elite[0][0])

        for phase in ("phase2", "phase3"):
            n = iterations // 3 if phase == "phase2" else iterations - 2 * (iterations // 3)
            children = []
            for _ in range(n):
                a = random.choice(elite)[1]
                b = random.choice(elite)[1]
                ch = _crossover(a, b)
                ch = _refine(ch) if phase == "phase3" else _mutate(ch)
                children.append(ch)
            res = list(pool.imap(_eval_worker, children))
            for r in res:
                elite.append(r)
            elite.sort(key=lambda x: -x[0])
            elite = elite[:population]
            log.info("%s 进化 %d 次 | best=%.4f", phase, n, elite[0][0])

    best_score, best_w = elite[0]
    log.info("========== 训练完成 | best=%.4f | %.0fs ==========", best_score, systime.time() - t0)
    log.info("最佳权重: %s", {k: best_w[k] for k in WEIGHT_KEYS})

    verify = {}
    for fk in FWD_DAYS:
        verify[f"corr_fwd{fk}"] = round(evaluate_weights(best_w, samples, f"fwd{fk}"), 4)
    # walk-forward
    ss = sorted(samples, key=lambda s: s["date"])
    cut = int(len(ss) * 0.7)
    wf = {
        "train_fit": round(evaluate_weights(best_w, ss[:cut], fwd_key), 4),
        "test_fit": round(evaluate_weights(best_w, ss[cut:], fwd_key), 4),
    }
    verify["walk_forward"] = wf

    return {
        "weights": {k: best_w[k] for k in WEIGHT_KEYS},
        "score": round(best_score, 4),
        "samples": len(samples),
        "verify": verify,
        "iterations": iterations,
        "elapsed_sec": round(systime.time() - t0, 1),
    }


def save_weights_to_store(r: dict) -> bool:
    """写入 cache_store OPTIMIZER_BEST.sp_weights (合并而非覆盖)。"""
    try:
        from tuixue_v3 import cache_store as cs
        store = cs.get_store()
        v = store.get(cs.K.OPTIMIZER_BEST) or {}
        v["sp_weights"] = r["weights"]
        v["sp_meta"] = {
            "score": r.get("score"),
            "samples": r.get("samples"),
            "verify": r.get("verify"),
            "iterations": r.get("iterations"),
            "updated_at": systime.time(),
        }
        store.set(cs.K.OPTIMIZER_BEST, v)
        log.info("已写入 OPTIMIZER_BEST.sp_weights")
        return True
    except Exception as e:
        log.error("写入 cache_store 失败: %s", e)
        return False


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--iter", type=int, default=10000)
    p.add_argument("--pop", type=int, default=80)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--fwd", default="fwd20")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--save", action="store_true")
    args = p.parse_args()
    r = run_train(iterations=args.iter, population=args.pop,
                  n_workers=args.workers, fwd_key=args.fwd, seed=args.seed)
    if args.save and r.get("weights"):
        save_weights_to_store(r)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2))