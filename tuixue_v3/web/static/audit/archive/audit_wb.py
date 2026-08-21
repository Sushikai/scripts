#!/usr/bin/env python3
"""精确测 weekly_bull 切过去耗时"""
import asyncio
import time
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_function("typeof showView === 'function'", timeout=15000)
        await page.wait_for_timeout(3000)

        # warm
        await page.evaluate("() => showView('weekly_bull')")
        await page.wait_for_timeout(3000)

        # measure
        t0 = time.perf_counter()
        await page.evaluate("() => showView('weekly_bull')")
        # 等 wb-card 出现
        try:
            await page.wait_for_selector(".wb-card", timeout=10000)
            elapsed = (time.perf_counter() - t0) * 1000
            cnt = await page.evaluate("() => document.querySelectorAll('.wb-card').length")
            print(f"weekly_bull: {elapsed:.0f}ms, {cnt} cards")
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"weekly_bull TIMEOUT after {elapsed:.0f}ms: {type(e).__name__}")

        # 看 sector 是不是真的有 innerText
        await page.evaluate("() => showView('sector', {arg: '半导体'})")
        await page.wait_for_timeout(5000)
        sec_innerText = await page.evaluate("() => document.querySelector('.view-sector')?.innerText?.length || 0")
        print(f"sector 内文长度: {sec_innerText}")

        # weekly_bull 是否需要 explicit reload?
        await page.evaluate("() => showView('weekly_bull')")
        await page.wait_for_timeout(3000)
        cnt2 = await page.evaluate("() => document.querySelectorAll('.wb-card').length")
        print(f"weekly_bull 二次: {cnt2} cards")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())