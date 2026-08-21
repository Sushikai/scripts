#!/usr/bin/env python3
"""最简化: 不 goto, 直接 page.route 拦截 view-stock, 看 body 真实状态"""
import asyncio
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        # 卸 SW
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(2000)
        try:
            await page.evaluate("() => navigator.serviceWorker.getRegistrations().then(rs => rs.forEach(r => r.unregister()))")
        except: pass

        responses = []
        async def on_response(r):
            if "view-stock" in r.url and "static" in r.url:
                try:
                    body = await r.body()
                except Exception as e:
                    body = f"<err {type(e).__name__}>"
                responses.append({"url": r.url, "fromSW": getattr(r, 'from_service_worker', None), "body_len": len(body) if isinstance(body, (bytes, bytearray)) else "n/a", "preview": body[:60] if isinstance(body, (bytes, bytearray)) else str(body)[:60]})
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        # 不动 SW, 等 5s
        await page.wait_for_timeout(5000)
        # 用 page.evaluate fetch 直接, 完全不走 SW
        r = await page.evaluate("""
            async () => {
                try {
                    const r1 = await fetch('/static/view-stock.js');
                    const t1 = await r1.text();
                    return {r1: {status: r1.status, len: t1.length, has_jing: t1.includes('view-stock')}};
                } catch (e) {
                    return {err: String(e)};
                }
            }
        """)
        print(f"3 fetches via page.evaluate:")
        for k, v in r.items():
            print(f"  {k}: {v}")
        await page.wait_for_timeout(2000)
        print(f"\nresponse events ({len(responses)}):")
        for r in responses:
            print(f"  {r}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
