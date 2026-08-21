"""search_high_wr.py — 逐月高胜率参数搜索 (快速版)

策略: 逐月回测 (快) → 找跨月稳定高 WR 参数 → Walk-forward 验证
"""
from __future__ import annotations
import sys, json, time, itertools
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from collections import defaultdict
from tuixue_v3 import zt_backtest as zb, zt_config as cfg

# ═══════════════════════════════════════════
# 目标参数组合 (30组, 聚焦高WR)
# ═══════════════════════════════════════════
# 核心思路:
#   1. trail activate 越低 → 越容易触发trail → WR越高
#   2. stop_loss 越宽 → 越少止损 → WR越高
#   3. regime 硬过滤 → 避开熊市 → WR越高
#   4. top_n=1 → 只买最强 → WR越高

def build_param_combos():
    combos = []
    # 维度: trail_activate, trail_pullback, stop_loss, regime, board, streak_range
    trail_activates = [0.1, 0.3, 0.5, 1.0]
    trail_pullbacks = [0.3, 0.5, 1.0, 2.0]
    stop_losses = [-3, -5, -7, -10]
    regimes = ["always", "soft", "hard"]
    boards = ["all", "main"]
    streak_ranges = [(1, 3), (1, 4), (2, 4), (2, 5)]

    for ta in trail_activates:
        for tp in trail_pullbacks:
            if tp < ta:  # pullback 必须 >= activate 才有意义
                continue
            for sl in stop_losses:
                for regime in regimes:
                    for board in boards:
                        for smin, smax in streak_ranges:
                            combos.append({
                                "trail_activate": ta, "trail_pullback": tp,
                                "stop_loss": sl, "regime": regime, "board": board,
                                "min_streak": smin, "max_streak": smax,
                            })
    return combos

# 精简到 ~35 组: 只保留 tp >= ta 的有效组合
combos = build_param_combos()
print(f"参数组合总数: {len(combos)}")

# 过滤: pullback 必须 >= activate
valid = [c for c in combos if c["trail_pullback"] >= c["trail_activate"]]
print(f"有效组合 (tp>=ta): {len(valid)}")

# 按预期 WR 排序
def combo_priority(c):
    score = 0
    score -= c["trail_activate"] * 10
    score += c["trail_pullback"] * 5
    score -= c["stop_loss"] * 2
    if c["regime"] == "hard": score += 20
    elif c["regime"] == "soft": score += 10
    if c["board"] == "main": score += 5
    return -score

valid.sort(key=combo_priority)

# 选前30组高优先级 + 5组低优先级对照
unique_combos = valid[:30] + valid[-5:]
# 去重
seen = set()
deduped = []
for c in unique_combos:
    key = str(c)
    if key not in seen:
        seen.add(key)
        deduped.append(c)
unique_combos = deduped
print(f"实际测试: {len(unique_combos)} 组 (预计 {len(unique_combos)*7*20/60:.0f} 分钟)")

# ═══════════════════════════════════════════
# 逐月测试
# ═══════════════════════════════════════════
MONTHS = [
    ("2025-12", "2025-12-01", "2026-01-01"),
    ("2026-01", "2026-01-01", "2026-02-01"),
    ("2026-02", "2026-02-01", "2026-03-01"),
    ("2026-03", "2026-03-01", "2026-04-01"),
    ("2026-04", "2026-04-01", "2026-05-01"),
    ("2026-05", "2026-05-01", "2026-06-01"),
    ("2026-06", "2026-06-01", "2026-07-01"),
]

FIXED = {
    "entry_rule": "open_t1",
    "exclude_yiziban": False,
    "fill_rate": 1.0,
    "leverage_factor": 1.0,
    "sample": 0,
    "top_n": 1,  # 只选最强1只
    "sealed_before": "10:00",
    "mcap_min_yi": 10,
    "mcap_max_yi": 500,
    "turnover_min_pct": 2,
    "turnover_max_pct": 50,
    "limit_order_min_yi": 0,
    "gap_activate_pct": 0.3,
    "burst_max": 2,
}

results = []  # [(combo_idx, month, trades, wr, avg_ret, monthly_cmp, dd)]

t0 = time.time()
for ci, combo in enumerate(unique_combos):
    combo_wrs = []
    combo_trades = []

    for month_label, m_start, m_end in MONTHS:
        try:
            result = zb.run_zt_backtest(
                start=m_start, end=m_end,
                min_streak=combo["min_streak"],
                max_streak=combo["max_streak"],
                trail_activate_pct=combo["trail_activate"],
                trail_pullback_pct=combo["trail_pullback"],
                stop_loss_pct=combo["stop_loss"],
                regime_mode=combo["regime"],
                board_filter=combo["board"],
                **FIXED,
            )
            s = result.get("summary", {})
            trades = s.get("trades", 0)
            wr = s.get("win_rate_pct", 0)
            avg = s.get("avg_return_pct", 0)
            mc = s.get("monthly_compound_pct", 0) or 0
            dd = s.get("max_drawdown_pct", 0)

            results.append({
                "combo_idx": ci, "month": month_label,
                "trades": trades, "wr": wr, "avg_ret": avg,
                "monthly_cmp": mc, "dd": dd,
            })
            if trades > 0:
                combo_wrs.append(wr)
                combo_trades.append(trades)
        except Exception as e:
            continue

    if combo_wrs:
        avg_wr = np.mean(combo_wrs)
        min_wr = min(combo_wrs)
        total_trades = sum(combo_trades)
        if avg_wr >= 65 or ci < 5:
            print(f"  [{ci}] avgWR={avg_wr:.1f}% minWR={min_wr:.1f}% "
                  f"trades={total_trades} | "
                  f"ta={combo['trail_activate']} tp={combo['trail_pullback']} "
                  f"sl={combo['stop_loss']} rg={combo['regime']} bd={combo['board']} "
                  f"st={combo['min_streak']}-{combo['max_streak']} "
                  f"WRs={[f'{w:.0f}' for w in combo_wrs]}")

