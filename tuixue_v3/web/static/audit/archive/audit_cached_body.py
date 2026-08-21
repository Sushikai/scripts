#!/usr/bin/env python3
"""SW cache 里 view-stock.js 的真实状态"""
import asyncio
import json
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_timeout(5000)
        js_v = await page.evaluate("() => (typeof _assetVersionQuery === 'function') ? _assetVersionQuery() : ''")
        print(f"_assetVersionQuery: {js_v}")

        names = await page.evaluate("() => caches.keys()")
        print(f"Cache names: {names}")
        for name in names:
            keys = await page.evaluate(
                "(n) => caches.open(n).then(c => c.keys().then(rs => rs.map(r => r.url)))",
                name,
            )
            stock_keys = [k for k in keys if 'view-stock' in k]
            print(f"\n[{name}] view-stock entries: {len(stock_keys)}")
            for k in stock_keys:
                info = await page.evaluate(
                    """
                    async (args) => {
                        const c = await caches.open(args.name);
                        const r = await c.match(args.k);
                        if (!r) return null;
                        const headers = {};
                        r.headers.forEach((v, k) => headers[k] = v);
                        let body_len = -1;
                        try {
                            const buf = await r.clone().arrayBuffer();
                            body_len = buf.byteLength;
                        } catch(e) {
                            body_len = 'ERR ' + e.message;
                        }
                        return {status: r.status, ct: headers['content-type'], cl: headers['content-length'], body_len, ok: r.ok};
                    }
                    """,
                    {"name": name, "k": k},
                )
                print(f"  {k[:120]}")
                print(f"    {json.dumps(info)}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())