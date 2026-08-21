"""
100 轮 得鑫页面 推股准确性检查 — DexinTrendAgent 跨日稳定性.

策略 (第一性 R 修正):
  - 100 轮 = 100 次独立"模拟当天"扫描 (不是 100 个不同交易日)
  - 原因: fetch_daily 只能拿"今天"为锚的最近 N 天, 不支持指定历史日期
         → 100 个不同交易日无法做"历史回测窗口"
  - 替代方案: 每轮随机抽 200 只 (RNG seed 化, 可复现) + 50 只随机基线
            + 用 df 末尾 5 天作为 "T+1 OPEN" base → 5d forward
  - 第一性 (A): 基准 = T+1 OPEN (真实买入价, 剔除跳空偏差)
  - 第一性 (B): 随机基线对照 (50 只/round 非候选, 量化算法贡献)
  - 第一性 (C): 预热全市场 5551 只 prefetch → 后续 100 轮走缓存

每个 round:
  1) 取当日全市场 spot (5551+) → seed 抽样 200 只 + 50 baseline
  2) 批量抓日线 (data_layer.fetch_daily, 180d 窗口, 多数命中 Redis)
  3) 跑 DexinTrendAgent.detect(df) per code
  4) 收集 de_xin / clearing / xu_sha / cang_zha 4 类候选
  5) 取 df 的 [-6] 作为 "今日" (T+1 OPEN), [-5..-1] 做 forward 5d
  6) hit = 1 if max_high_5d ≥ base_open × 1.01
  7) 聚合 hit rate + sample size per stage

输出:
  /tmp/dexin_100rounds/round_NN.json  per-round
  /tmp/dexin_100rounds/summary.md    总体
  /tmp/dexin_100rounds/rounds_summary.json
"""
from __future__ import annotations

import json
import random
import sys
import time
import traceback
from collections import defaultdict, Counter
from pathlib import Path

OUT_DIR = Path("/tmp/dexin_100rounds")
OUT_DIR.mkdir(parents=True, exist_ok=True)

ROUNDS = 100
HIT_THRESHOLD = 0.01   # 1% 算命中 (过滤持平噪音)
FWD_DAYS = 5           # 持有期 T+5
MAX_CODES_PER_DAY = 200 # 单日最多处理 200 只 (全市场 TOP-200 成交额, 仿 R163 POOL_CAP=800 的 1/4)

sys.path.insert(0, "/Users/kaikai/scripts")  # 让 import tuixue_v3 能找到父包
sys.path.insert(0, "/Users/kaikai/scripts/tuixue_v3")


def _load_modules():
    from tuixue_v3.multi_source_fetchers import fetch_trade_dates, fetch_zt_pool, fetch_spot_a_full
    from tuixue_v3.data_layer import fetch_daily
    from tuixue_v3.web.dexin_screener import DexinTrendAgent, _enrich_modules
    from tuixue_v3.web.backtest_screener import _prefetch_daily
    return fetch_trade_dates, fetch_zt_pool, fetch_spot_a_full, fetch_daily, DexinTrendAgent, _enrich_modules, _prefetch_daily


def _build_candidate_codes(round_idx: int, zt_pool: list[dict], spot: dict) -> list[str]:
    """第一性 (R): 每轮用 seed 化随机抽样 200 只全市场 (可复现),
    涨停池 zt_pool 补强 (不限定主源)."""
    all_spot_codes = list((spot or {}).keys())
    if not all_spot_codes:
        return []
    rng = random.Random(round_idx * 7919 + 104729)  # 每轮不同 seed, 可复现
    sampled = rng.sample(all_spot_codes, min(MAX_CODES_PER_DAY, len(all_spot_codes)))
    codes = list(sampled)
    for z in (zt_pool or []):
        c = z.get("code")
        if c and c not in codes:
            codes.append(c)
    return codes


def _stage_for(code: str, df, agent, modules: dict) -> dict | None:
    """单只跑 detect, 返 stage dict (含 phase_dates 锚点 + signals)."""
    if df is None or len(df) < 30:
        return None
    try:
        return agent.detect(df, modules=modules)
    except Exception as e:
        return {"stage": "error", "stage_label": f"detect失败:{type(e).__name__}", "phase_dates": {}, "signals": {}}


