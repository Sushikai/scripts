#!/usr/bin/env python3
"""深入诊断: 跟踪每个 view 切到时, view script 加载次数 + 全 console"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
OUT = Path(__file__).parent


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        err_log = []
        warn_log = []
        script_loads = []
        page.on("console", lambda m: (err_log.append({"type": m.type, "text": m.text[:300]}) if m.type == "error" else
                                         (warn_log.append({"text": m.text[:200]}) if m.type == "warning" else None)))
        # 跟踪每个 script 元素的 src 出现
        page.on("request", lambda r: script_loads.append({"url": r.url, "type": r.resource_type}) if r.resource_type == "script" else None)

        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(3000)

        views = ["dash", "stock", "dragons", "watchlist", "review", "ai-review",
                 "screener", "sector", "all_stocks", "weekly_bull", "strategy_picker",
                 "optimize", "laws", "sources", "yaogu", "yeren-ai"]
        for v in views:
            script_loads.clear()
            err_log.clear()
            if v == "stock":
                await page.evaluate("showView('stock'); loadStockDetail('600519');")
            elif v == "sector":
                await page.evaluate("showView('sector', { arg: '半导体' });")
            else:
                await page.evaluate(f"showView('{v}');")
            await page.wait_for_timeout(4000)
            print(f"\n[{v}]")
            for sl in script_loads:
                short = sl["url"].replace("http://127.0.0.1:7799", "")
                print(f"  script: {short}")
            for e in err_log[:8]:
                print(f"  ERR: {e['text'][:200]}")
            if not script_loads and not err_log:
                print(f"  (no script load, no error)")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
