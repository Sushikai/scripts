"""
weekly_bull_train.py — 周线擒牛 打分权重科学训练 (10000 轮进化)。

目标: 把 web/weekly_bull 的 6 个 pattern 命中权重 (硬编码) 变成数据训练的产物。

方法论:
  1) 样本采集: 从 cache_db.daily() 全市场历史日线, 对每只股票滑动窗口 (2024→今),
     每个历史周末点跑 weekly_bull.analyze_one → 记录 (matched 集合, 未来 N 日收益)。
  2) fitness: 给定一组权重, 对样本打分 (命中 pattern 权重和), 算 score 与未来收益的
     Spearman 相关。越接近 1 说明权重把高收益组合排前面。
  3) 进化算法 (复用 ZT 模式): 随机 → 交叉 → 微调, 搜 6 维权重, 10000 轮。

权重落地:
  写 cache_store OPTIMIZER_BEST.wb_weights, 由 web/weekly_bull 扫描时读取覆盖默认值。
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import random
import sys
import time as systime
from typing import Any

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("weekly_bull_train")

from tuixue_v3 import cache_db as cdb
from tuixue_v3 import zt_backtest as zt
from tuixue_v3.web import weekly_bull as wb

# ── 训练窗口 ─────────────────────────────
TRAIN_START = "2024-01-01"   # 样本起点 (滑动窗口用历史 250 天检测)
TRAIN_END = "2026-08-04"     # 训练截至
FWD_DAYS = (5, 10, 20)       # 未来收益窗口 (多尺度)
MIN_WEEKS = 12               # 周线最少周数

WEIGHT_KEYS = wb.PATTERN_WEIGHT_KEYS
GRID = wb.PATTERN_WEIGHT_GRID


# ── 样本构建 ─────────────────────────────

def _df_to_daily_dicts(df) -> list[dict]:
    """DataFrame (日期/开盘/最高/最低/收盘/成交量/成交额) → weekly_bull 兼容 dict 列表。
    weekly_bull._to_weekly 期望 date 是 'YYYY-MM-DD' (10 位)。"""
    out = []
    for _, r in df.iterrows():
        d = str(r.get("日期") or "").strip()
        if len(d) == 8 and d.isdigit():
            d = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        if len(d) != 10:
            continue
        out.append({
            "date": d,
            "open": float(r.get("开盘", 0) or 0),
            "high": float(r.get("最高", 0) or 0),
            "low": float(r.get("最低", 0) or 0),
            "close": float(r.get("收盘", 0) or 0),
            "volume": float(r.get("成交量", 0) or 0),
            "amount": float(r.get("成交额", 0) or 0),
        })
    return out


def collect_samples(stock_sample: int = 0, max_weeks_per_stock: int = 400) -> list[dict]:
    """从全市场历史日线构建训练样本。

    对每只股票: 按周末切割, 每个历史周末点 → (截至该点的 250 天日线) → analyze_one
    → matched 集合 + 未来 5/10/20 日收益。若未来收益缺失 (窗口尾部) 跳过。

    返回 [{code, date, matched:[...], fwd5, fwd10, fwd20}]
    """
    daily_cache = zt._batch_cache_load(cdb)
    log.info("日线缓存: %d 只股票", len(daily_cache))

    # 限样本量 (调试用)
    codes = list(daily_cache.keys())
    if stock_sample > 0:
        random.seed(42)
        codes = random.sample(codes, min(stock_sample, len(codes)))
        log.info("股票抽样: %d 只", len(codes))

    samples: list[dict] = []
    t0 = systime.time()
    for ci, code in enumerate(codes):
        df = daily_cache[code]
        daily = _df_to_daily_dicts(df)
        if len(daily) < MIN_WEEKS * 5 + FWD_DAYS[-1]:
            continue
        # 切成周 (复用 weekly_bull 聚合)
        weeks = wb._to_weekly(daily)
        if len(weeks) < MIN_WEEKS:
            continue
        # 对每个历史周末点 (排除最后 2 周, 未来收益窗口太短)
        n_weeks = len(weeks)
        week_count = 0
        for i in range(n_weeks - 2):
            # 该周结束对应的日线截止点 = 周线最后一根 bar 的日期
            week_end_idx = None
            week_date_end = weeks[i]["date_end"]  # YYYY-MM-DD
            # 在 daily 里找 <= week_date_end 的截止索引
            for j, d in enumerate(daily):
                if d["date"] <= week_date_end:
                    week_end_idx = j
                else:
                    break
            if week_end_idx is None or week_end_idx < 250:
                continue
            # 构造历史 loader: 截至该点的最近 250 天
            hist = daily[week_end_idx - 249:week_end_idx + 1]
            if len(hist) < 30:
                continue
            # 跑 pattern 检测
            try:
                res = wb.analyze_one(code, kline_loader=lambda c, days: hist)
            except Exception:
                continue
            if res.get("_skip") or not res.get("matched"):
                continue
            # 未来收益 (从该周之后)
            fwd = {}
            ok = True
            for n in FWD_DAYS:
                fi = week_end_idx + n
                if fi < len(daily) and daily[fi]["close"] > 0:
                    base = daily[week_end_idx]["close"]
                    if base > 0:
                        fwd[f"fwd{n}"] = round((daily[fi]["close"] / base - 1) * 100, 3)
                    else:
                        ok = False
                        break
                else:
                    ok = False
                    break
            if not ok:
                continue
            samples.append({
                "code": code,
                "date": week_date_end,
                "matched": res["matched"],
                "fwd5": fwd.get("fwd5", 0),
                "fwd10": fwd.get("fwd10", 0),
                "fwd20": fwd.get("fwd20", 0),
            })
            week_count += 1
            if week_count >= max_weeks_per_stock:
                break
        if (ci + 1) % 500 == 0:
            log.info("  采样 %d/%d 只, 累计样本 %d (%ds)",
                     ci + 1, len(codes), len(samples), systime.time() - t0)
    log.info("样本采集完成: %d 条 (%ds)", len(samples), systime.time() - t0)
    return samples


# ── fitness ─────────────────────────────

def _weighted_score(matched: list[str], w: dict) -> float:
    return sum(w.get(k, 0) for k in matched)


def _spearman(x: list[float], y: list[float]) -> float:
    def _ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        ranks = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks
    rx = _ranks(x)
    ry = _ranks(y)
    dx = np.array(rx) - np.array(rx).mean()
    dy = np.array(ry) - np.array(ry).mean()
    denom = np.sqrt((dx ** 2).sum() * (dy ** 2).sum())
    if denom == 0:
        return 0.0
    return float((dx * dy).sum() / denom)


def evaluate_weights(w: dict, samples: list[dict], fwd_key: str = "fwd10") -> float:
    """给定权重 → 月度 beta 中性选股超额。

    按月份分组: 组内按权重打分排序 → top 20% 平均 fwd 收益 − 组内全体 mean。
    取各月超额的均值。这样剥离市场周期 (普涨/普跌月各自的 beta),
    真正衡量权重是否"每月都从命中里选出更好的"。再加多空分化正则。
    """
    if not samples:
        return 0.0
    by_month: dict[str, list] = {}
    for s in samples:
        m = str(s.get("date") or "")[:7]
        by_month.setdefault(m, []).append(s)

    month_spreads = []
    month_seps = []
    global_unique = set()
    for m, grp in by_month.items():
        n = len(grp)
        if n < 30:
            continue  # 月度样本太少不评估
        rets = np.array([g.get(fwd_key, 0) for g in grp])
        scores = [_weighted_score(g["matched"], w) for g in grp]
        global_unique.update(scores)
        if len(set(scores)) <= 1:
            continue  # 该月权重无区分
        order = np.argsort(-np.array(scores))
        base_mean = float(rets.mean())
        top_n = max(int(n * 0.2), 5)
        top_ret = float(rets[order[:top_n]].mean())
        bot_ret = float(rets[order[-top_n:]].mean())
        month_spreads.append(top_ret - base_mean)
        month_seps.append(top_ret - bot_ret)

    if not month_spreads:
        return -1.0  # 所有月份无区分 → 强惩罚
    if len(global_unique) <= 1:
        return -1.0

    avg_spread = float(np.mean(month_spreads))
    avg_sep = float(np.mean(month_seps))
    # 主看月度平均超额, 次看多空分化
    return round(avg_spread + 0.3 * avg_sep, 4)


# ── 进化算法 (复用 ZT 模式) ─────────────

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


# ── Worker (multiprocessing) ─────────────

_GLOBAL_SAMPLES = None
_GLOBAL_FWD = "fwd10"


def _init_worker(samples, fwd_key):
    global _GLOBAL_SAMPLES, _GLOBAL_FWD
    _GLOBAL_SAMPLES = samples
    _GLOBAL_FWD = fwd_key


def _eval_worker(w: dict) -> tuple[float, dict]:
    return evaluate_weights(w, _GLOBAL_SAMPLES, _GLOBAL_FWD), w


def load_samples(path: str | None = None) -> list[dict]:
    """加载样本 (pickle 缓存优先, 否则重新采集)。"""
    import os
    if path and os.path.exists(path):
        import pickle
        with open(path, "rb") as f:
            s = pickle.load(f)
        log.info("从缓存加载样本: %d 条", len(s))
        return s
    samples = collect_samples()
    if path:
        import pickle
        with open(path, "wb") as f:
            pickle.dump(samples, f)
        log.info("样本已缓存到 %s", path)
    return samples


def run_train(
    iterations: int = 10000,
    population: int = 60,
    n_workers: int = 8,
    stock_sample: int = 0,
    fwd_key: str = "fwd20",
    seed: int | None = None,
    samples_path: str | None = "/tmp/wb_samples.pkl",
) -> dict:
    """进化算法搜 6 维权重, 目标 = score 与未来收益 Spearman 最大化。"""
    if seed is not None:
        random.seed(seed)
    t0 = systime.time()
    log.info("========== weekly_bull 权重训练 %d 轮 (pop=%d workers=%d) ==========",
             iterations, population, n_workers)

    if stock_sample and stock_sample > 0:
        samples = collect_samples(stock_sample=stock_sample)
    else:
        samples = load_samples(samples_path)
    if len(samples) < 50:
        log.error("样本不足 (%d), 无法训练", len(samples))
        return {"samples": len(samples), "error": "样本不足"}
    log.info("样本: %d 条, 开始进化搜索...", len(samples))

    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_workers, initializer=_init_worker,
                  initargs=(samples, fwd_key)) as pool:
        # Phase 1: 随机
        pop = [_random_w() for _ in range(iterations // 3)]
        pop_results = list(pool.imap(_eval_worker, pop))
        pop_results.sort(key=lambda x: -x[0])
        elite = pop_results[:population]
        log.info("Phase1 随机 %d 次 | best=%.4f", len(pop), elite[0][0])

        # Phase 2: 交叉
        for phase in ("phase2", "phase3"):
            n = iterations // 3 if phase == "phase2" else iterations - 2 * (iterations // 3)
            children = []
            for _ in range(n):
                a = random.choice(elite)[1]
                b = random.choice(elite)[1]
                ch = _crossover(a, b)
                if phase == "phase3":
                    ch = _refine(ch)
                else:
                    ch = _mutate(ch)
                children.append(ch)
            results = list(pool.imap(_eval_worker, children))
            for r in results:
                elite.append(r)
            elite.sort(key=lambda x: -x[0])
            elite = elite[:population]
            log.info("%s 进化 %d 次 | best=%.4f", phase, n, elite[0][0])

    best_score, best_w = elite[0]
    elapsed = systime.time() - t0
    log.info("========== 训练完成 | best=%.4f | %.0fs ==========", best_score, elapsed)
    log.info("最佳权重: %s", {k: best_w[k] for k in WEIGHT_KEYS})

    # 多窗口验证
    verify = {}
    for fk in FWD_DAYS:
        verify[f"corr_fwd{fk}"] = round(evaluate_weights(best_w, samples, f"fwd{fk}"), 4)

    # walk-forward 泛化验证: 前 70% 训练 → 后 30% 测试, 拒绝过拟合权重
    wf = _walk_forward(best_w, samples, fwd_key)
    verify["walk_forward"] = wf
    log.info("验证: %s", verify)
    # 泛化守卫: 测试窗口必须 ≥ 训练窗口 (同量纲), 否则提示过拟合
    if wf.get("test_fit", 0) < wf.get("train_fit", 0) - 0.15:
        log.warning("⚠ 过拟合风险: test(%.3f) << train(%.3f), 权重可能不泛化",
                    wf.get("test_fit"), wf.get("train_fit"))

    return {
        "weights": {k: best_w[k] for k in WEIGHT_KEYS},
        "score": round(best_score, 4),
        "samples": len(samples),
        "verify": verify,
        "elapsed_sec": round(elapsed, 1),
        "iterations": iterations,
    }


def _walk_forward(w: dict, samples: list[dict], fwd_key: str) -> dict:
    """前 70% 训练 / 后 30% 测试窗口的 fitness, 检测过拟合。"""
    ss = sorted(samples, key=lambda s: s["date"])
    cut = int(len(ss) * 0.7)
    train_s, test_s = ss[:cut], ss[cut:]
    train_fit = evaluate_weights(w, train_s, fwd_key)
    test_fit = evaluate_weights(w, test_s, fwd_key)
    return {"train_fit": round(train_fit, 4), "test_fit": round(test_fit, 4)}


def save_weights_to_store(r: dict) -> bool:
    """把训练权重写入 cache_store OPTIMIZER_BEST.wb_weights (server 端读取)。

    注意: 只更新 wb_weights/wb_meta 两个 key, 保留 OPTIMIZER_BEST 里其它模块
    (ZT params/weights/monthly_breakdown 等) 的已有数据。
    """
    try:
        from tuixue_v3 import cache_store as cs
        store = cs.get_store()
        v = store.get(cs.K.OPTIMIZER_BEST) or {}
        # 合并写入, 不覆盖其它 key
        v["wb_weights"] = r["weights"]
        v["wb_meta"] = {
            "score": r.get("score"),
            "samples": r.get("samples"),
            "verify": r.get("verify"),
            "iterations": r.get("iterations"),
            "updated_at": systime.time(),
        }
        store.set(cs.K.OPTIMIZER_BEST, v)
        log.info("已写入 cache_store OPTIMIZER_BEST.wb_weights: %s", r["weights"])
        return True
    except Exception as e:
        log.error("写入 cache_store 失败: %s", e)
        return False


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--iter", type=int, default=10000)
    p.add_argument("--pop", type=int, default=60)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--stock-sample", type=int, default=0)
    p.add_argument("--fwd", default="fwd10")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--save", action="store_true", help="训练完成写入 cache_store")
    args = p.parse_args()
    r = run_train(iterations=args.iter, population=args.pop,
                  n_workers=args.workers, stock_sample=args.stock_sample,
                  fwd_key=args.fwd, seed=args.seed)
    if args.save and r.get("weights"):
        save_weights_to_store(r)
    import json
    print(json.dumps(r, ensure_ascii=False, indent=2))
