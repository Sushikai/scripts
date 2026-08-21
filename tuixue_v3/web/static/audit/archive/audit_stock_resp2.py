#!/usr/bin/env python3
"""看 view-stock.js 拿到 body 真实大小"""
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
                import time as _t
                t0 = _t.time()
                try:
                    body = await r.body()
                    took = _t.time() - t0
                except Exception as e:
                    body = f"<err {e}>"
                    took = -1
                all_headers = {k: v for k, v in r.headers.items()}
                responses.append({"url": r.url, "status": r.status, "ct": r.headers.get("content-type", "?")[:40], "len_header": r.headers.get("content-length", "?"), "fromSW": getattr(r, 'from_service_worker', None), "headers_keys": list(all_headers.keys())[:10], "body_len": len(body) if isinstance(body, (bytes, bytearray)) else "n/a", "body_took_s": round(took, 3), "body_preview": body[:80] if isinstance(body, (bytes, bytearray)) else body})
        page.on("response", lambda r: asyncio.create_task(on_response(r)))
        err = []
        sw_log = []
        page.on("console", lambda m: (err.append(m.text[:200]) if m.type == "error" else (sw_log.append(m.text[:300]) if "[SW]" in m.text else None)))

        # 关 SW 模式
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(2000)
        # 卸 SW + 清 cache, 避免干扰
        try:
            await page.evaluate("() => navigator.serviceWorker.getRegistrations().then(rs => rs.forEach(r => r.unregister()))")
            await page.evaluate("() => caches.keys().then(ks => ks.forEach(k => caches.delete(k)))")
        except Exception as e:
            print(f"clear err: {e}")
        await page.wait_for_timeout(500)
        # 重新加载, 没 SW 拦截
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(3000)
        try:
            await page.evaluate("showView('stock'); loadStockDetail('600519');")
        except Exception as e:
            print(f"eval err: {e}")
        await page.wait_for_timeout(5000)
        print(f"view-stock responses ({len(responses)}):")
        for r in responses:
            print(f"  status={r['status']} ct={r['ct']} len_h={r['len_header']} fromSW={r['fromSW']} body_len={r['body_len']} body_took={r['body_took_s']}s")
            print(f"    headers: {r['headers_keys']}")
        print(f"\nerrors ({len(err)}):")
        for e in err[:5]:
            print(f"  {e[:200]}")
        print(f"\nSW log ({len(sw_log)}):")
        for s in sw_log[:10]:
            print(f"  {s[:200]}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
