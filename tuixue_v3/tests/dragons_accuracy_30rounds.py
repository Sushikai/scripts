"""
30 轮 龙头页面 数据准确性分析 — /api/dragons vs 东财涨停池真值.

每轮:
  1. 抓 /api/dragons (前几轮用 cache, 中段强制 refresh=1)
  2. 抽样 N 行 (前 5 / 中 5 / 尾 5)
  3. 对每行跟东财涨停池真值 (fetch_zt_pool + fetch_spot_a_full) 逐字段比对
  4. 汇总本轮偏差, 写入 round_NN.json

最后生成 summary.md, 列偏差热力 + 漏算/重复 code.
"""
import json
import sys
import time
from pathlib import Path
from collections import Counter, defaultdict

OUT_DIR = Path("/tmp/dragons_30rounds")
OUT_DIR.mkdir(exist_ok=True)

API = "http://100.104.113.66:7799/api/dragons"
ROUNDS = 30
SAMPLE_PER_ROUND = 12  # 头 4 / 中 4 / 尾 4

def fetch_api(refresh: bool) -> dict:
    import urllib.request
    url = API + ("?refresh=1" if refresh else "")
    with urllib.request.urlopen(url, timeout=95) as r:
        return json.loads(r.read())

def fetch_truth(date_str: str) -> tuple[dict, dict]:
    """返回 (zt_pool_by_code, spot_by_code) — 用今天日期"""
    import os
    sys.path.insert(0, "/Users/kaikai/scripts/tuixue_v3")
    from multi_source_fetchers import fetch_zt_pool, fetch_spot_a_full
    zt_pool = fetch_zt_pool(date_str) or []
    zt_by_code = {z["code"]: z for z in zt_pool if z.get("code")}
    spot = fetch_spot_a_full(8) or {}
    return zt_by_code, spot

def compare_one(api_row: dict, zt_truth: dict, spot_truth: dict) -> dict:
    """逐字段比对, 返回 deviations dict"""
    code = api_row.get("code")
    issues = []
    truth_zt = zt_truth.get(code, {})
    truth_spot = spot_truth.get(code, {})

    # 1) 名称
    if truth_zt and api_row.get("name") != truth_zt.get("name"):
        issues.append(f"name api='{api_row.get('name')}' truth='{truth_zt.get('name')}'")

    # 2) 连板 streak
    if truth_zt:
        truth_streak = int(truth_zt.get("streak", 1) or 1)
        if api_row.get("streak") != truth_streak:
            issues.append(f"streak api={api_row.get('streak')} truth={truth_streak}")

    # 3) 板块 sector
    if truth_zt:
        truth_sector = truth_zt.get("sector", "")
        if api_row.get("sector") != truth_sector:
            issues.append(f"sector api='{api_row.get('sector')}' truth='{truth_sector}'")

    # 4) 封成比 seal_ratio_pct
    if truth_zt:
        truth_loa = float(truth_zt.get("limit_order_amount", 0) or 0)
        truth_amt = float(truth_zt.get("amount", 0) or 0)
        truth_seal = round(truth_loa / truth_amt * 100, 1) if truth_amt > 0 else None
        api_seal = api_row.get("seal_ratio_pct")
        # 允许 ±1% 抖动
        if truth_seal is None and api_seal is not None:
            issues.append(f"seal api={api_seal} truth=None")
        elif truth_seal is not None and api_seal is None:
            issues.append(f"seal api=None truth={truth_seal}")
        elif truth_seal is not None and api_seal is not None and abs(truth_seal - api_seal) > 1.0:
            issues.append(f"seal api={api_seal} truth={truth_seal} Δ={api_seal-truth_seal:.1f}")

    # 5) 今日涨幅 change_pct — 跟 spot 真值对比
    api_pct = api_row.get("change_pct")
    spot_pct_raw = truth_spot.get("涨跌幅")
    spot_pct = float(spot_pct_raw) if spot_pct_raw is not None and spot_pct_raw != "-" else None
    if api_pct is None and spot_pct is not None:
        issues.append(f"change_pct api=None spot={spot_pct}")
    elif api_pct is not None and spot_pct is not None and abs(api_pct - spot_pct) > 0.05:
        issues.append(f"change_pct api={api_pct} spot={spot_pct} Δ={api_pct-spot_pct:+.2f}")

    # 6) 市盈 pe_ttm
    api_pe = api_row.get("pe_ttm")
    spot_pe_raw = truth_spot.get("市盈率")
    spot_pe = float(spot_pe_raw) if spot_pe_raw is not None and spot_pe_raw != "-" else None
    if api_pe is None and spot_pe is not None:
        issues.append(f"pe api=None spot={spot_pe}")
    elif api_pe is not None and spot_pe is not None and abs(api_pe - spot_pe) > 0.5:
        issues.append(f"pe api={api_pe} spot={spot_pe} Δ={api_pe-spot_pe:+.1f}")

    # 7) sector_zt_count — 跟 zt_pool 全量自验
    api_sec_zt = api_row.get("sector_zt_count")
    if truth_zt and api_row.get("sector"):
        sec = api_row.get("sector")
        truth_sec_zt = sum(1 for z in zt_truth.values() if z.get("sector") == sec)
        if api_sec_zt != truth_sec_zt:
            issues.append(f"sector_zt_count api={api_sec_zt} truth={truth_sec_zt}")

    return {"code": code, "issues": issues}

