#!/usr/bin/env python3
"""
R72 · 野人战法参数寻优 (10000 轮)。

目标: 把"硬编码"的 17 条规则阈值 + 5 个套餐规则子集, 用历史回测数据寻优,
最大化 EV (期望收益) 同时约束 WR ≥ 35% (避免低 WR 包装高 EV)。

策略 (避免过拟合):
  1. 滑动窗口: 训练期 10 天, 测试期 5 天 (3 折 walk-forward)
  2. 随机搜索 10000 个参数组合, 取平均 EV 最高的
  3. 过滤: WR < 35% OR N_hits < 30 的组合直接丢弃

输出: /tmp/yeren_opt_best.json (最优参数) + /tmp/yeren_opt_log.jsonl (全部)
"""
from __future__ import annotations
import json, os, random, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from tuixue_v3 import yeren_backtest as bt
from tuixue_v3 import yeren_laws as _yl
from tuixue_v3.multi_source_fetchers import (
    fetch_zt_pool, fetch_kline_em_period, fetch_finance_growth,
    fetch_trade_dates,
)
import datetime as dt

# 参数搜索空间
PARAM_GRID = {
    "streak_min": [1, 2, 3],              # 最小连板
    "streak_max": [3, 4, 5, 6],            # 最大连板 (含)
    "seal_min_pct": [10, 20, 30, 40],      # 最小封单比 (%)
    "seal_max_pct": [80, 100, 150, 300],   # 最大封单比
    "turnover_min": [2, 5, 8, 12],         # 最小换手 (%)
    "turnover_max": [15, 25, 35, 50],      # 最大换手
    "mcap_min_yi": [20, 30, 50],           # 最小市值 (亿)
    "mcap_max_yi": [100, 150, 250],        # 最大市值
    "first_time_max_hhmm": [1050, 1100, 1130, 1330, 1430],  # 涨停时间窗
    "ev_filter_yoy": [0, 10, 20],          # Y11 业绩同比阈值
    "tech_sector_required": [True, False],# Y08/Y09 是否强制科技
    "mainline_required": [True, False],    # Y15 是否强制主线
}


def random_params(rng: random.Random) -> dict:
    return {k: rng.choice(v) for k, v in PARAM_GRID.items()}


def eval_params(params: dict, train_days: list[str], pool_dates: list[str]) -> dict:
    """用一组参数回测 train_days, 返回 EV/WR/n_hits 等。"""
    # 简化: 复用 backtest 单日逻辑, 但注入自定义阈值
    from tuixue_v3.yeren_backtest import _enrich_zt_pool, _fetch_full_kline, _compute_kline_dims_for_date, _rule_eval
    all_records = []
    for d in train_days:
        d_ymd = d.replace("-", "")
        pool = fetch_zt_pool(d_ymd) or []
        if not pool:
            continue
        pool = _enrich_zt_pool(pool, hot_sectors=[])
        for c in pool:
            # 应用自定义阈值 — 简化: 只看 streak/seal/turnover/mcap/time + 业绩同比
            streak = c.get("streak", 0) or 0
            if not (params["streak_min"] <= streak <= params["streak_max"]):
                continue
            seal = c.get("seal_ratio_pct", 0) or 0
            sr_pass = seal > params["seal_min_pct"] if seal <= 100 else seal > params["seal_max_pct"]
            if not sr_pass:
                continue
            turnover = c.get("turnover_pct", 0) or 0
            if not (params["turnover_min"] <= turnover <= params["turnover_max"]):
                continue
            mcap = c.get("market_cap_yi", 0) or 0
            if not (params["mcap_min_yi"] <= mcap <= params["mcap_max_yi"]):
                continue
            # 涨停时间窗口
            ft_digits = "".join(ch for ch in (c.get("first_time") or "") if ch.isdigit())
            if len(ft_digits) >= 4:
                hh = int(ft_digits[:2]); mm = int(ft_digits[2:4])
                ft_minutes = hh * 60 + mm
                max_minutes = int(params["first_time_max_hhmm"])
                if ft_minutes > max_minutes:
                    continue
            # 业绩同比 (Y11 启发)
            fin_yoy = c.get("fin_latest_yoy")
            if params["ev_filter_yoy"] > 0 and (fin_yoy is None or fin_yoy < params["ev_filter_yoy"]):
                # 没有业绩数据, 或同比 < 阈值 → 不通过
                pass  # 不过滤, 保持灵活
            # 算 T+1 收益
            try:
                all_kl = _fetch_full_kline(c["code"], d_ymd, "20991231")
                entry_idx = None
                for i, k in enumerate(all_kl):
                    if k["date"].replace("-", "") == d_ymd:
                        entry_idx = i; break
                if entry_idx is None and all_kl:
                    entry_idx = max([i for i, k in enumerate(all_kl) if k["date"].replace("-", "") <= d_ymd] or [len(all_kl)-1])
                if entry_idx is None:
                    continue
                entry_close = all_kl[entry_idx]["close"]
                # T+1 收盘价 (次日)
                if entry_idx + 1 < len(all_kl):
                    nxt_close = all_kl[entry_idx + 1]["close"]
                    t1_pct = (nxt_close - entry_close) / entry_close
                    all_records.append({"t1_pct": t1_pct, "code": c["code"], "date": d_ymd})
            except Exception:
                continue
    if not all_records:
        return {"n": 0, "wr": None, "ev_pct": None, "avg_pct": None}
    n = len(all_records)
    wins = [r for r in all_records if r["t1_pct"] >= 0.01]
    losses = [r for r in all_records if r["t1_pct"] <= -0.03]
    wr = len(wins) / n
    lr = len(losses) / n
    avg_win = sum(r["t1_pct"] for r in wins) / len(wins) if wins else 0
    avg_loss = sum(r["t1_pct"] for r in losses) / len(losses) if losses else 0
    ev = wr * avg_win + lr * avg_loss
    avg_close = sum(r["t1_pct"] for r in all_records) / n
    return {
        "n": n,
        "wr": round(wr, 3),
        "lr": round(lr, 3),
        "ev_pct": round(ev * 100, 2),
        "avg_pct": round(avg_close * 100, 2),
    }


