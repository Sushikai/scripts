#!/usr/bin/env python3
"""P95 延迟分布: 跑 20 个用户路径 × 3 轮, 收集每个 API 的 P50/P95/max"""
import asyncio
import time
from collections import defaultdict
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def user_path(browser, name, fn_seq):
    """fn_seq: list of fn strings to run sequentially"""
    ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await ctx.new_page()
    api_durs = defaultdict(list)

    async def on_response(r):
        if "/api/" in r.url and "stream" not in r.url:
            t0 = time.perf_counter()
            try:
                await r.finished()
            except Exception:
                return
            dur = (time.perf_counter() - t0) * 1000
            path = r.url.split("?")[0].replace(BASE, "")
            api_durs[path].append(dur)

    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    await page.goto(BASE, wait_until="commit", timeout=60000)
    await page.wait_for_function("typeof showView === 'function'", timeout=15000)
    await page.wait_for_timeout(3000)
    for fn in fn_seq:
        try:
            await page.evaluate(f"() => {{ try {{ {fn}; }} catch(e) {{}} }}")
            await page.wait_for_timeout(2500)
        except Exception:
            pass
    await ctx.close()
    return api_durs


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        all_apis = defaultdict(list)

        # 模拟用户 5 个常见路径
        paths = [
            ("homepage_visit", ["showView('dash')"]),
            ("stock_check_600519", ["showView('stock'); loadStockDetail('600519')"]),
            ("stock_check_000001", ["showView('stock'); loadStockDetail('000001')"]),
            ("weekly_bull_view", ["showView('weekly_bull')"]),
            ("sector_view", ["showView('sector', {arg: '半导体'})"]),
            ("dragons_view", ["showView('dragons')"]),
            ("review_view", ["showView('review')"]),
            ("all_stocks_view", ["showView('all_stocks')"]),
            ("watchlist_view", ["showView('watchlist')"]),
            ("yaogu_view", ["showView('yaogu')"]),
        ]

        for round_no in range(2):
            for name, fns in paths:
                durs = await user_path(browser, name, fns)
                for k, v in durs.items():
                    all_apis[k].extend(v)

        print(f"{'API':<40} {'calls':>6} {'P50':>8} {'P95':>8} {'max':>8}")
        # 按 max 排序, 找最慢的
        items = []
        for path, durs in all_apis.items():
            durs.sort()
            p50 = durs[len(durs) // 2] if durs else 0
            p95_idx = max(0, int(len(durs) * 0.95) - 1)
            p95 = durs[p95_idx] if durs else 0
            items.append((path, len(durs), p50, p95, max(durs or [0])))
        items.sort(key=lambda x: x[4], reverse=True)
        for path, n, p50, p95, mx in items[:25]:
            flag = "  ⚠" if mx > 500 else ""
            print(f"{path:<40} {n:>6} {p50:>7.0f}ms {p95:>7.0f}ms {mx:>7.0f}ms{flag}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())