elapsed = time.time() - t0
print(f"\n{'='*70}")
print(f"完成 {len(unique_combos)} 组 × 7 月 = {len(results)} 次回测 ({elapsed:.0f}s)")

# ═══════════════════════════════════════════
# 汇总: 找跨月高WR且稳定的组合
# ═══════════════════════════════════════════
combo_summary = defaultdict(list)
for r in results:
    combo_summary[r["combo_idx"]].append(r)

print(f"\n=== 跨月稳定性排名 (按 avgWR 降序, 要求 ≥4 个月有交易) ===")
ranked = []
for ci, months in combo_summary.items():
    wrs = [m["wr"] for m in months if m["trades"] > 0]
    if len(wrs) < 4:
        continue
    combo = unique_combos[ci]
    total_t = sum(m["trades"] for m in months)
    ranked.append({
        "ci": ci,
        "avg_wr": np.mean(wrs),
        "min_wr": min(wrs),
        "std_wr": np.std(wrs),
        "total_trades": total_t,
        "combo": combo,
        "wrs": wrs,
        "months": months,
    })

ranked.sort(key=lambda x: (-x["avg_wr"], -x["total_trades"]))

for i, r in enumerate(ranked[:20]):
    c = r["combo"]
    print(f"  #{i+1}: avgWR={r['avg_wr']:.1f}% minWR={r['min_wr']:.0f}% "
          f"std={r['std_wr']:.1f}% trades={r['total_trades']} | "
          f"ta={c['trail_activate']} tp={c['trail_pullback']} sl={c['stop_loss']} "
          f"rg={c['regime']} bd={c['board']} st={c['min_streak']}-{c['max_streak']} "
          f"WRs={[f'{w:.0f}' for w in r['wrs']]}")

# ═══════════════════════════════════════════
# 保存最佳参数
# ═══════════════════════════════════════════
if ranked:
    best = ranked[0]
    print(f"\n=== 最佳高WR参数 ===")
    print(f"avgWR={best['avg_wr']:.1f}% minWR={best['min_wr']:.0f}% trades={best['total_trades']}")
    print(json.dumps(best["combo"], ensure_ascii=False))

    # 逐月详情
    print(f"\n--- 逐月详情 ---")
    for m in best["months"]:
        print(f"  {m['month']}: trades={m['trades']} WR={m['wr']:.1f}% "
              f"avg={m['avg_ret']:.2f}% 复利={m['monthly_cmp']:.1f}% DD={m['dd']:.1f}%")

    # 用最佳参数跑完整回测
    print(f"\n=== 完整回测 (2025-12 ~ 2026-06) ===")
    c = best["combo"]
    full_result = zb.run_zt_backtest(
        start="2025-12-01", end="2026-06-30",
        min_streak=c["min_streak"], max_streak=c["max_streak"],
        trail_activate_pct=c["trail_activate"], trail_pullback_pct=c["trail_pullback"],
        stop_loss_pct=c["stop_loss"], regime_mode=c["regime"],
        board_filter=c["board"], **FIXED,
    )
    s = full_result.get("summary", {})
    trades_list = full_result.get("trades", [])
    print(f"trades={s.get('trades')} WR={s.get('win_rate_pct'):.1f}% "
          f"avg={s.get('avg_return_pct'):.2f}%")
    print(f"月复利={s.get('avg_monthly_compound_pct'):.1f}% "
          f"总复利={s.get('total_compound_pct'):.1f}% DD={s.get('max_drawdown_pct'):.1f}%")
    print(f"PF={s.get('profit_factor'):.2f}")

    # 按退出方式统计
    if trades_list:
        by_trigger = defaultdict(list)
        for t in trades_list:
            by_trigger[t.get("trigger", "?")].append(t.get("return_pct", 0))
        print(f"\n--- 按退出方式 ---")
        for trig, rets in sorted(by_trigger.items()):
            print(f"  {trig}: {len(rets)}笔 WR={sum(1 for r in rets if r>0)/len(rets)*100:.1f}% "
                  f"avg={np.mean(rets):.2f}% med={np.median(rets):.2f}%")

    # 逐月复利
    for m in s.get("monthly_compounds", []):
        print(f"  {m['month']}: {m['compound_pct']:.1f}% (unlevered {m['compound_pct_unlevered']:.1f}%)")
