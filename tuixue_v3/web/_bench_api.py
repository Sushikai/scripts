#!/usr/bin/env python3
"""R-PERF-050: API bench — 10 端点 × N 次 取 P50/P95/P99, 输出到 _baseline_results.json.

Usage:  python3 _bench_api.py [--rounds N] [--out PATH]
"""
import argparse, asyncio, json, time, statistics, sys
from pathlib import Path

DEFAULT_ROUNDS = 50
ENDPOINTS = [
    ("/api/stock/600519/full",          "GET", None, 8.0),
    ("/api/stock/000001/full",          "GET", None, 8.0),
    ("/api/stock/600519/intraday_5d",   "GET", None, 12.0),
    ("/api/stock/000001/intraday_5d",   "GET", None, 12.0),
    ("/api/dashboard/signal",           "GET", None, 3.0),
    ("/api/dashboard/index_trend?period=day", "GET", None, 5.0),
    ("/api/market/overview",            "GET", None, 3.0),
    ("/api/dexin/screen",               "GET", None, 5.0),
    ("/api/zt/live_pick?top_n=20",      "GET", None, 5.0),
    ("/api/yeren/scan",                 "GET", None, 8.0),
]

import urllib.request, urllib.parse


def _hit(url: str, timeout: float):
    t0 = time.monotonic()
    try:
        req = urllib.request.Request("http://127.0.0.1:7799" + url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return (time.monotonic() - t0) * 1000, 200
    except Exception as e:
        return (time.monotonic() - t0) * 1000, getattr(e, "code", 0) or 0


def bench(rounds: int) -> dict:
    out = {}
    for path, method, body, to in ENDPOINTS:
        times = []
        codes = []
        for _ in range(rounds):
            ms, code = _hit(path, to)
            times.append(ms)
            codes.append(code)
        times_sorted = sorted(times)
        p50 = times_sorted[len(times_sorted) // 2]
        p95 = times_sorted[int(len(times_sorted) * 0.95)]
        p99 = times_sorted[int(len(times_sorted) * 0.99)] if len(times_sorted) > 1 else times_sorted[-1]
        out[path] = {
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
            "p99_ms": round(p99, 1),
            "max_ms": round(max(times), 1),
            "n": rounds,
            "non_2xx": sum(1 for c in codes if c != 200),
        }
        print(f"  {path:45s}  p50={p50:7.1f}  p95={p95:7.1f}  p99={p99:7.1f}  max={max(times):7.1f}  2xx={rounds - sum(1 for c in codes if c != 200)}/{rounds}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    ap.add_argument("--out", default=str(Path(__file__).with_name("_baseline_results.json")))
    args = ap.parse_args()
    print(f"=== R-PERF bench: {args.rounds} rounds × {len(ENDPOINTS)} endpoints ===", flush=True)
    t0 = time.monotonic()
    results = bench(args.rounds)
    dt = time.monotonic() - t0
    payload = {
        "rounds": args.rounds,
        "wall_clock_s": round(dt, 1),
        "results": results,
        "ts": time.time(),
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nSaved → {args.out}  (wall {dt:.1f}s)", flush=True)


if __name__ == "__main__":
    main()