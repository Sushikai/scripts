#!/usr/bin/env python3
"""追踪 404 / script 错误的具体 URL / 来源"""
import asyncio
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        bad = []
        page.on("response", lambda r: bad.append({"url": r.url, "status": r.status}) if r.status >= 400 else None)
        page.on("requestfailed", lambda r: bad.append({"url": r.url, "reason": str(r.failure)[:80]}))

        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_function("typeof showView === 'function'", timeout=15000)
        # 跑所有 view
        targets = [
            ("stock", "showView('stock'); loadStockDetail('600519');"),
            ("watchlist", "showView('watchlist')"),
            ("dragons", "showView('dragons')"),
            ("sector", "showView('sector', {arg: '半导体'})"),
            ("all_stocks", "showView('all_stocks')"),
            ("screener", "showView('screener')"),
            ("review", "showView('review')"),
            ("weekly_bull", "showView('weekly_bull')"),
            ("ai-review", "showView('ai-review')"),
            ("strategy_picker", "showView('strategy_picker')"),
            ("optimize", "showView('optimize')"),
            ("yaogu", "showView('yaogu')"),
        ]
        for name, fn in targets:
            try:
                await page.evaluate(f"() => {{ try {{ {fn}; }} catch(e) {{ console.error('PREP: ' + e.message); }} }}")
                await page.wait_for_timeout(2500)
            except Exception as e:
                print(f"[{name}] PREP ERR: {type(e).__name__}: {str(e)[:80]}")
        print(f"\n=== {len(bad)} failures ===")
        for b in bad[:20]:
            print(f"  {b}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())