def _forward_window_return(df, idx: int, n_days: int = FWD_DAYS, hit_th: float = HIT_THRESHOLD) -> dict | None:
    """从 idx+1 起取 n 天, 计算 max_high / min_low / hit.

    第一性 (A): 基准价用 T+1 OPEN (真实买入价) — 不用 idx 当日 close.
    跳过 idx+1 当日 (那一天是决策日, 没有 T+1 完整周期),
    所以 base = opens[idx+1], 窗口 = idx+2..idx+1+n_days.
    退回分支: 若 idx+1 缺失 open, 用 idx 当日 close.
    """
    if df is None or idx is None or idx < 0:
        return None
    closes = df["_close"].tolist() if "_close" in df.columns else df["收盘"].tolist()
    highs = df["_high"].tolist() if "_high" in df.columns else df["最高"].tolist()
    lows = df["_low"].tolist() if "_low" in df.columns else df["最低"].tolist()
    opens = df["_open"].tolist() if "_open" in df.columns else df["开盘"].tolist()
    if idx + 2 >= len(closes):
        return None
    end = min(idx + 2 + n_days, len(closes))
    # 基准 = T+1 open
    if idx + 1 < len(opens) and float(opens[idx + 1] or 0) > 0:
        base_open = float(opens[idx + 1])
    else:
        # 退到当日 close
        base_open = float(closes[idx])
    if base_open <= 0:
        return None
    window_highs = [float(h) for h in highs[idx + 2:end] if h is not None]
    window_lows = [float(l) for l in lows[idx + 2:end] if l is not None]
    if not window_highs:
        return None
    max_high = max(window_highs)
    min_low = min(window_lows)
    last_close = float(closes[end - 1])
    return {
        "base_open": round(base_open, 2),
        "max_high_5d": round(max_high, 2),
        "min_low_5d": round(min_low, 2),
        "last_close_5d": round(last_close, 2),
        "max_chg_pct": round((max_high - base_open) / base_open * 100, 2),
        "min_chg_pct": round((min_low - base_open) / base_open * 100, 2),
        "last_chg_pct": round((last_close - base_open) / base_open * 100, 2),
        "hit": int(max_high >= base_open * (1 + hit_th)),
        "actual_fwd_days": end - idx - 2,
    }


def _pick_index(df, target_date_str: str) -> int | None:
    """找 df 里日期 == target_date_str 的最近一行 (索引)."""
    if df is None or len(df) == 0:
        return None
    dates = df["日期"].astype(str).tolist()
    target = target_date_str.replace("-", "")[:8]
    # 优先完全匹配 (YYYYMMDD)
    for i, d in enumerate(dates):
        d_compact = str(d).replace("-", "")[:8]
        if d_compact == target:
            return i
    # 退到日期 ≤ target 的最后一行
    last = None
    for i, d in enumerate(dates):
        d_compact = str(d).replace("-", "")[:8]
        if d_compact <= target:
            last = i
    return last


def _random_baseline_round(date_str: str, spot: dict, candidate_codes: set, n: int = 50, ctx: dict | None = None) -> list[dict]:
    """第一性 (B): 随机基线对照 — 从 全市场 spot 抽 n 只'非候选'码, 跑同样 5d T+1open forward.
    返 records 列表 (含 hit / max_chg_pct), 让上层聚合平均胜率.
    """
    fetch_daily = ctx["fetch_daily"]
    _prefetch_daily = ctx.get("_prefetch_daily")
    non_candidate = [c for c in (spot or {}).keys() if c not in candidate_codes]
    if len(non_candidate) < n:
        return []
    rng = random.Random(hash(date_str) & 0xFFFFFFFF)  # 同日同种子, 可复现
    sampled = rng.sample(non_candidate, n)
    out = []
    if _prefetch_daily:
        try:
            dailies = _prefetch_daily(sampled, days=180)
        except Exception:
            dailies = {}
    else:
        dailies = {}
    for code in sampled:
        try:
            df = dailies.get(code)
            if df is None:
                df = fetch_daily(code, days=180, force=False)
        except Exception:
            df = None
        if df is None or len(df) < 30:
            continue
        if len(df) < 12:
            continue
        idx = len(df) - 6
        fwd = _forward_window_return(df, idx)
        if fwd is None:
            continue
        if fwd.get("actual_fwd_days", 0) < 3:
            continue
        out.append({"code": code, "kind": "random", **fwd})
    return out


