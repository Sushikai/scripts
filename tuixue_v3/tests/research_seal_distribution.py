"""
研究脚本：用 push2ex 数据建立 封板时间 vs OHLC特征 的统计模型
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict, Counter
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))


def get_trading_dates():
    """获取最近的可用的交易日（push2ex 只覆盖 ~15 天）"""
    return ["20260724", "20260723", "20260722", "20260721",
            "20260720", "20260717", "20260716", "20260715",
            "20260714", "20260713", "20260710", "20260709",
            "20260708", "20260707", "20260706"]


def fetch_push2ex_full(date_str: str) -> list[dict]:
    """获取单日全量涨停数据"""
    url = "https://push2ex.eastmoney.com/getTopicZTPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": "0",
        "pagesize": "500",
        "sort": "fbt:asc",
        "date": date_str,
    }
    r = requests.get(url, params=params, timeout=15)
    data = r.json()
    pool = data.get("data")
    if not pool or not isinstance(pool, dict):
        return []

    results = []
    for item in pool.get("pool", []):
        fbt = item.get("fbt", 0)  # 首次封板时间 (e.g. 92500 = 09:25:00)
        lbt = item.get("lbt", 0)   # 最后封板时间
        zbc = item.get("zbc", 0)   # 炸板次数
        fund = item.get("fund", 0)  # 封板资金
        lbc = item.get("lbc", 0)   # 连板数

        # Convert fbt to HHMM int string
        fbt_str = str(fbt).zfill(6)

        results.append({
            "code": str(item.get("c", "")).zfill(6),
            "name": item.get("n", ""),
            "price": item.get("p", 0) / 1000,  # 涨停价
            "pct": item.get("zdp", 0),
            "amount": item.get("amount", 0),
            "ltsz": item.get("ltsz", 0),       # 流通市值
            "tshare": item.get("tshare", 0),   # 总市值
            "hs": item.get("hs", 0),           # 换手率
            "fbt": fbt_str,                     # 首次封板时间
            "lbt": str(lbt).zfill(6),          # 最后封板时间
            "zbc": zbc,                         # 炸板次数
            "fund": fund,                       # 封板资金
            "lbc": lbc,                         # 连板数
            "sector": item.get("hybk", ""),
            "zttj_days": item.get("zttj", {}).get("days", 0),
        })
    return results


def fetch_ohlc(code: str, date_str: str) -> dict | None:
    """获取单日 OHLC"""
    from tuixue_v3.data_layer import fetch_stock_daily
    try:
        df = fetch_stock_daily(code)
        if df is None or df.empty:
            return None
        row = df[df["日期"] == date_str]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            "open": float(r["开盘"]),
            "high": float(r["最高"]),
            "low": float(r["最低"]),
            "close": float(r["收盘"]),
            "volume": float(r.get("成交量", 0) or 0),
            "amount_d": float(r.get("成交额", 0) or 0),
        }
    except Exception:
        return None


def classify_seal_time(fbt: str) -> str:
    """将封板时间分类"""
    if not fbt or fbt == "0" or len(fbt) < 4:
        return "unknown"
    hour_min = int(fbt[:4])

    if hour_min <= 925:
        return "open_auction"  # 09:25 集合竞价封板（一字板）
    elif hour_min <= 1000:
        return "early"         # 09:25-10:00 早盘封板
    elif hour_min <= 1130:
        return "mid_morning"   # 10:00-11:30 上午封板
    elif hour_min <= 1400:
        return "afternoon"     # 13:00-14:00 下午封板
    elif hour_min <= 1457:
        return "late"          # 14:00-14:57 尾盘封板
    else:
        return "close_auction" # 14:57+ 收盘集合竞价


def analyze_distribution(all_data: dict[str, list[dict]]):
    """分析封板时间分布"""
    total = 0
    category_counts = Counter()
    category_examples = defaultdict(list)

    for date_str, entries in all_data.items():
        for e in entries:
            cat = classify_seal_time(e["fbt"])
            category_counts[cat] += 1
            total += 1
            if len(category_examples[cat]) < 3:
                category_examples[cat].append(f'{e["code"]} {e["name"]} fbt={e["fbt"]} pct={e["pct"]:.1f}% hs={e["hs"]:.1f}% fund={e["fund"]/1e8:.2f}亿 zbc={e["zbc"]}')

    print(f"\n=== 封板时间分布 (共 {total} 条) ===")
    for cat in ["open_auction", "early", "mid_morning", "afternoon", "late", "close_auction"]:
        cnt = category_counts.get(cat, 0)
        if total > 0:
            print(f"  {cat}: {cnt} ({cnt/total*100:.1f}%)")
        for ex in category_examples[cat]:
            print(f"    e.g. {ex}")


def analyze_fill_feasibility(all_data: dict[str, list[dict]]):
    """分析不同封板时间类别的成交可行性"""
    print(f"\n=== 成交可行性分析 ===")
    print("""
