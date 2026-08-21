#!/usr/bin/env python3
"""冷启动性能: 全新 context (无 SW cache) → goto → 切 view, 抓每个 API 延迟"""
import asyncio
import time
from collections import defaultdict
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def cold_view(browser, name, fn, data_ready_expr):
    ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await ctx.new_page()
    api_times = {}
    api_order = []

    async def on_response(r):
        if "/api/" in r.url and "stream" not in r.url:
            t0 = time.perf_counter()
            await r.finished()
            dur = (time.perf_counter() - t0) * 1000
            path = r.url.replace(BASE, "").split("?")[0]
            api_times[path] = api_times.get(path, 0) + dur
            api_order.append((path, dur))

    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    t_start = time.perf_counter()
    await page.goto(BASE, wait_until="commit", timeout=60000)
    await page.wait_for_function("typeof showView === 'function'", timeout=15000)
    try:
        await page.evaluate(f"() => {{ try {{ {fn}; }} catch(e) {{}} }}")
    except Exception:
        pass
    try:
        await page.wait_for_function(data_ready_expr, timeout=12000)
        data_ms = (time.perf_counter() - t_start) * 1000
    except Exception:
        data_ms = None
    await ctx.close()
    return {"name": name, "data_ms": data_ms, "api_times": api_times, "api_order": api_order}


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        views = [
            ("dash", "showView('dash')",
             "document.querySelector('#ts-stamp')?.textContent?.includes('已刷新')"),
            ("stock-600519", "showView('stock'); loadStockDetail('600519')",
             "document.querySelector('.view-stock')?.innerText?.length > 500"),
            ("watchlist", "showView('watchlist')",
             "document.querySelectorAll('#watchlist-tbody tr, .wl-row, [data-code]').length > 5"),
            ("dragons", "showView('dragons')",
             "document.querySelector('.view-dragons')?.innerText?.length > 100"),
            ("sector", "showView('sector', {arg: '半导体'})",
             "document.querySelector('.view-sector')?.innerText?.length > 200"),
            ("all_stocks", "showView('all_stocks')",
             "document.querySelector('.view-all_stocks')?.innerText?.length > 200"),
            ("review", "showView('review')",
             "document.querySelectorAll('.view-review tr').length > 5"),
            ("weekly_bull", "showView('weekly_bull')",
             "document.querySelectorAll('.wb-card').length > 10"),
            ("yaogu", "showView('yaogu')",
             "document.querySelector('.view-yaogu')?.innerText?.length > 200"),
            ("dexin", "showView('dexin')",
             "document.querySelector('.view-dexin')?.innerText?.length > 200"),
            ("optimize", "showView('optimize')",
             "document.querySelector('.view-optimize')?.innerText?.length > 100"),
        ]

        print(f"{'view':<18} {'data_ms':>8} | slow APIs (dur>300ms)")
        for name, fn, expr in views:
            r = await cold_view(browser, name, fn, expr)
            slow = sorted(r["api_order"], key=lambda x: x[1], reverse=True)
            slow_apis = [(p, f"{d:.0f}ms") for p, d in slow if d > 300][:5]
            status = f"{r['data_ms']:.0f}" if r["data_ms"] else "TIMEOUT"
            print(f"{name:<18} {status:>8} | {slow_apis if slow_apis else '(all <300ms)'}")
            # 总 API 调用
            total = sum(r["api_times"].values())
            if total > 500:
                print(f"    total API wall: {total:.0f}ms over {len(r['api_order'])} calls")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())