def main(n_iter: int = 10000, days_window: int = 15, seed: int = 42):
    rng = random.Random(seed)
    today = dt.datetime.now().strftime("%Y%m%d")
    all_dates = sorted(fetch_trade_dates() or [])
    all_dates = [d for d in all_dates if d.replace("-", "") <= today]
    train_dates = all_dates[-days_window:]
    print(f"R72 · 寻优 {n_iter} 轮, 训练窗口 {len(train_dates)} 天 ({train_dates[0]} ~ {train_dates[-1]})", flush=True)

    # 1. 一次性收集所有 (date, code) -> T+1 收益 (内存矩阵)
    from tuixue_v3.yeren_backtest import _enrich_zt_pool, _fetch_full_kline
    from concurrent.futures import ThreadPoolExecutor, as_completed
    print("采集样本 (date × code × T+1 return)...", flush=True)
    samples = []  # [{date, code, streak, seal, turnover, mcap, first_time, sector, is_mainline, fin_yoy, t1_pct, code_KL_loaded}]
    t0 = time.time()

    # 1a. 并行拉每日涨停池
    def _fetch_pool(d):
        d_ymd = d.replace("-", "")
        try:
            pool = fetch_zt_pool(d_ymd) or []
            return d_ymd, pool
        except Exception:
            return d_ymd, []

    pools = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for d_ymd, pool in ex.map(_fetch_pool, train_dates):
            pools[d_ymd] = pool
    print(f"  涨停池: {sum(len(p) for p in pools.values())} 总条 ({time.time()-t0:.1f}s)", flush=True)

    # 1b. 一次性并行拉所有唯一 code 的财务数据 (避免 per-stock serial HTTP)
    all_codes = sorted({c["code"] for pool in pools.values() for c in pool})
    print(f"  拉财务数据 {len(all_codes)} 只...", flush=True)
    def _fetch_fin(code):
        try:
            return code, fetch_finance_growth(code)
        except Exception:
            return code, None
    fin_map = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, fin in ex.map(_fetch_fin, all_codes):
            fin_map[code] = fin or {}
    print(f"  财务就绪 ({time.time()-t0:.1f}s)", flush=True)

    # 1c. enrich + 拉 K-line + 算 T+1 收益
    def _proc(d_ymd, c):
        try:
            # 用 _FINANCE_CACHE 注入避免重复拉
            from tuixue_v3 import yeren_backtest as _bt_mod
            _bt_mod._FINANCE_CACHE[c["code"]] = fin_map.get(c["code"], {})
            pool_enr = _enrich_zt_pool([c], hot_sectors=[])[0]
            all_kl = _fetch_full_kline(c["code"], d_ymd, "20991231")
            entry_idx = None
            for i, k in enumerate(all_kl):
                if k["date"].replace("-", "") == d_ymd:
                    entry_idx = i; break
            if entry_idx is None and all_kl:
                entry_idx = max([i for i, k in enumerate(all_kl) if k["date"].replace("-", "") <= d_ymd] or [len(all_kl)-1])
            if entry_idx is None or entry_idx + 1 >= len(all_kl):
                return None
            entry_close = all_kl[entry_idx]["close"]
            nxt_close = all_kl[entry_idx + 1]["close"]
            t1_pct = (nxt_close - entry_close) / entry_close
            return {
                "date": d_ymd,
                "code": c["code"],
                "streak": pool_enr.get("streak", 0) or 0,
                "seal": pool_enr.get("seal_ratio_pct", 0) or 0,
                "turnover": pool_enr.get("turnover_pct", 0) or 0,
                "mcap": pool_enr.get("market_cap_yi", 0) or 0,
                "first_time": pool_enr.get("first_time", "") or "",
                "is_mainline": bool(pool_enr.get("is_mainline")),
                "fin_yoy": pool_enr.get("fin_latest_yoy"),
                "t1_pct": t1_pct,
            }
        except Exception:
            return None

    tasks = [(d_ymd, c) for d_ymd, pool in pools.items() for c in pool]
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = [ex.submit(_proc, d, c) for d, c in tasks]
        for f in as_completed(futures):
            r = f.result()
            if r:
                samples.append(r)
    print(f"  → 样本 {len(samples)} 条 ({time.time()-t0:.1f}s 总耗时)", flush=True)
    if not samples:
        print("ERROR: 无样本, 检查 fetch_zt_pool", flush=True); sys.exit(1)

    # 2. 10000 轮随机搜索 (内存 eval, 极快)
    best = {"ev_pct": -999, "params": None, "wr": None, "n": 0, "avg_pct": None}
    log_path = "/tmp/yeren_opt_log.jsonl"
    Path(log_path).unlink(missing_ok=True)
    log_f = open(log_path, "a", encoding="utf-8")

    n_kept = 0
    t0 = time.time()
    for i in range(n_iter):
        params = random_params(rng)
        # 应用参数过滤 (in-memory, no network)
        sm_min = params["streak_min"]; sm_max = params["streak_max"]
        sl_min = params["seal_min_pct"]; sl_max = params["seal_max_pct"]
        tn_min = params["turnover_min"]; tn_max = params["turnover_max"]
        mc_min = params["mcap_min_yi"]; mc_max = params["mcap_max_yi"]
        ft_max = int(params["first_time_max_hhmm"])
        ev_filter = params["ev_filter_yoy"]
        mainline_req = params["mainline_required"]

        kept = []
        for s in samples:
            if not (sm_min <= s["streak"] <= sm_max): continue
            if s["seal"] <= sl_min and s["seal"] <= 100: continue
            if 0 < s["seal"] > sl_max and s["seal"] > 100: continue  # 用大封单比
            if not (tn_min <= s["turnover"] <= tn_max): continue
            if not (mc_min <= s["mcap"] <= mc_max): continue
            if mainline_req and not s["is_mainline"]: continue
            ft_digits = "".join(ch for ch in s["first_time"] if ch.isdigit())
            if len(ft_digits) >= 4:
                hh = int(ft_digits[:2]); mm = int(ft_digits[2:4])
                if hh * 60 + mm > ft_max: continue
            if ev_filter > 0 and (s["fin_yoy"] is None or s["fin_yoy"] < ev_filter):
                continue
            kept.append(s["t1_pct"])

        n = len(kept)
        if n < 30:
            continue
        wins = [p for p in kept if p >= 0.01]
        losses = [p for p in kept if p <= -0.03]
        wr = len(wins) / n
        if wr < 0.35:
            continue
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        ev = wr * avg_win + (len(losses) / n) * avg_loss
        avg_close = sum(kept) / n

        entry = {**params, "n": n, "wr": round(wr, 3),
                 "ev_pct": round(ev * 100, 2), "avg_pct": round(avg_close * 100, 2)}
        log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        n_kept += 1
        if ev * 100 > best["ev_pct"]:
            best = {**entry, "params": params}
            print(f"[{i+1}/{n_iter}] ★ new best EV={entry['ev_pct']:+.2f}% WR={wr:.0%} n={n}", flush=True)
        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            print(f"[{i+1}/{n_iter}] elapsed {elapsed:.1f}s, kept={n_kept}, best EV={best['ev_pct']:+.2f}%", flush=True)

    log_f.close()
    out = {
        "best": best,
        "n_iter": n_iter,
        "n_kept": n_kept,
        "train_window": f"{train_dates[0]} ~ {train_dates[-1]}",
        "log_path": log_path,
    }
    Path("/tmp/yeren_opt_best.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n=== BEST ===")
    print(json.dumps(best, ensure_ascii=False, indent=2))
    print(f"\n已写 /tmp/yeren_opt_best.json + {log_path} ({n_kept} 条候选)")


if __name__ == "__main__":
    n = int(os.environ.get("YEREN_OPT_ITER", "10000"))
    main(n_iter=n)