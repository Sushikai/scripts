#!/usr/bin/env python3
"""追踪每个 view 切过去时的 503/404 URL 来源"""
import asyncio
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        failures = []
        responses = []
        page.on("response", lambda r: (
            failures.append({"url": r.url, "status": r.status, "method": r.request.method})
            if r.status >= 400 else None
        ))
        page.on("requestfailed", lambda r: failures.append({
            "url": r.url, "status": "FAIL", "method": r.method,
            "reason": r.failure
        }))

        # 一次 load 完整页面, 然后切到各个 view
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(3000)

        targets = [
            ("dash", "showView('dash')"),
            ("stock", "showView('stock'); loadStockDetail('600519')"),
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
            ("laws", "showView('laws')"),
            ("sources", "showView('sources')"),
            ("yaogu", "showView('yaogu')"),
            ("dexin", "showView('dexin')"),
            ("review-detail", "showView('review-detail')"),
            ("bull-trace", "showView('bull-trace')"),
        ]

        for name, fn in targets:
            failures.clear()
            try:
                await page.evaluate(f"() => {{ try {{ {fn}; }} catch(e) {{ console.error('PREP_FAIL: ' + e.message); }} }}")
                await page.wait_for_timeout(2500)
            except Exception as e:
                print(f"[{name}] PREP ERR: {type(e).__name__}: {str(e)[:80]}")
                continue
            # 跳过 page errors: 关心的是网络层
            actual = [f for f in failures if f.get("status") not in (None,)]
            if actual:
                print(f"[{name}] {len(actual)} failures:")
                for f in actual[:8]:
                    extra = f.get("reason", "") or f.get("status", "")
                    print(f"  {f['method']} {f['status']} {f['url'][:140]} {str(extra)[:60]}")
            else:
                print(f"[{name}] OK")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())