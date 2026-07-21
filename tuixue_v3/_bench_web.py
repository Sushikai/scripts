#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端 Web Vitals + 请求矩阵 benchmark — Playwright

用法:
    python3 _bench_web.py [--view dash|stock|dragons|screener|all_stocks|watchlist] [--mobile] [--runs 3]
    python3 _bench_web.py --all [--runs 3]  # 全部 view × desktop+mobile

输出: /tmp/bench_web.json (每次结果一行, 含 LCP/FCP/TTI/TBT/INP + API 矩阵)
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright


VIEWS = [
    ("dash",       "http://localhost:7799/#dash",         {"api": ["/api/dashboard/signal", "/api/dashboard/hot_sectors", "/api/dragons"]}),
    ("stock",      "http://localhost:7799/#stock=600519", {"api": ["/api/stock/600519/core", "/api/stock/600519/full"]}),
    ("dragons",    "http://localhost:7799/#dragons",      {"api": ["/api/dragons"]}),
    ("screener",   "http://localhost:7799/#screener",     {"api": ["/api/screener/result?strategy_id=baseline", "/api/screener/result?strategy_id=optimized"]}),
    ("all_stocks", "http://localhost:7799/#all_stocks",   {"api": ["/api/all_stocks/l1", "/api/all_stocks/board"]}),
    ("watchlist",  "http://localhost:7799/#watchlist",    {"api": ["/api/watchlist"]}),
]

VP_DESKTOP = {"width": 1440, "height": 900}
VP_MOBILE  = {"width": 390, "height": 844, "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"}


