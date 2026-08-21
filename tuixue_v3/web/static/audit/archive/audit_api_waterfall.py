#!/usr/bin/env python3
"""冷启动下, 每个 view 的真实 API waterfall (sorted by start time)"""
import asyncio
import time
from collections import defaultdict
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def measure(browser, name, fn, data_ready_expr):
    ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await ctx.new_page()
    api_starts = []
    api_durations = {}

    async def on_request(r):
        if "/api/" in r.url and "stream" not in r.url:
            api_starts.append((time.perf_counter(), r.url.split("?")[0].replace(BASE, "")))

    async def on_response(r):
        if "/api/" in r.url and "stream" not in r.url:
            t0 = time.perf_counter()
            try:
                await r.finished()
            except Exception:
                pass
            dur = (time.perf_counter() - t0) * 1000
            api_durations[r.url.split("?")[0].replace(BASE, "")] = dur

    page.on("request", lambda r: asyncio.create_task(on_request(r)))
    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    t_start = time.perf_counter()
    await page.goto(BASE, wait_until="commit", timeout=60000)
    await page.wait_for_function("typeof showView === 'function'", timeout=15000)
    await page.evaluate(f"() => {{ try {{ {fn}; }} catch(e) {{}} }}")
    try:
        await page.wait_for_function(data_ready_expr, timeout=15000)
        data_ms = (time.perf_counter() - t_start) * 1000
    except Exception:
        data_ms = -1
    await ctx.close()
    return data_ms, api_durations


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        views = [
            ("dash", "showView('dash')",
             "document.querySelector('#ts-stamp')?.textContent?.includes('已刷新')"),
            ("review", "showView('review')",
             "document.querySelectorAll('.view-review tr').length > 5"),
            ("weekly_bull", "showView('weekly_bull')",
             "document.querySelectorAll('.wb-card').length > 10"),
            ("all_stocks", "showView('all_stocks')",
             "document.querySelector('.view-all_stocks')?.innerText?.length > 200"),
            ("strategy_picker", "showView('strategy_picker')",
             "document.querySelector('.view-strategy_picker')?.innerText?.length > 500"),
        ]

        for name, fn, expr in views:
            data_ms, api_durs = await measure(browser, name, fn, expr)
            top = sorted(api_durs.items(), key=lambda x: x[1], reverse=True)[:6]
            print(f"\n[{name}] data_ms={data_ms:.0f}")
            for path, dur in top:
                print(f"  {dur:>6.0f}ms  {path}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())