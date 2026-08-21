#!/usr/bin/env python3
"""诊断: 每次 page.goto 后, view-dash.js script tag 实际出现次数 + 实际执行状态"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        err_log = []
        page.on("console", lambda m: err_log.append({"type": m.type, "text": m.text[:200]}) if m.type == "error" else None)

        for i in range(3):
            err_log.clear()
            stock_tags2 = []
            tags = []
            await page.goto(BASE, wait_until="commit", timeout=60000)
            await page.wait_for_timeout(4000)
            # 1. 数 view-dash script tags
            tags = await page.evaluate("""
                () => Array.from(document.querySelectorAll('script[src*="view-dash.js"]'))
                    .map(s => ({src: s.src, has_loaded: !!s.dataset.loaded}))
            """)
            stock_tags = await page.evaluate("""
                () => Array.from(document.querySelectorAll('script[src*="view-stock.js"]'))
                    .map(s => ({src: s.src, has_loaded: !!s.dataset.loaded}))
            """)
            # 触发 stock view
            await page.evaluate("showView('stock'); loadStockDetail('600519');")
            await page.wait_for_timeout(5000)
            stock_tags2 = await page.evaluate("""
                () => Array.from(document.querySelectorAll('script[src*="view-stock.js"]'))
                    .map(s => ({src: s.src, has_loaded: !!s.dataset.loaded}))
            """)
            # 2. 错误
            err_filter = [e for e in err_log if 'TREEMAP' in e['text'] or 'view-stock' in e['text'] or 'view-loader' in e['text']]
            print(f"\n[round {i+1}] view-dash tags: {len(tags)}, view-stock (before): {len(stock_tags)}, view-stock (after): {len(stock_tags2)}, errors: {len(err_filter)}")
            for t in stock_tags2:
                print(f"  stock tag src: {t['src'][:120]}")
            for t in tags:
                print(f"  dash tag src: {t['src'][:120]}")
            for e in err_filter[:3]:
                print(f"  ERR: {e['text'][:200]}")
            # 全 err
            if not err_filter:
                print(f"  ALL errors:")
                for e in err_log[:5]:
                    print(f"    {e['text'][:150]}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