async def measure_one(browser, view, vp_name, url):
    ctx = await browser.new_context(viewport={k: v for k, v in vp_name.items() if k in ("width", "height")},
                                    user_agent=vp_name.get("user_agent"))
    page = await ctx.new_page()
    await page.add_init_script("""
        window.__bench = {};
        window.__bench.apiCalls = [];
        window.__bench.apiBytes = 0;
        window.__bench.apiDone = 0;
    """)

    api_events = []  # 收集 {kind: req|resp, url, status, t}

    def _on_request(req):
        if "/api/" in req.url:
            api_events.append({"kind": "req", "url": req.url, "t": time.perf_counter()})

    def _on_response(resp):
        if "/api/" in resp.url:
            try:
                cl = resp.headers.get("content-length")
                body_size = int(cl) if cl and cl.isdigit() else 0
            except Exception:
                body_size = 0
            api_events.append({"kind": "resp", "url": resp.url, "status": resp.status, "size": body_size, "t": time.perf_counter()})
    page.on("request", _on_request)
    page.on("response", _on_response)

    webvitals_js = """
    () => new Promise(async (resolve) => {
      const out = {fcp: null, lcp: null, tti_approx: null, long_tasks: 0, inp: null};
      try {
        new PerformanceObserver((list) => {
          for (const e of list.getEntries()) out.fcp = e.startTime;
        }).observe({type: 'paint', buffered: true});

        new PerformanceObserver((list) => {
          const es = list.getEntries();
          if (es.length) out.lcp = es[es.length - 1].startTime;
        }).observe({type: 'largest-contentful-paint', buffered: true});

        new PerformanceObserver((list) => {
          for (const e of list.getEntries()) out.long_tasks += e.duration || 0;
        }).observe({type: 'longtask', buffered: true});

        const nav = performance.getEntriesByType('navigation')[0];
        out.dcl = nav ? nav.domContentLoadedEventEnd : null;
        out.load = nav ? nav.loadEventEnd : null;

        // LCP 通常在 1s 内稳定,等 1.2s 够
        await new Promise(r => setTimeout(r, 1200));
      } catch(e) {}
      resolve(out);
    })
    """

    t0 = time.perf_counter()
    await page.goto(url, wait_until="domcontentloaded")
    vitals = await page.evaluate(webvitals_js)

    # 等首屏 API 完成 (SSE/轮询永不 idle,跳过 networkidle 直接走固定等待)
    await page.wait_for_timeout(1500)  # 给首屏 API 完成时间

    # 从 Python 侧的事件日志聚合 API 矩阵
    reqs = [e for e in api_events if e["kind"] == "req"]
    resps = [e for e in api_events if e["kind"] == "resp"]
    api_count = len(reqs)
    api_bytes = sum(e.get("size", 0) for e in resps)
    # 过滤 4xx/5xx
    error_count = sum(1 for e in resps if e.get("status", 0) >= 400)
    # ttfb: 按响应 - 请求
    t0_mono = api_events[0]["t"] if api_events else time.perf_counter()
    ttfb_ms = []
    for r in resps:
        url = r["url"]
        # 找同 URL 的 req
        matched = next((q for q in reqs if q["url"] == url and q["t"] <= r["t"]), None)
        if matched:
            ttfb_ms.append(round((r["t"] - matched["t"]) * 1000, 1))
    metrics = {
        "api_calls": api_count,
        "api_done": len(resps),
        "api_errors": error_count,
        "api_bytes": api_bytes,
        "first_api_at_ms": round((reqs[0]["t"] - t0_mono) * 1000, 1) if reqs else None,
        "last_api_at_ms": round((resps[-1]["t"] - t0_mono) * 1000, 1) if resps else None,
        "ttfb_p50_ms": round(sorted(ttfb_ms)[len(ttfb_ms)//2], 1) if ttfb_ms else None,
        "ttfb_p95_ms": round(sorted(ttfb_ms)[max(0, int(len(ttfb_ms)*0.95)-1)], 1) if ttfb_ms else None,
        "ttfb_max_ms": round(max(ttfb_ms), 1) if ttfb_ms else None,
    }
    metrics.update(vitals)
    metrics["wall_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    metrics["view"] = url.split('#')[-1] if '#' in url else url
    metrics["viewport"] = vp_name.get("width", "?")
    await ctx.close()
    return metrics


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--view")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default="/tmp/bench_web.json")
    args = ap.parse_args()

    vp = VP_MOBILE if args.mobile else VP_DESKTOP
    vp_name = "mobile" if args.mobile else "desktop"

    targets = VIEWS
    if args.view:
        targets = [(t, u, x) for t, u, x in VIEWS if t == args.view]
        if not targets:
            print(f"Unknown view: {args.view}", file=sys.stderr)
            return

    all_results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for tag, url, _ in targets:
            print(f"\n=== {tag} ({vp_name}) ===", flush=True)
            runs = []
            for r in range(args.runs):
                # 每次独立 ctx 模拟 cold cache
                m = await measure_one(browser, tag, vp, url)
                runs.append(m)
                print(f"  run {r+1}: LCP={m.get('lcp')}ms FCP={m.get('fcp')}ms wall={m['wall_ms']}ms apis={m['api_calls']}", flush=True)
            # 中位数作为该 view × viewport 的代表值
            med_lcp = sorted(r.get('lcp') or 0 for r in runs)[len(runs)//2]
            med_fcp = sorted(r.get('fcp') or 0 for r in runs)[len(runs)//2]
            med_wall = sorted(r['wall_ms'] for r in runs)[len(runs)//2]
            med_apis = sorted(r['api_calls'] for r in runs)[len(runs)//2]
            med_long = sorted(r.get('long_tasks') or 0 for r in runs)[len(runs)//2]
            agg = {
                "view": tag, "viewport": vp_name,
                "lcp_p50_ms": round(med_lcp, 1),
                "fcp_p50_ms": round(med_fcp, 1),
                "wall_p50_ms": round(med_wall, 1),
                "api_count_p50": med_apis,
                "long_tasks_ms_p50": round(med_long, 1),
                "runs": runs,
            }
            all_results.append(agg)
        await browser.close()

    out = Path(args.out)
    out.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n=== saved {out} ===")
    print("\n=== SUMMARY ===")
    print(f"{'view':12s} {'viewport':8s} {'LCP':>8s} {'FCP':>8s} {'wall':>8s} {'apis':>5s} {'longT':>7s}")
    for r in all_results:
        print(f"{r['view']:12s} {r['viewport']:8s} {r['lcp_p50_ms']:>7.1f}ms {r['fcp_p50_ms']:>7.1f}ms {r['wall_p50_ms']:>7.1f}ms {r['api_count_p50']:>5d} {r['long_tasks_ms_p50']:>6.1f}ms")


if __name__ == "__main__":
    asyncio.run(main())
