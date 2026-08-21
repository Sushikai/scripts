#!/usr/bin/env python3
"""完全直连测试, 不触发 SW, 不 reload — 一次 goto 后 5s wait, fetch view-stock.js"""
import asyncio
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        responses = []

        async def on_response(r):
            if "view-stock" in r.url:
                # 不 wait, 立即尝试读
                try:
                    body = await r.body()
                except Exception as e:
                    body = f"<err {type(e).__name__}: {str(e)[:80]}>"
                responses.append({"url": r.url, "status": r.status, "fromSW": getattr(r, 'from_service_worker', None), "body_len": len(body) if isinstance(body, (bytes, bytearray)) else "n/a", "preview": body[:60] if isinstance(body, (bytes, bytearray)) else str(body)[:60]})
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        # 一次 goto, 然后 fetch view-stock.js 直接 (不通过 _loadViewScript)
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(3000)
        # 用 page.evaluate fetch
        try:
            r = await page.evaluate("""
                async () => {
                    const r = await fetch('/static/view-stock.js');
                    const t = await r.text();
                    return {status: r.status, content_length: r.headers.get('content-length'), transfer_encoding: r.headers.get('transfer-encoding'), body_len: t.length, preview: t.slice(0, 60)};
                }
            """)
            print(f"page.evaluate fetch: {r}")
        except Exception as e:
            print(f"err: {e}")
        # 等待响应事件
        await page.wait_for_timeout(2000)
        print(f"\nresponse events ({len(responses)}):")
        for r in responses:
            print(f"  {r}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
