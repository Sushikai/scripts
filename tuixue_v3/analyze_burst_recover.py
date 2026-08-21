"""analyze_burst_recover.py — 炸板回封行为分析

炸板回封: T日 open≈涨停价 AND low<<涨停价 AND close≈涨停价
开盘即封→盘中炸开→尾盘回封（洗盘后真金白银回封）
与一字板区别: 一字板 low≈涨停价，炸板回封 low 显著低于涨停价
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from collections import defaultdict
from tuixue_v3 import zt_backtest as zb, zt_config as cfg

# 获取原始数据
daily_cache, dates, all_stocks, zt_cache = zb.build_zt_cache(
    cfg.ZT_START, cfg.ZT_OPTIMIZE_WINDOW_END, board_filter="all"
)

date_set = set(dates)

# 分类: 每只涨停股，读取 T 日 OHLC，判断类型
yiziban_list = []
burst_recover_list = []
normal_list = []

for date_str, zt_list in sorted(zt_cache.items()):
    for zt in zt_list:
        code = zt["code"]
        df = daily_cache.get(code)
        if df is None:
            continue
        idx_list = df.index[df["日期"] == date_str].tolist()
        if not idx_list:
            continue
        ti = idx_list[0]
        row = df.iloc[ti]
        limit_price = float(row["收盘"])
        open_p = float(row["开盘"])
        low_p = float(row["最低"])

        if limit_price <= 0:
            continue

        is_yiziban = open_p >= limit_price * 0.995
        # 炸板回封: open >= 0.98 * limit AND low <= 0.97 * limit
        # (开盘接近涨停，盘中炸开3%+，收盘回封)
        is_burst_recover = (
            not is_yiziban and
            open_p >= limit_price * 0.98 and
            low_p <= limit_price * 0.97
        )

        entry = {
            "code": code, "name": zt.get("name", code),
            "date": date_str, "streak": zt.get("streak", 0),
            "limit_price": limit_price, "open_p": open_p, "low_p": low_p,
        }

        if is_yiziban:
            yiziban_list.append(entry)
        elif is_burst_recover:
            burst_recover_list.append(entry)
        else:
            normal_list.append(entry)

print(f"一字板: {len(yiziban_list)}")
print(f"炸板回封: {len(burst_recover_list)}")
print(f"普通涨停: {len(normal_list)}")

# 炸板回封样本
if burst_recover_list:
    print(f"\n=== 炸板回封样本 (前30) ===")
    for c in burst_recover_list[:30]:
        open_vs_limit = (c["open_p"] / c["limit_price"] - 1) * 100
        low_vs_limit = (c["low_p"] / c["limit_price"] - 1) * 100
        print(f"  {c['date']} {c['code']} {c['name']:6s} streak={c['streak']} "
              f"open={c['open_p']:.2f} low={c['low_p']:.2f} limit={c['limit_price']:.2f} "
              f"open%={open_vs_limit:.1f}% low%={low_vs_limit:.1f}%")

    print(f"\n--- 炸板回封 连板分布 ---")
    by_streak = defaultdict(list)
    for c in burst_recover_list:
        by_streak[c["streak"]].append(c)
    for s in sorted(by_streak):
        print(f"  {s}连板: {len(by_streak[s])}只")

# ── T+1 开盘买入 + 简单退出模拟 ──
def simulate_open_t1_entry(candidates, label, trail_activate=0.5, trail_pullback=2.0, stop_loss=-3.0):
    """T+1 开盘买入，T+2 trail stop 退出"""
    returns = []
    details = []
    for c in candidates:
        code, date_str = c["code"], c["date"]
        df = daily_cache.get(code)
        if df is None:
            continue

        # 找 T+1 (buy day)
        idx_list = df.index[df["日期"] > date_str].tolist()
        if len(idx_list) < 2:
            continue
        t1_idx = idx_list[0]
        t2_idx = idx_list[1]
        t1_row = df.iloc[t1_idx]
        t2_row = df.iloc[t2_idx]

        buy_price = float(t1_row["开盘"])
        if buy_price <= 0:
            continue

        t2_high = float(t2_row["最高"])
        t2_low = float(t2_row["最低"])
        t2_close = float(t2_row["收盘"])

        # Trail stop 模拟
        trail_active_price = buy_price * (1 + trail_activate / 100)
        exit_price = t2_close
        trigger = "close_t2"

        if t2_low <= buy_price * (1 + stop_loss / 100):
            exit_price = buy_price * (1 + stop_loss / 100)
            trigger = "stop_loss"
        elif t2_high >= trail_active_price:
            # 触发 trail: 从高点回撤 pullback%
            exit_price = max(t2_high * (1 - trail_pullback / 100), buy_price)
            trigger = "trail_t2"
        elif t2_high >= buy_price * 1.003:  # gap at open
            # 开盘跳空: 以 T+2 最高价卖出
            exit_price = t2_high
            trigger = "gap_high"

        ret = (exit_price / buy_price - 1) * 100
        returns.append(ret)
        details.append({**c, "return_pct": ret, "trigger": trigger,
                        "buy_price": buy_price, "exit_price": exit_price,
                        "buy_date": df.iloc[t1_idx]["日期"]})

    if not returns:
        print(f"\n{label}: 0笔 (无有效交易)")
        return returns, details

    print(f"\n=== {label}: {len(returns)}笔 ===")
    print(f"  WR={sum(1 for r in returns if r>0)/len(returns)*100:.1f}% "
          f"avg={np.mean(returns):.2f}% med={np.median(returns):.2f}% "
          f"std={np.std(returns):.2f}%")
    print(f"  P10={np.percentile(returns,10):.2f} P25={np.percentile(returns,25):.2f} "
          f"P75={np.percentile(returns,75):.2f} P90={np.percentile(returns,90):.2f}")
    print(f"  max={max(returns):.2f} min={min(returns):.2f}")
    print(f"  总收益={sum(returns):.1f}% 月均={sum(returns)/len(returns)*(len(returns)/5):.1f}%")

    # 按退出方式
    by_exit = defaultdict(list)
    for d in details:
        by_exit[d["trigger"]].append(d["return_pct"])
    for k, v in sorted(by_exit.items()):
        print(f"    {k}: {len(v)}笔 avg={np.mean(v):.2f}% wr={sum(1 for r in v if r>0)/len(v)*100:.1f}%")

    # 按连板
    by_streak = defaultdict(list)
    for d in details:
        by_streak[d["streak"]].append(d["return_pct"])
    print(f"  --- 按连板 ---")
    for s in sorted(by_streak):
        v = by_streak[s]
        print(f"    {s}连板: {len(v)}笔 avg={np.mean(v):.2f}% med={np.median(v):.2f}% wr={sum(1 for r in v if r>0)/len(v)*100:.1f}%")

    return returns, details

# 跑三类对比
print(f"\n{'='*60}")
print("T+1 开盘买入 + Trail Stop (激活0.5%, 回撤2.0%, 止损-3%)")
print(f"{'='*60}")

br_ret, br_detail = simulate_open_t1_entry(burst_recover_list, "炸板回封")
yz_ret, yz_detail = simulate_open_t1_entry(yiziban_list, "一字板")
nm_ret, nm_detail = simulate_open_t1_entry(normal_list, "普通涨停")

# 炸板回封最佳/最差样本
if br_detail:
    br_detail.sort(key=lambda x: x["return_pct"], reverse=True)
    print(f"\n--- 炸板回封 最佳 15 笔 ---")
    for d in br_detail[:15]:
        print(f"  {d['buy_date']} {d['code']} {d['name']:6s} streak={d['streak']} "
              f"ret={d['return_pct']:.2f}% trigger={d['trigger']}")
    print(f"\n--- 炸板回封 最差 15 笔 ---")
    for d in br_detail[-15:]:
        print(f"  {d['buy_date']} {d['code']} {d['name']:6s} streak={d['streak']} "
              f"ret={d['return_pct']:.2f}% trigger={d['trigger']}")