def main():
    date_str = time.strftime("%Y%m%d")
    print(f"[run] date={date_str} rounds={ROUNDS}")

    # 真值抓一次 (30 轮内不变)
    print("[truth] 抓东财涨停池真值 + spot_a_full …")
    t0 = time.time()
    zt_truth, spot_truth = fetch_truth(date_str)
    print(f"[truth] zt_pool={len(zt_truth)} spot={len(spot_truth)} 耗时 {time.time()-t0:.1f}s")

    # 漏算/重复: api rows - truth codes
    api_codes_seen = Counter()
    issues_by_field = Counter()
    all_issues = []
    rounds_summary = []

    for round_idx in range(1, ROUNDS + 1):
        t0 = time.time()
        # 前 10 轮用 cache, 11-20 轮 refresh=1, 21-30 轮混
        refresh = (round_idx > 10 and round_idx <= 20) or (round_idx % 7 == 0)
        try:
            api_data = fetch_api(refresh).get("data", {})
        except Exception as e:
            print(f"  round {round_idx:02d}: API ERR {e}")
            rounds_summary.append({"round": round_idx, "err": str(e), "elapsed": round(time.time()-t0, 1)})
            time.sleep(5)
            continue
        elapsed = round(time.time() - t0, 1)
        all_rows = api_data.get("all") or []
        yest_rows = api_data.get("yesterday_all") or []
        print(f"  round {round_idx:02d}: refresh={int(refresh)} all={len(all_rows)} yest={len(yest_rows)} {elapsed}s")

        # 抽样 — 头 4 / 中 4 / 尾 4
        n = len(all_rows)
        if n == 0:
            rounds_summary.append({"round": round_idx, "n": 0, "elapsed": elapsed})
            time.sleep(2)
            continue
        sample_idx = list(range(min(4, n))) + \
                     list(range(max(0, n//2 - 2), min(n, n//2 + 2))) + \
                     list(range(max(0, n - 4), n))
        sample_idx = sorted(set(sample_idx))[:SAMPLE_PER_ROUND]

        round_issues = 0
        for i in sample_idx:
            api_codes_seen[all_rows[i].get("code")] += 1
            cmp = compare_one(all_rows[i], zt_truth, spot_truth)
            if cmp["issues"]:
                round_issues += len(cmp["issues"])
                all_issues.append({"round": round_idx, **cmp})
                for iss in cmp["issues"]:
                    field = iss.split()[0].rstrip(":")
                    issues_by_field[field] += 1

        rounds_summary.append({
            "round": round_idx,
            "refresh": int(refresh),
            "elapsed": elapsed,
            "all_count": len(all_rows),
            "yest_count": len(yest_rows),
            "sampled": len(sample_idx),
            "issues": round_issues,
        })

        # 漏算: 涨停池有但 /api/dragons 全量 all 缺
        api_all_codes = {r.get("code") for r in all_rows}
        missing = [c for c in zt_truth if c not in api_all_codes]
        if missing:
            issues_by_field["missing_code"] += len(missing)
            all_issues.append({"round": round_idx, "code": "MISSING", "issues": [f"missing {len(missing)} codes: {missing[:5]}"]})

        # 重复
        dup = [c for c, cnt in api_codes_seen.items() if cnt > 1]
        # dup is across rounds, skip per-round reporting

        time.sleep(2 if not refresh else 4)

    # summary
    summary = {
        "date": date_str,
        "rounds": rounds_summary,
        "issues_by_field": dict(issues_by_field),
        "total_issues": sum(s.get("issues", 0) for s in rounds_summary),
        "all_issues": all_issues[:100],  # 截断
    }

    out_json = OUT_DIR / "summary.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n[done] 30 轮完成, 写入 {out_json}")
    print(f"[stats] 偏差按字段: {dict(issues_by_field)}")
    print(f"[stats] 总偏差条数: {sum(s.get('issues',0) for s in rounds_summary)}")

if __name__ == "__main__":
    main()
