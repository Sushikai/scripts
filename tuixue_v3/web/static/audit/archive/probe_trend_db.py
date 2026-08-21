#!/usr/bin/env python3
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True)
        c = await b.new_context(viewport={'width':1280,'height':900})
        p = await c.new_page()
        msgs = []
        p.on('console', lambda m: msgs.append(f'[{m.type}] {m.text[:300]}'))
        p.on('request', lambda r: print('REQ:', r.url) if 'index_trend' in r.url else None)
        p.on('response', lambda r: print('RES:', r.url, r.status) if 'index_trend' in r.url else None)
        await p.goto('http://127.0.0.1:7799', wait_until='domcontentloaded')
        await p.wait_for_timeout(3500)
        await p.evaluate("showView('dash')")
        await p.wait_for_timeout(8000)
        snap = await p.evaluate('''
            () => {
                const idx = document.getElementById('index-trend-grid');
                const sec = document.getElementById('sector-trend-grid');
                return {
                    idxHTML: idx?.outerHTML.slice(0,500) || 'NO IDX',
                    secHTML: sec?.outerHTML.slice(0,500) || 'NO SEC',
                };
            }
        ''')
        for k,v in snap.items(): print(k, '=>', v)
        for m in msgs[-15:]: print(m)
        await b.close()

asyncio.run(main())
