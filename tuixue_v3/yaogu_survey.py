"""
yaogu_survey.py — 妖股调研 (500 轮调研 · 阶段 1: 历史样本采集 + 参数特征统计)

调研问题:
1. 历史妖股样本长什么样? (连板段分布 / 涨幅分布)
2. 妖股启动时参数特征? (价格 / 换手率 / 成交额)
3. 不同阶段介入的胜率? (2板 / 3板 / 4板 / 断板低吸)
4. 妖股期间换手结构? (换手板 vs 一字板)
5. "断板即走"是否成立? (断板后 1/2/5 日收益)

数据源: cache_db SQLite daily (2020-01-01 之后)
口径:
- 涨停: 主板 ≥9.5%, 创业板/科创板 ≥19.5%
- 连板段: 连续涨停日, 段内无中断
- 真实路径: T+1 开盘买 + 一字板空仓 + 0.66% 双边成本 (与 zt_backtest 同口径)

调研结论 (写入 YAOGU_500_SURVEY.md §6b):
- 2板介入期望最优 (胜率 42%, avg +0.52%, 空仓率 42%)
- 断板低吸负期望 (胜率 34%, avg -2.91%) — 页面排除
"""
from __future__ import annotations

import json
import statistics
import sys
import time as systime
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, "/Users/kaikai/scripts")

from tuixue_v3 import cache_db

START = "20200101"
END = "20260807"


def is_limit_up(code: str, pct: float) -> bool:
    if code.startswith(("300", "301", "688", "689")):
        return pct >= 19.5
    return pct >= 9.5


def load_daily() -> dict[str, pd.DataFrame]:
    t0 = systime.time()
    conn = cache_db.get_conn()
    rows = conn.execute(
        "SELECT code, date, open, high, low, close, volume, amount, turnover "
        "FROM daily WHERE date >= ? AND date <= ? ORDER BY code, date",
        (START, END),
    ).fetchall()
    groups: dict[str, list] = defaultdict(list)
    for code, d, o, h, l, c, v, a, t in rows:
        groups[code].append({
            "日期": d, "开盘": float(o or 0), "最高": float(h or 0), "最低": float(l or 0),
            "收盘": float(c or 0), "成交量": float(v or 0), "成交额": float(a or 0),
            "换手率": float(t or 0),
        })
    out = {}
    for code, recs in groups.items():
        df = pd.DataFrame(recs)
        df["涨跌幅"] = (df["收盘"].pct_change() * 100).round(2)
        df["涨停"] = df.apply(lambda r: is_limit_up(code, r["涨跌幅"]), axis=1)
        # 一字板: 开盘==最高 (容差 0.3%)
        df["一字"] = (df["开盘"] > 0) & (abs(df["开盘"] - df["最高"]) <= df["最高"] * 0.003)
        out[code] = df
    print(f"  load daily: {len(out)} stocks, {sum(len(v) for v in out.values()):,} rows ({systime.time()-t0:.0f}s)", flush=True)
    return out


def extract_streaks(df: pd.DataFrame) -> list[dict]:
    """提取连板段 (streak>=1), 返回段信息。"""
    streaks = []
    run_start = None
    for i, row in df.iterrows():
        if i == 0:
            if bool(row["涨停"]):
                run_start = i
            continue
        if bool(row["涨停"]) and run_start is not None:
            continue  # 延续
        if bool(row["涨停"]):
            run_start = i
        else:
            if run_start is not None and i - 1 >= run_start:
                streaks.append(_make_streak(df, run_start, i - 1))
                run_start = None
    if run_start is not None and len(df) - 1 >= run_start:
        streaks.append(_make_streak(df, run_start, len(df) - 1))
    return streaks