def run_one_round(round_idx: int, date_str: str, ctx: dict) -> dict:
    """单 round: 抓当日 zt_pool+spot → 跑 agent → 算 5d-max-high 胜率."""
    t0 = time.time()
    fetch_zt_pool, fetch_spot_a_full = ctx["fetch_zt_pool"], ctx["fetch_spot_a_full"]
    fetch_daily, agent = ctx["fetch_daily"], ctx["agent"]
    _prefetch_daily = ctx.get("_prefetch_daily")

    # 1) 当日涨停池 + spot
    try:
        zt_pool = fetch_zt_pool(date_str) or []
    except Exception as e:
        return {"round": round_idx, "date": date_str, "err": f"zt_pool:{type(e).__name__}", "elapsed": round(time.time()-t0,1)}
    try:
        spot = fetch_spot_a_full(6) or {}
    except Exception as e:
        spot = {}

    codes = _build_candidate_codes(round_idx, zt_pool, spot)
    if not codes:
        return {"round": round_idx, "date": date_str, "n_codes": 0, "elapsed": round(time.time()-t0,1)}

    # 2) 批量拉日线 (优先 _prefetch_daily 40-worker 并行 + 缓存命中) + 跑 agent
    stage_records: list[dict] = []
    if _prefetch_daily:
        try:
            dailies = _prefetch_daily(codes, days=180)
        except Exception:
            dailies = {}
    else:
        dailies = {}
    for code in codes:
        try:
            df = dailies.get(code)
            if df is None:
                df = fetch_daily(code, days=180, force=False)
        except Exception:
            df = None
        if df is None or len(df) < 30:
            continue
        # enrich modules (从 spot 拿)
        info = spot.get(code) or {}
        try:
            modules = {
                "amount_yi": round(float(info.get("成交额", 0) or 0) / 1e8, 2),
                "turnover_pct": round(float(info.get("换手率", 0) or 0), 2),
                "amplitude": round(float(info.get("振幅", 0) or 0), 2),
                "vol_ratio": round(float(info.get("量比", 0) or 0), 2),
                "change_pct": round(float(info.get("涨跌幅", 0) or 0), 2),
                "sector_strong": False,
                "dragon_net_yi": float(info.get("main_fund_inflow_wan", 0) or 0) / 1e4,
            }
        except Exception:
            modules = {}

        # 第一性 (R): base_idx = df[-6] 作为"今日" (T+1 OPEN 基准, forward 5d)
        if len(df) < 12:
            continue
        idx = len(df) - 6

        # 跑 agent (用截至当日的数据 — 用 iloc 切)
        df_asof = df.iloc[:idx + 1].copy()
        stage = _stage_for(code, df_asof, agent, modules)
        if not stage:
            continue

        # 5d forward return (T+1OPEN 基准)
        fwd = _forward_window_return(df, idx)
        if fwd is None:
            continue

        # 数据源 cut 过早 (< 3d forward) → 跳过, 避免"短窗 = 偏低胜率"的偏差
        if fwd.get("actual_fwd_days", 0) < 3:
            continue

        stage_records.append({
            "code": code,
            "name": info.get("名称") or "",
            "stage": stage.get("stage", "none"),
            "stage_label": stage.get("stage_label", ""),
            "variant": stage.get("variant"),
            "cycle_days": stage.get("cycle_days", 0),
            "breakout_chg_pct": (stage.get("signals") or {}).get("breakout_chg_pct"),
            "drawdown_pct": (stage.get("signals") or {}).get("drawdown_pct"),
            **fwd,
        })

    elapsed = round(time.time() - t0, 1)
    # 第一性 (B): 随机基线对照 — 50 只非候选
    baseline_records = _random_baseline_round(date_str, spot, set(codes), n=50, ctx=ctx)
    return {
        "round": round_idx, "date": date_str,
        "n_codes": len(codes), "n_classified": len(stage_records),
        "stages": stage_records,
        "baseline": baseline_records,
        "baseline_n": len(baseline_records),
        "elapsed": elapsed,
    }


