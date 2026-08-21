#!/usr/bin/env python3
"""精确复现: focused audit 里 stock 的 view-stock.js ERR_FAILED 顺序"""
import asyncio
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def view_test(page, name, prep_fn=None, wait_ms=6000):
    """fresh start 模式"""
    failures = []
    page.on("response", lambda r: failures.append({"url": r.url, "status": r.status}) if r.status >= 400 else None)
    page.on("requestfailed", lambda r: failures.append({
        "url": r.url, "reason": str(r.failure)[:80]
    }))

    await page.goto(BASE, wait_until="commit", timeout=60000)
    await page.wait_for_timeout(3000)
    if prep_fn:
        await page.evaluate(prep_fn)
    else:
        await page.evaluate(f"showView('{name}')")
    await page.wait_for_timeout(wait_ms)
    actual = [f for f in failures if 'sw.js' not in f.get('url', '') and '/sockjs' not in f.get('url', '')]
    if actual:
        print(f"[{name}] {len(actual)} failures:")
        for f in actual[:6]:
            print(f"  {f}")


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await view_test(page, "stock-ticker", """
            () => { const tk = document.querySelector('.tk-clickable'); if (tk) tk.click(); }
        """, wait_ms=7000)
        await view_test(page, "stock-direct", """
            () => { showView('stock'); loadStockDetail('600519'); }
        """, wait_ms=7000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())