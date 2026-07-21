#!/usr/bin/env python3
"""全 A 风向页 提速基准 (BEFORE / AFTER 对照)
────────────────────────────────────────────────
用法:
  python3 -m tuixue_v3.web.tests.bench_all_stocks --label before
  python3 -m tuixue_v3.web.tests.bench_all_stocks --label after

指标:
  1. 覆盖率   — board 返回的 total_available (真全 A 应 ≥ 5000)
  2. 冷延迟   — 清缓存后首次 board (ms)
  3. 热延迟   — 缓存命中 board P50 / P95 (ms)
  4. 筛选延迟 — 带 l1 filter 的 board P50 / P95 (ms)
  5. 并发连点 — 10 并发不同 sort 请求, 总耗时 + 是否有超时/失败
结果写 web/tests/artifacts/bench_all_stocks_{label}.json, 并打印对照。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.environ.get("BENCH_BASE", "http://127.0.0.1:7799")
ART = os.path.join(os.path.dirname(__file__), "artifacts")


def _get(path: str, params: dict | None = None, timeout: float = 30.0) -> tuple[dict, float]:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
        dt = (time.time() - t) * 1000
        return body, dt
    except Exception as e:
        return {"_err": repr(e)}, (time.time() - t) * 1000


def _board(params: dict, timeout: float = 30.0):
    body, dt = _get("/api/all_stocks/board", params, timeout)
    data = body.get("data") if isinstance(body, dict) else None
    return data or {}, dt, body.get("_err")


def bench(label: str) -> dict:
    print(f"\n{'='*60}\n  BENCH [{label}]  base={BASE}\n{'='*60}")
    out: dict = {"label": label, "ts": time.time(), "base": BASE}

    base_params = {"page_size": 30, "offset": 0, "sort": "amount", "order": "desc", "with_fund": "true"}

    # 1. 冷延迟 (第一发, 缓存可能冷)
    d0, dt0, err0 = _board(base_params)
    cov = d0.get("total_available", 0)
    universe = d0.get("total_universe", 0)
    stats_total = (d0.get("stats") or {}).get("stats_total_count", 0)
    out["cold_ms"] = round(dt0, 1)
    out["cold_items"] = d0.get("count", 0)
    out["total_available"] = cov
    out["total_universe"] = universe
    out["stats_total_count"] = stats_total
    out["cold_err"] = err0
    print(f"[1] 冷启: {dt0:.0f}ms  首页 {d0.get('count',0)} 行  "
          f"total_available={cov}  universe={universe}  stats_total={stats_total}")

    # 2. 热延迟 P50/P95 (无 filter, 20 次)
    warm = []
    for _ in range(20):
        _, dt, _e = _board(base_params)
        warm.append(dt)
    warm.sort()
    out["warm_p50_ms"] = round(statistics.median(warm), 1)
    out["warm_p95_ms"] = round(warm[int(len(warm) * 0.95) - 1], 1)
    out["warm_max_ms"] = round(max(warm), 1)
    print(f"[2] 热启 (20x): P50={out['warm_p50_ms']}ms  P95={out['warm_p95_ms']}ms  max={out['warm_max_ms']}ms")

    # 3. 筛选延迟 (l1=大科技 等, 5 类各 4 次)
    filt = []
    filt_cov = []
    for l1 in ["大科技", "高端制造", "医药生物", "消费", "周期资源"]:
        for _ in range(4):
            data, dt, _e = _board({**base_params, "l1": l1})
            filt.append(dt)
            filt_cov.append(data.get("total_available", 0))
    filt.sort()
    out["filter_p50_ms"] = round(statistics.median(filt), 1)
    out["filter_p95_ms"] = round(filt[int(len(filt) * 0.95) - 1], 1)
    out["filter_max_ms"] = round(max(filt), 1)
    out["filter_avg_cov"] = round(sum(filt_cov) / len(filt_cov), 1) if filt_cov else 0
    print(f"[3] 筛选 (20x): P50={out['filter_p50_ms']}ms  P95={out['filter_p95_ms']}ms  "
          f"max={out['filter_max_ms']}ms  平均命中 {out['filter_avg_cov']:.0f} 只")

    # 4. 并发连点 (10 并发不同 sort — 模拟用户狂点表头)
    sorts = ["amount", "change_pct", "turnover", "volume_ratio", "mcap",
             "amplitude", "change_amt", "pe", "main_fund_inflow", "zt_today"]
    t_conc = time.time()
    conc_dts, conc_fail = [], 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_board, {**base_params, "sort": s}): s for s in sorts}
        for f in as_completed(futs):
            data, dt, e = f.result()
            conc_dts.append(dt)
            if e or not data:
                conc_fail += 1
    out["concurrent_total_ms"] = round((time.time() - t_conc) * 1000, 1)
    out["concurrent_max_ms"] = round(max(conc_dts), 1)
    out["concurrent_fail"] = conc_fail
    print(f"[4] 并发连点 (10 并发): 总 {out['concurrent_total_ms']}ms  "
          f"单发 max={out['concurrent_max_ms']}ms  失败 {conc_fail}/10")

    os.makedirs(ART, exist_ok=True)
    path = os.path.join(ART, f"bench_all_stocks_{label}.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"→ 写入 {path}")
    return out


def compare():
    before_p = os.path.join(ART, "bench_all_stocks_before.json")
    after_p = os.path.join(ART, "bench_all_stocks_after.json")
    if not (os.path.exists(before_p) and os.path.exists(after_p)):
        print("需要 before + after 两份数据才能对照")
        return
    b = json.load(open(before_p))
    a = json.load(open(after_p))
    print(f"\n{'='*60}\n  对照 BEFORE → AFTER\n{'='*60}")

    def speedup(bv, av):
        if not av or av <= 0:
            return "n/a"
        return f"{bv/av:.1f}x ({(bv/av-1)*100:+.0f}%)"

    rows = [
        ("覆盖 total_available", b.get("total_available"), a.get("total_available"), "越大越好"),
        ("冷延迟 ms", b.get("cold_ms"), a.get("cold_ms"), speedup(b.get("cold_ms", 0), a.get("cold_ms", 1))),
        ("热 P50 ms", b.get("warm_p50_ms"), a.get("warm_p50_ms"), speedup(b.get("warm_p50_ms", 0), a.get("warm_p50_ms", 1))),
        ("热 P95 ms", b.get("warm_p95_ms"), a.get("warm_p95_ms"), speedup(b.get("warm_p95_ms", 0), a.get("warm_p95_ms", 1))),
        ("筛选 P95 ms", b.get("filter_p95_ms"), a.get("filter_p95_ms"), speedup(b.get("filter_p95_ms", 0), a.get("filter_p95_ms", 1))),
        ("并发总 ms", b.get("concurrent_total_ms"), a.get("concurrent_total_ms"), speedup(b.get("concurrent_total_ms", 0), a.get("concurrent_total_ms", 1))),
    ]
    for name, bv, av, note in rows:
        print(f"  {name:22} {str(bv):>10} → {str(av):>10}   {note}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="before")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    if args.compare:
        compare()
    else:
        bench(args.label)
