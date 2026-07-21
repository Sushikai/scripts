#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后端 API benchmark — 单接口 N 并发 M 请求,收集 P50/P95/P99/max/throughput

用法:
    python3 _bench_api.py <endpoint> [n=200] [c=20]
    python3 _bench_api.py --list            # 列预设端点
    python3 _bench_api.py --all [n=100]     # 跑全部预设

输出: stdout json (一行一项端点) — 便于二次聚合
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import aiohttp


# 关键端点 + 标签
PRESETS = [
    ("stock_core",   "/api/stock/600519/core"),
    ("stock_full",   "/api/stock/600519/full"),
    ("watchlist",    "/api/watchlist"),
    ("dashboard_signal", "/api/dashboard/signal"),
    ("dashboard_hot",    "/api/dashboard/hot_sectors"),
    ("dragons",      "/api/dragons"),
    ("screener_base",    "/api/screener/result?sort=pass_count:desc&limit=200&mode=multi&strategy_id=baseline"),
    ("screener_opt",     "/api/screener/result?sort=v2_score:desc&limit=200&mode=multi&strategy_id=optimized"),
    ("all_stocks_board", "/api/all_stocks/board?page_size=30"),
    ("review_portfolio", "/api/review/portfolio"),
]


async def bench_one(session, url, n, c):
    """N 次请求 C 并发 — 收集每次耗时 (ms)"""
    latencies = []
    errors = 0
    sem = asyncio.Semaphore(c)

    async def hit():
        nonlocal errors
        async with sem:
            t0 = time.perf_counter()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=60, connect=10)) as r:
                    await r.read()
                    if r.status != 200:
                        errors += 1
                    else:
                        latencies.append((time.perf_counter() - t0) * 1000)
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"  ERR #{errors}: {type(e).__name__}: {e}", file=sys.stderr)

    t0 = time.perf_counter()
    await asyncio.gather(*[hit() for _ in range(n)])
    wall = (time.perf_counter() - t0) * 1000
    return latencies, errors, wall


def pct(xs, p):
    if not xs:
        return 0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(len(xs) * p / 100)))
    return round(xs[k], 2)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("endpoint", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--n", type=int, default=200, help="总请求数")
    ap.add_argument("--c", type=int, default=20, help="并发")
    ap.add_argument("--warmup", type=int, default=10, help="预热次数 (默认 10 — cache 类端点需 > Redis TTL)")
    ap.add_argument("--base", default="http://localhost:7799")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    if args.list:
        for tag, ep in PRESETS:
            print(f"{tag:20s} {ep}")
        return

    targets = []
    if args.all:
        targets = PRESETS
    elif args.endpoint:
        targets = [(args.tag or args.endpoint, args.endpoint)]
    else:
        ap.print_help()
        return

    timeout = aiohttp.ClientTimeout(total=60)
    conn = aiohttp.TCPConnector(limit=200, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
        results = []
        for tag, ep in targets:
            url = urljoin(args.base, ep)
            # warmup
            for _ in range(args.warmup):
                try:
                    async with session.get(url) as r:
                        await r.read()
                except Exception:
                    pass
            # bench
            t0 = time.perf_counter()
            latencies, errors, wall = await bench_one(session, url, args.n, args.c)
            rps = args.n / ((time.perf_counter() - t0) or 0.001)
            res = {
                "tag": tag, "endpoint": ep,
                "n": args.n, "c": args.c,
                "rps": round(rps, 2),
                "errors": errors,
                "p50_ms": pct(latencies, 50),
                "p95_ms": pct(latencies, 95),
                "p99_ms": pct(latencies, 99),
                "max_ms": round(max(latencies) if latencies else 0, 2),
                "mean_ms": round(statistics.mean(latencies) if latencies else 0, 2),
                "n_ok": len(latencies),
            }
            results.append(res)
            print(json.dumps(res, ensure_ascii=False))

        # 写文件 (--all 时)
        if args.all:
            out = Path("/tmp/bench_api.json")
            out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
            print(f"\n=== saved {out} ===")


if __name__ == "__main__":
    asyncio.run(main())