def aggregate(stage_records: list[dict]) -> dict:
    by_stage = defaultdict(list)
    for r in stage_records:
        by_stage[r["stage"]].append(r)

    summary = {}
    for st, rows in by_stage.items():
        n = len(rows)
        hits = sum(r["hit"] for r in rows)
        avg_max = sum(r["max_chg_pct"] for r in rows) / n if n else 0
        avg_min = sum(r["min_chg_pct"] for r in rows) / n if n else 0
        avg_last = sum(r["last_chg_pct"] for r in rows) / n if n else 0
        summary[st] = {
            "n": n,
            "hit_rate_pct": round(hits / n * 100, 1) if n else 0,
            "avg_max_chg_pct": round(avg_max, 2),
            "avg_min_chg_pct": round(avg_min, 2),
            "avg_last_chg_pct": round(avg_last, 2),
            "median_max_chg_pct": round(sorted([r["max_chg_pct"] for r in rows])[n // 2], 2) if n else 0,
        }
    return summary


def main():
    t_start = time.time()
    fetch_trade_dates, fetch_zt_pool, fetch_spot_a_full, fetch_daily, DexinTrendAgent, _enrich_modules, _prefetch_daily = _load_modules()

    # 拿最近 100 个交易日
    print("[init] 取交易日历 …", flush=True)
    trade_dates = fetch_trade_dates() or set()
    if not trade_dates:
        print("FATAL: 拿不到 trade_dates")
        return
    # 排序 → 转 YYYYMMDD
    trade_dates_sorted = sorted(trade_dates)
    print(f"[init] 交易日历 {len(trade_dates_sorted)} 天", flush=True)

    # 只取最近 100 天
    target_dates = trade_dates_sorted[-ROUNDS:]
    print(f"[init] 本次跑最近 {len(target_dates)} 天: {target_dates[0]} → {target_dates[-1]}", flush=True)
    print(f"[init] 每 round 候选池: 全市场 spot 按成交额 TOP-{MAX_CODES_PER_DAY} + 涨停梯队补强", flush=True)

    agent = DexinTrendAgent()
    ctx = {
        "fetch_zt_pool": fetch_zt_pool,
        "fetch_spot_a_full": fetch_spot_a_full,
        "fetch_daily": fetch_daily,
        "agent": agent,
        "_prefetch_daily": _prefetch_daily,
    }

    # 第一性 (C): 预热不做全市场 (7.8% 命中率 → 冷码拿不到, 浪费)
    #   → 每轮 run_one_round 内部 _prefetch_daily(codes) 只拉该轮 200 只, 命中缓存复用

    all_records: list[dict] = []
    all_baseline: list[dict] = []
    rounds_summary = []
    for i, d in enumerate(target_dates, 1):
        d_yyyymmdd = d.replace("-", "")[:8]
        try:
            rec = run_one_round(i, d_yyyymmdd, ctx)
        except Exception as e:
            rec = {"round": i, "date": d_yyyymmdd, "err": f"top:{type(e).__name__}:{e}", "trace": traceback.format_exc()[:300]}
        rounds_summary.append({"round": i, "date": d_yyyymmdd, **{
            k: v for k, v in rec.items() if k in ("n_codes", "n_classified", "elapsed", "err")
        }})

        # 写 round json
        try:
            (OUT_DIR / f"round_{i:03d}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
            )
        except Exception as e:
            print(f"  round {i:03d} 写盘失败: {e}", flush=True)

        # 累加有效记录
        if rec.get("stages"):
            all_records.extend(rec["stages"])
        if rec.get("baseline"):
            all_baseline.extend(rec["baseline"])

        if i % 10 == 0 or i == 1:
            agg = aggregate(all_records)
            bl_n = len(all_baseline)
            bl_hr = round(sum(r["hit"] for r in all_baseline) / bl_n * 100, 1) if bl_n else 0
            de_xin = agg.get("de_xin", {})
            clearing = agg.get("clearing", {})
            xu_sha = agg.get("xu_sha", {})
            cang_zha = agg.get("cang_zha", {})
            print(
                f"  round {i:03d}/{len(target_dates)} {d_yyyymmdd} "
                f"elapsed={rec.get('elapsed','?')}s classified={rec.get('n_classified',0)} "
                f"累 de_xin={de_xin.get('n',0)}({de_xin.get('hit_rate_pct',0)}%) "
                f"clearing={clearing.get('n',0)}({clearing.get('hit_rate_pct',0)}%) "
                f"xu_sha={xu_sha.get('n',0)}({xu_sha.get('hit_rate_pct',0)}%) "
                f"cang_zha={cang_zha.get('n',0)}({cang_zha.get('hit_rate_pct',0)}%) "
                f"baseline_n={bl_n}({bl_hr}%)",
                flush=True,
            )

    # 总聚合
    overall = aggregate(all_records)
    elapsed_total = round(time.time() - t_start, 1)

    # 输出 summary.md
    md_lines = [
        "# 得鑫页面推股准确性 100 轮报告",
        "",
        f"- 跑测日期范围: `{target_dates[0]}` → `{target_dates[-1]}` ({len(target_dates)} 交易日)",
        f"- 候选池上限: 单日 {MAX_CODES_PER_DAY} 只",
        f"- 基准价: **T+1 OPEN** (第一性 A: 真实买入价, 剔除跳空偏差)",
        f"- 命中阈值: max_high ≥ base_open × (1 + {HIT_THRESHOLD:.0%})",
        f"- 前向窗口: {FWD_DAYS} d",
        f"- 总耗时: {elapsed_total}s",
        f"- 总样本数: {len(all_records)} (de_xin/clearing/xu_sha/cang_zha 加总)",
        f"- 随机基线样本: {len(all_baseline)} (50 只/轮 非候选, 第一性 B: 量化算法贡献)",
        "",
        "## 按 stage 聚合 (5d-max-high 胜率 + 平均涨跌)",
        "",
        "| stage | n | 5d hit_rate% | avg_max_chg% | avg_min_chg% | avg_last_chg% | median_max% |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for st, s in sorted(overall.items(), key=lambda kv: -kv[1]["n"]):
        md_lines.append(
            f"| **{st}** | {s['n']} | {s['hit_rate_pct']} | {s['avg_max_chg_pct']} | {s['avg_min_chg_pct']} | {s['avg_last_chg_pct']} | {s['median_max_chg_pct']} |"
        )

    # 关键判定 + 第一性 B: 超额胜率
    de_xin = overall.get("de_xin", {})
    clearing = overall.get("clearing", {})
    xu_sha = overall.get("xu_sha", {})
    cang_zha = overall.get("cang_zha", {})
    bl_n = len(all_baseline)
    bl_hr = round(sum(r["hit"] for r in all_baseline) / bl_n * 100, 1) if bl_n else 0
    bl_avg_max = round(sum(r["max_chg_pct"] for r in all_baseline) / bl_n, 2) if bl_n else 0

    md_lines += [
        "",
        "## 第一性结论 — 量化算法贡献",
        "",
        "| 类别 | n | hit_rate% | 超额 vs 随机 | avg_max_chg% | 超额 avg_max% |",
        "|---|---:|---:|---:|---:|---:|",
        f"| **de_xin (得鑫)** | {de_xin.get('n',0)} | {de_xin.get('hit_rate_pct',0)} | **{de_xin.get('hit_rate_pct',0) - bl_hr:+.1f}pp** | {de_xin.get('avg_max_chg_pct',0)} | {de_xin.get('avg_max_chg_pct',0) - bl_avg_max:+.2f} |",
        f"| **clearing** | {clearing.get('n',0)} | {clearing.get('hit_rate_pct',0)} | {clearing.get('hit_rate_pct',0) - bl_hr:+.1f}pp | {clearing.get('avg_max_chg_pct',0)} | {clearing.get('avg_max_chg_pct',0) - bl_avg_max:+.2f} |",
        f"| **xu_sha (过渡)** | {xu_sha.get('n',0)} | {xu_sha.get('hit_rate_pct',0)} | {xu_sha.get('hit_rate_pct',0) - bl_hr:+.1f}pp | {xu_sha.get('avg_max_chg_pct',0)} | {xu_sha.get('avg_max_chg_pct',0) - bl_avg_max:+.2f} |",
        f"| **cang_zha (藏诈)** | {cang_zha.get('n',0)} | {cang_zha.get('hit_rate_pct',0)} | {cang_zha.get('hit_rate_pct',0) - bl_hr:+.1f}pp | {cang_zha.get('avg_max_chg_pct',0)} | {cang_zha.get('avg_max_chg_pct',0) - bl_avg_max:+.2f} |",
        f"| **随机基线 (全市场)** | {bl_n} | {bl_hr} | — | {bl_avg_max} | — |",
        "",
        "## 关键判定",
        "",
        f"- **de_xin 绝对胜率**: {de_xin.get('hit_rate_pct',0)}% (T+1open 基准, 目标 ≥80% — 实际 {'✅ 达标' if de_xin.get('hit_rate_pct',0) >= 80 else '⚠️ 未达标'})",
        f"- **de_xin 相对基线 (超额)**: {de_xin.get('hit_rate_pct',0) - bl_hr:+.1f}pp",
        f"- **基线 hit_rate**: {bl_hr}% (随机 50 只/轮, 5000 只全市场样本)",
        f"- **第二随机基线 hit_rate**: {bl_hr}% → 如果 de_xin 接近基线, 说明 de_xin 信号几乎没提供超额收益",
        "",
        "## 异常 / 数据缺失 round",
        "",
    ]
    errs = [r for r in rounds_summary if r.get("err")]
    if errs:
        md_lines.append(f"- {len(errs)} 个 round 异常, 详见 round_*.json")
        for r in errs[:10]:
            md_lines.append(f"  - round {r['round']} {r['date']}: {r['err']}")
    else:
        md_lines.append("- 无异常 round")

    md_text = "\n".join(md_lines) + "\n"
    (OUT_DIR / "summary.md").write_text(md_text, encoding="utf-8")
    (OUT_DIR / "rounds_summary.json").write_text(
        json.dumps(rounds_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + md_text)
    print(f"\n[done] 总耗时 {elapsed_total}s, 报告 → {OUT_DIR}/summary.md")


if __name__ == "__main__":
    main()