def _make_streak(df: pd.DataFrame, s: int, e: int) -> dict:
    seg = df.iloc[s : e + 1]
    pre = df.iloc[s - 1] if s - 1 >= 0 else None
    streak = int(e - s + 1)
    return {
        "code": df.attrs.get("code", ""),
        "name": df.attrs.get("name", ""),
        "start_idx": s, "end_idx": e,
        "start_date": seg.iloc[0]["日期"], "end_date": seg.iloc[-1]["日期"],
        "streak": streak,
        "days": [{
            "date": r["日期"], "open": r["开盘"], "high": r["最高"], "low": r["最低"],
            "close": r["收盘"], "vol": r["成交量"], "amount": r["成交额"], "turn": r["换手率"],
            "one_word": bool(r["一字"]),
        } for _, r in seg.iterrows()],
        "pre_close": float(pre["收盘"]) if pre is not None else 0.0,
        "pre_turn": float(pre["换手率"]) if pre is not None else 0.0,
        "pre_amount": float(pre["成交额"]) if pre is not None else 0.0,
        "wave_pct": float((seg.iloc[-1]["收盘"] / seg.iloc[0]["开盘"] - 1) * 100) if seg.iloc[0]["开盘"] else 0.0,
    }


def follow_returns(df: pd.DataFrame, end_idx: int, days: list[int]) -> dict:
    """断板后 N 日收益 (用收盘价, 裸收益)。"""
    out = {}
    base = float(df.iloc[end_idx]["收盘"])
    for n in days:
        j = end_idx + n
        if j < len(df):
            out[n] = round((float(df.iloc[j]["收盘"]) / base - 1) * 100, 2)
    return out


# ═══════════════════════════════════════════
# 真实路径介入模拟 (ZT 同口径: T+1 开盘买 + 一字板空仓 + 0.66% 成本)
# ═══════════════════════════════════════════
COST_BPS = 0.66


def sim_entry_exit(daily: dict[str, pd.DataFrame], streaks: list[dict], entry_streak: int) -> list[dict]:
    """在连板段达到 entry_streak 后, 次日开盘买入 (一字板空仓), 持有到断板收盘卖出."""
    trades = []
    for st in streaks:
        if st["streak"] < entry_streak:
            continue
        df = daily[st["code"]]
        hit_idx = st["start_idx"] + entry_streak - 1
        if hit_idx + 1 >= len(df):
            continue
        nx = df.iloc[hit_idx + 1]
        t_open, t_high = float(nx["开盘"]), float(nx["最高"])
        if t_open <= 0:
            continue
        if abs(t_open - t_high) <= t_high * 0.003:
            trades.append({"buy": 0.0, "ret": 0.0, "trigger": "one_word",
                           "date": nx["日期"], "exit": nx["日期"]})
            continue
        sell_idx = st["end_idx"] + 1
        if sell_idx >= len(df):
            continue
        sell_close = float(df.iloc[sell_idx]["收盘"])
        ret = (sell_close / t_open - 1) * 100 - COST_BPS
        trades.append({"buy": t_open, "ret": round(ret, 2), "trigger": "break",
                       "date": nx["日期"], "exit": df.iloc[sell_idx]["日期"],
                       "streak": st["streak"]})
    return trades


def sim_break_buy(daily: dict[str, pd.DataFrame], streaks: list[dict], buy_idx_offset: int) -> list[dict]:
    """断板日收盘买 (首阴低吸), 持有 buy_idx_offset 日卖出."""
    trades = []
    for st in streaks:
        if st["streak"] < 3:
            continue
        df = daily[st["code"]]
        break_idx = st["end_idx"] + 1
        if break_idx >= len(df):
            continue
        buy_close = float(df.iloc[break_idx]["收盘"])
        sell_idx = break_idx + buy_idx_offset
        if sell_idx >= len(df):
            continue
        sell_close = float(df.iloc[sell_idx]["收盘"])
        ret = (sell_close / buy_close - 1) * 100 - COST_BPS
        trades.append({"ret": round(ret, 2), "date": df.iloc[break_idx]["日期"],
                       "exit": df.iloc[sell_idx]["日期"], "streak": st["streak"]})
    return trades


def summarize(trades: list[dict], label: str) -> None:
    if not trades:
        print(f"  {label}: 无交易", flush=True)
        return
    nf = sum(1 for t in trades if t.get("trigger") == "one_word")
    live = [t for t in trades if t.get("trigger") != "one_word"]
    if not live:
        print(f"  {label}: 全部一字板空仓 ({len(trades)} 笔)", flush=True)
        return
    rets = [t["ret"] for t in live]
    wins = sum(1 for x in rets if x > 0)
    cum = sum(rets)
    worst, best = min(rets), max(rets)
    print(f"  {label}: n={len(live)} 一字板={nf} "
          f"胜率={wins/len(rets)*100:.0f}% avg={sum(rets)/len(rets):+.2f}% "
          f"累计={cum:+.1f}% worst={worst:+.2f}% best={best:+.2f}%", flush=True)
    for lo, hi in [(3, 3), (4, 4), (5, 5), (6, 99)]:
        sub = [t for t in live if lo <= t.get("streak", 99) <= hi]
        if not sub:
            continue
        r = [t["ret"] for t in sub]
        wr = sum(1 for x in r if x > 0) / len(r)
        print(f"      [{lo}-{hi}板段]: n={len(sub)} 胜率={wr*100:.0f}% avg={sum(r)/len(r):+.2f}%", flush=True)


