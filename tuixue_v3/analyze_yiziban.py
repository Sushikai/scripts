"""analyze_yiziban.py — 一字板次日破封行为分析"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from collections import defaultdict
from tuixue_v3 import zt_backtest, zt_config as cfg

result = zt_backtest.run_zt_backtest(
    start=cfg.ZT_START, end=cfg.ZT_OPTIMIZE_WINDOW_END,
    min_streak=1, max_streak=20, burst_max=10,
    sealed_before="15:00", mcap_min_yi=1, mcap_max_yi=100000,
    turnover_min_pct=0.01, turnover_max_pct=100, limit_order_min_yi=0,
    top_n=10, trail_activate_pct=0.5, trail_pullback_pct=1.5,
    stop_loss_pct=-3, entry_rule="open_t1", gap_activate_pct=0.2,
    board_filter="all", regime_mode="always",
    exclude_yiziban=False, fill_rate=1.0, leverage_factor=1.0,
    sample=0,
)
trades = result.get("trades", [])
yizi = [t for t in trades if t.get("is_yiziban")]
normal = [t for t in trades if not t.get("is_yiziban")]
print(f"总交易: {len(trades)} | 一字板: {len(yizi)} | 普通板: {len(normal)}")

if not yizi:
    print("ERROR: 没抓到一字板，检查 is_yiziban 传递")
    sys.exit(1)

# 一字板收益
y_ret = [t["return_pct"] for t in yizi]
print(f"\n=== 一字板 T+1开盘买入 ===")
print(f"笔数={len(yizi)} WR={sum(1 for r in y_ret if r>0)/len(y_ret)*100:.1f}% "
      f"avg={np.mean(y_ret):.2f}% med={np.median(y_ret):.2f}% std={np.std(y_ret):.2f}%")
print(f"P10={np.percentile(y_ret,10):.2f} P25={np.percentile(y_ret,25):.2f} P75={np.percentile(y_ret,75):.2f} P90={np.percentile(y_ret,90):.2f}")
print(f"max={max(y_ret):.2f} min={min(y_ret):.2f}")

# 按退出方式
print(f"\n--- 按退出方式 ---")
by_exit = defaultdict(list)
for t in yizi:
    by_exit[t.get("trigger","?")].append(t.get("return_pct",0))
for k, v in sorted(by_exit.items()):
    print(f"  {k}: {len(v)}笔 avg={np.mean(v):.2f}% wr={sum(1 for r in v if r>0)/len(v)*100:.1f}%")

# 按连板数
print(f"\n--- 按连板数 ---")
by_streak = defaultdict(list)
for t in yizi:
    by_streak[t.get("streak",0)].append(t.get("return_pct",0))
for s in sorted(by_streak):
    v = by_streak[s]
    print(f"  {s}连板: {len(v)}笔 avg={np.mean(v):.2f}% med={np.median(v):.2f}% wr={sum(1 for r in v if r>0)/len(v)*100:.1f}%")

# 对比普通板
n_ret = [t["return_pct"] for t in normal]
print(f"\n=== 普通板对比 ===")
print(f"笔数={len(normal)} WR={sum(1 for r in n_ret if r>0)/len(n_ret)*100:.1f}% "
      f"avg={np.mean(n_ret):.2f}% med={np.median(n_ret):.2f}%")

# 样本
print(f"\n--- 一字板最近 15 笔 ---")
for t in yizi[-15:]:
    ep = t.get("exits_pct", {})
    print(f"  {t['date']} {t['code']} {t['name']} streak={t['streak']} "
          f"ret={t['return_pct']:.2f}% trigger={t['trigger']} "
          f"gap={ep.get('gap_t1','?'):.2f} trail={ep.get('trail_t2','?'):.2f}")

print(f"\n--- 一字板最佳 10 笔 ---")
best_yizi = sorted(yizi, key=lambda t: t["return_pct"], reverse=True)[:10]
for t in best_yizi:
    ep = t.get("exits_pct", {})
    print(f"  {t['date']} {t['code']} {t['name']} streak={t['streak']} "
          f"ret={t['return_pct']:.2f}% trigger={t['trigger']}")
