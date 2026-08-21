#!/usr/bin/env python3
"""跟踪 view-stock.js 失败时实际 response"""
import asyncio
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        responses = []
        page.on("response", lambda r: responses.append({"url": r.url, "status": r.status, "ct": r.headers.get("content-type", "?")[:40], "len": r.headers.get("content-length", "?")}) if "view-stock" in r.url else None)
        err = []
        page.on("console", lambda m: err.append(m.text[:200]) if m.type == "error" else None)

        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(3000)
        # 先清 SW
        try:
            await page.evaluate("() => navigator.serviceWorker.getRegistrations().then(rs => rs.forEach(r => r.unregister()))")
        except: pass
        await page.wait_for_timeout(500)
        # 再清 cache
        try:
            await page.evaluate("() => caches.keys().then(ks => ks.forEach(k => caches.delete(k)))")
        except: pass
        await page.wait_for_timeout(500)

        # 重 load
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(3000)
        # 切 stock
        try:
            await page.evaluate("showView('stock'); loadStockDetail('600519');")
        except Exception as e:
            print(f"eval err: {e}")
        await page.wait_for_timeout(5000)
        print(f"view-stock responses ({len(responses)}):")
        for r in responses:
            print(f"  status={r['status']} ct={r['ct']} len={r['len']} url={r['url'][:100]}")
        print(f"\nerrors ({len(err)}):")
        for e in err[:10]:
            print(f"  {e[:200]}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