收盘集合竞价 (14:57-15:00) 买入可行性:
  open_auction (09:25封):  一字板，全天封死 → 几乎不可成交 (0-5%)
  early (09:25-10:00封):   早盘封板 → 卖盘极少 → 很难成交 (5-20%)
  mid_morning (10:00-11:30): 上午封板 → 有交易量 → 可能成交 (20-50%)
  afternoon (13:00-14:00):  下午封板 → 更多卖盘 → 较易成交 (30-60%)
  late (14:00-14:57):       尾盘封板 → 卖盘充足 → 容易成交 (50-80%)
  close_auction (14:57+):   集合竞价封板 → 基本可成交 (80-100%)
""")


def analyze_ohlc_patterns(all_data: dict[str, list[dict]]):
    """获取部分样本的 OHLC 数据，分析封板类别与 OHLC 特征的关系"""
    print(f"\n=== OHLC 特征 vs 封板类别 ===")

    # 取每个类别的样本
    samples_by_cat = defaultdict(list)
    for date_str, entries in all_data.items():
        for e in entries:
            cat = classify_seal_time(e["fbt"])
            if len(samples_by_cat[cat]) < 10:
                samples_by_cat[cat].append((date_str, e))

    # 对每个样本获取 OHLC
    for cat in ["open_auction", "early", "mid_morning", "afternoon", "late"]:
        samples = samples_by_cat[cat]
        if not samples:
            continue

        print(f"\n--- {cat} ({len(samples)} samples) ---")
        ratios_open = []
        ratios_high = []
        turnovers = []

        for date_str, e in samples:
            ohlc = fetch_ohlc(e["code"], date_str)
            if ohlc is None:
                continue
            open_p = ohlc["open"]
            high_p = ohlc["high"]
            close_p = ohlc["close"]
            price = e["price"]  # this is the limit-up price (= close price for sealed stocks)

            if price > 0:
                ratios_open.append(open_p / price)
            if price > 0 and high_p > 0:
                ratios_high.append(high_p / price)
            turnovers.append(e["hs"])

            if len(ratios_open) <= 5:
                print(f"  {e['code']} {e['name']}: open={open_p:.2f} limit={price:.2f} "
                      f"ratio={open_p/price:.3f} hs={e['hs']:.1f}% zbc={e['zbc']}")

        if ratios_open:
            print(f"  open/limit: mean={np.mean(ratios_open):.3f} "
                  f"min={np.min(ratios_open):.3f} max={np.max(ratios_open):.3f}")
        if turnovers:
            print(f"  turnover: mean={np.mean(turnovers):.1f}% "
                  f"min={np.min(turnovers):.1f}% max={np.max(turnovers):.1f}%")


def main():
    # 获取最近交易日的全量 push2ex 数据
    dates = get_trading_dates()
    print(f"Fetching push2ex data for dates: {dates}")

    all_data = {}
    for d in dates:
        entries = fetch_push2ex_full(d)
        if entries:
            all_data[d] = entries
            print(f"  {d}: {len(entries)} stocks")
        else:
            print(f"  {d}: NO DATA (will skip)")
        time.sleep(0.3)

    if not all_data:
        print("No push2ex data available!")
        return

    analyze_distribution(all_data)
    analyze_fill_feasibility(all_data)

    # OHLC 分析只需要小样本
    print("\nFetching OHLC samples for pattern analysis...")
    analyze_ohlc_patterns(all_data)

    # 保存数据供后续使用
    with open("/tmp/zt_seal_time_research.json", "w") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nSaved {sum(len(v) for v in all_data.values())} records to /tmp/zt_seal_time_research.json")


if __name__ == "__main__":
    main()
