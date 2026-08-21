"""
研究脚本：从 1-min K线重建封板时间，对比 push2ex 验证准确性。
运行: python -m tuixue_v3.tests.research_seal_time
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))


def fetch_push2ex_zt(date_str: str) -> list[dict]:
    """从 push2ex 获取涨停池（含封板时间 ground truth）"""
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
    if not data.get("data"):
        return []
    results = []
    for item in data["data"]["pool"]:
        code = str(item.get("c", "")).zfill(6)
        name = item.get("n", "")
        first_time = str(item.get("fbt", "")).zfill(6)
        last_time = str(item.get("lbt", "")).zfill(6)
        burst = int(item.get("zbc", 0) or 0)
        seal_amount = float(item.get("fba", 0) or 0)
        pct = float(item.get("zdp", 0) or 0)
        close = float(item.get("p", 0) or 0) / 1000
        results.append({
            "code": code, "name": name, "first_time": first_time,
            "last_time": last_time, "burst": burst,
            "seal_amount": seal_amount, "pct": pct, "close": close,
        })
    return results


def fetch_1min_kline(code: str, date_str: str) -> list[dict]:
    """从腾讯获取 1-min K线（仅返回目标日期的 candles）"""
    prefix = code[:3]
    if prefix.startswith(("6", "9")):
        mkt = "sh"
    else:
        mkt = "sz"
    url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={mkt}{code},m1,,{date_str},320"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        all_klines = data.get("data", {}).get(f"{mkt}{code}", {}).get("m1", [])
        if not all_klines:
            return []
        results = []
        for k in all_klines:
            t = str(k[0])
            if not t.startswith(date_str):
                continue
            results.append({
                "time": t[8:12],  # HHMM only
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]),
            })
        return results
    except Exception as e:
        print(f"  fetch_1min {code} {date_str} FAIL: {e}")
        return []


def detect_seal_from_1min(klines: list[dict], limit_price: float) -> dict:
    """
    从 1-min K线检测封板行为。
    K线时间范围 09:30-15:00，09:25 开盘集合竞价结果体现在 09:30 首根 K 线。
    封板阈值：close 在涨停价 0.3% 以内。
    """
    if not klines or limit_price <= 0:
        return {"first_time": "", "last_time": "", "burst_count": 0,
                "sealed_duration_min": 0, "sealed_before_1430": False,
                "sealed_at_open": False}

    first_time = ""
    last_time = ""
    burst_count = 0
    sealed_duration = 0
    was_sealed = False
    sealed_before_1430 = False
    sealed_at_open = False

    for k in klines:
        t = k["time"]
        close = k["close"]
        is_sealed = (close >= limit_price * 0.997) if limit_price > 0 else False

        if is_sealed:
            if not first_time:
                first_time = t
                if t == "0930":
                    sealed_at_open = True
                    first_time = "092500"  # 开盘集合竞价即封板
                try:
                    hh, mm = int(t[:2]), int(t[2:4])
                    if hh < 14 or (hh == 14 and mm < 30):
                        sealed_before_1430 = True
                except Exception:
                    pass
            last_time = t
            sealed_duration += 1
            if not was_sealed and first_time and first_time != "092500" and t != first_time:
                burst_count += 1
            was_sealed = True
        else:
            if was_sealed:
                burst_count += 1
            was_sealed = False

    return {
        "first_time": first_time,
        "last_time": last_time,
        "burst_count": burst_count,
        "sealed_duration_min": sealed_duration,
        "sealed_before_1430": sealed_before_1430,
        "sealed_at_open": sealed_at_open,
    }


def compare(date_str: str):
    """对比 push2ex ground truth vs 1-min 重建"""
    print(f"\n{'='*60}")
    print(f"验证日期: {date_str}")
    print(f"{'='*60}")

    truth = fetch_push2ex_zt(date_str)
    if not truth:
        print("push2ex 返回空，跳过")
        return

    truth_map = {t["code"]: t for t in truth}
    print(f"push2ex 涨停数: {len(truth)}")

    # 统计
    matches_first = 0
    matches_burst = 0
    total_compared = 0
    time_diffs = []
    early_seal_count = 0  # 14:30 前封板
    open_seal_count = 0   # 开盘即封板（一字板）
    detected_results = {}  # code -> detected

    for t in truth:
        code = t["code"]
        limit_price = t["close"]  # push2ex 的"最新价"即涨停价
        klines = fetch_1min_kline(code, date_str)
        if not klines:
            continue

        detected = detect_seal_from_1min(klines, limit_price)
        detected_results[code] = detected
        total_compared += 1

        # 比较首次封板时间
        truth_ft = t["first_time"]
        detected_ft = detected["first_time"]
        if truth_ft and detected_ft:
            try:
                t_mins = int(truth_ft[:2]) * 60 + int(truth_ft[2:4])
                d_mins = int(detected_ft[:2]) * 60 + int(detected_ft[2:4])
                diff = abs(t_mins - d_mins)
                time_diffs.append(diff)
                if diff <= 2:  # 2 分钟以内视为匹配
                    matches_first += 1
            except Exception:
                pass

        # 比较炸板次数
        if abs(detected["burst_count"] - t["burst"]) <= 1:
            matches_burst += 1

        if detected["sealed_before_1430"]:
            early_seal_count += 1
        if detected["sealed_at_open"]:
            open_seal_count += 1

        # 打印前 15 个对比
        if total_compared <= 15:
            at_open = " [开盘封]" if detected["sealed_at_open"] else ""
            print(f"  {code} {t['name']}: truth_ft={truth_ft} detected_ft={detected_ft} "
                  f"truth_burst={t['burst']} detected_burst={detected['burst_count']} "
                  f"seal_dur={detected['sealed_duration_min']}min{at_open}")

    print(f"\n--- 汇总 ---")
    print(f"对比样本: {total_compared}")
    print(f"首次封板时间匹配率 (±2min): {matches_first}/{total_compared} = "
          f"{matches_first/total_compared*100:.1f}%" if total_compared else "N/A")
    if time_diffs:
        print(f"时间差分布: avg={sum(time_diffs)/len(time_diffs):.1f}min "
              f"median={sorted(time_diffs)[len(time_diffs)//2]}min "
              f"max={max(time_diffs)}min")
    print(f"炸板次数匹配率 (±1): {matches_burst}/{total_compared}")
    print(f"开盘即封板 (一字板): {open_seal_count}/{total_compared} = "
          f"{open_seal_count/total_compared*100:.1f}%" if total_compared else "N/A")
    print(f"14:30 前首次封板: {early_seal_count}/{total_compared} = "
          f"{early_seal_count/total_compared*100:.1f}%" if total_compared else "N/A")

    # 封板时间分布
    ft_dist = defaultdict(int)
    for t in truth:
        ft = t["first_time"]
        if ft:
            hour = ft[:2]
            ft_dist[hour] += 1
    print(f"\n封板时间分布 (push2ex ground truth):")
    for h in sorted(ft_dist):
        print(f"  {h}:00-{h}:59: {ft_dist[h]} 只")

    return truth, time_diffs


if __name__ == "__main__":
    # 验证最近 3 个交易日
    for d in ["20260724", "20260723", "20260722"]:
        try:
            compare(d)
        except Exception as e:
            print(f"{d} 验证失败: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(0.5)