def calc_market_env(daily: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """每日情绪环境: zt_count(涨停家数) + 晋级率 (昨日涨停今日继续涨停)."""
    zt_days: dict[str, set[str]] = defaultdict(set)
    for code, df in daily.items():
        for i, r in df.iterrows():
            if i == 0:
                continue
            if bool(r["涨停"]):
                zt_days.setdefault(r["日期"], set()).add(code)
    env: dict[str, dict] = {}
    sorted_dates = sorted(zt_days)
    for d in sorted_dates:
        env[d] = {"zt_count": len(zt_days[d])}
    for i, d in enumerate(sorted_dates):
        if i == 0:
            continue
        prev = zt_days.get(sorted_dates[i - 1], set())
        cur = zt_days.get(d, set())
        if prev:
            env[d]["promo"] = round(len(prev & cur) / len(prev) * 100, 1)
    return env


def main() -> None:
    t0 = systime.time()
    print("=== 妖股调研 · 样本采集 ===", flush=True)
    daily = load_daily()

    streaks_all: list[dict] = []
    for code, df in daily.items():
        df.attrs["code"] = code
        for st in extract_streaks(df):
            st["code"] = code
            st["follow"] = follow_returns(df, st["end_idx"], [1, 2, 3, 5, 10])
            last_day = st["days"][-1]
            vol_shares = last_day["vol"] * 100
            turn_pct = last_day["turn"]
            if turn_pct and turn_pct > 0:
                float_shares = vol_shares / (turn_pct / 100)
                st["mcap_yi"] = round(float_shares * last_day["close"] / 1e8, 1)
            else:
                st["mcap_yi"] = None
            streaks_all.append(st)
    print(f"  连板段总数: {len(streaks_all)} (含首板), 耗时 {systime.time()-t0:.0f}s", flush=True)

    # ── 7) 真实路径介入模拟 (核心: 妖股回测机制原型) ──
    print("\n=== 7) 真实路径介入模拟 (T+1开盘买 + 一字板空仓 + 0.66%成本) ===", flush=True)
    for es in [2, 3, 4]:
        trades = sim_entry_exit(daily, streaks_all, es)
        summarize(trades, f"连板{es}后次日开盘买 → 断板收盘卖")
    trades_break = sim_break_buy(daily, streaks_all, 1)
    summarize(trades_break, "断板日收盘买(首阴低吸) → 次日收盘卖")

    # ── 8) 情绪环境 ──
    print("\n=== 8) 情绪环境分布 (2020-01 ~ 2026-08) ===", flush=True)
    env = calc_market_env(daily)
    zt_counts = [v["zt_count"] for v in env.values()]
    promos = [v["promo"] for v in env.values() if "promo" in v]
    print(f"  涨停家数: 中位={statistics.median(zt_counts):.0f} P25={sorted(zt_counts)[len(zt_counts)//4]} "
          f"P75={sorted(zt_counts)[3*len(zt_counts)//4]} 交易日={len(zt_counts)}", flush=True)
    print(f"  晋级率: 中位={statistics.median(promos):.1f}% P25={sorted(promos)[len(promos)//4]}% "
          f"P75={sorted(promos)[3*len(promos)//4]}%", flush=True)

    # ── 1) 连板段分布 ──
    dist: dict[int, int] = defaultdict(int)
    for st in streaks_all:
        dist[st["streak"]] += 1
    print("\n=== 1) 连板段分布 (2020-01 ~ 2026-08) ===")
    for k in sorted(dist):
        print(f"  {k}板: {dist[k]} 段")

    yaogu_cand = [st for st in streaks_all if st["streak"] >= 3 or st["wave_pct"] >= 50]
    print(f"\n  妖股候选 (>=3板 或 段涨幅>=50%): {len(yaogu_cand)} 段, "
          f"涉及 {len({st['code'] for st in yaogu_cand})} 只股票")

    # ── 2) 启动特征 ──
    print("\n=== 2) 妖股启动日特征 (段首前一日) ===")
    pres = [st for st in yaogu_cand if st["pre_close"] > 0]
    for label, key, fmt in [
        ("启动前收盘价", "pre_close", "{:.2f}"),
        ("启动前换手率%", "pre_turn", "{:.2f}"),
        ("末板流通市值(亿)", "mcap_yi", "{:.1f}"),
    ]:
        vals = [st[key] for st in pres if st.get(key) is not None]
        if vals:
            print(f"  {label}: 中位={fmt.format(statistics.median(vals))} "
                  f"P25={fmt.format(sorted(vals)[len(vals)//4])} "
                  f"P75={fmt.format(sorted(vals)[3*len(vals)//4])} "
                  f"样本={len(vals)}")

    # ── 3) 断板后收益 ──
    print("\n=== 3) 断板后收益 (裸收益%, 含一字空仓风险前) ===")
    for streak in [3, 4, 5, 6]:
        sub = [st for st in yaogu_cand if st["streak"] == streak and 5 in st["follow"]]
        if not sub:
            continue
        r5 = [st["follow"][5] for st in sub]
        wr5 = sum(1 for x in r5 if x > 0) / len(r5)
        print(f"  {streak}板 (n={len(sub)}): 断板后5日 avg={sum(r5)/len(r5):+.2f}% "
              f"中位={statistics.median(r5):+.2f}% 胜率={wr5*100:.0f}% "
              f"worst={min(r5):+.2f}% best={max(r5):+.2f}%")

    # ── 4) 换手结构 ──
    print("\n=== 4) 板内换手结构 (3板+) ===")
    sub3 = [st for st in yaogu_cand if st["streak"] >= 3]
    if sub3:
        first_turns, last_turns, one_words = [], [], 0
        for st in sub3:
            first_turns.append(st["days"][0]["turn"])
            last_turns.append(st["days"][-1]["turn"])
            one_words += sum(1 for d in st["days"] if d["one_word"])
        total_days = sum(len(st["days"]) for st in sub3)
        print(f"  首板换手中位={statistics.median(first_turns):.1f}%  末板换手中位={statistics.median(last_turns):.1f}%")
        print(f"  一字板占比: {one_words}/{total_days} = {one_words/total_days*100:.0f}%")

    # ── 5) 断板后是否续涨 ──
    print("\n=== 5) 断板后是否续涨 (2日) ===")
    for streak in [3, 4, 5]:
        sub = [st for st in yaogu_cand if st["streak"] == streak and 2 in st["follow"]]
        if not sub:
            continue
        r2 = [st["follow"][2] for st in sub]
        wr2 = sum(1 for x in r2 if x > 0) / len(r2)
        print(f"  {streak}板断板 (n={len(sub)}): 2日后收益 avg={sum(r2)/len(r2):+.2f}% 胜率={wr2*100:.0f}%")

    # ── 6) 历史大妖 TOP20 ──
    print("\n=== 6) 历史大妖 TOP20 (按段涨幅) ===")
    big = sorted(yaogu_cand, key=lambda s: s["wave_pct"], reverse=True)[:20]
    for st in big:
        f5 = st["follow"].get(5)
        print(f"  {st['code']} {st['streak']}板 {st['start_date']}~{st['end_date']} "
              f"段涨幅{st['wave_pct']:+.0f}% 断板5日{f5 if f5 is None else f'{f5:+.0f}%'}")

    out_path = Path("/tmp/yaogu_survey.json")
    out_path.write_text(json.dumps({
        "n_streaks": len(streaks_all),
        "n_yaogu_cand": len(yaogu_cand),
        "streak_dist": dict(dist),
        "top_big": [{
            "code": s["code"], "streak": s["streak"],
            "start": s["start_date"], "end": s["end_date"],
            "wave_pct": s["wave_pct"], "follow5": s["follow"].get(5),
            "mcap_yi": s["mcap_yi"],
        } for s in big],
    }, ensure_ascii=False, indent=1))
    print(f"\n  摘要 → {out_path} (总耗时 {systime.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
