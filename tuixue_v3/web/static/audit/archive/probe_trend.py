#!/usr/bin/env python3
"""验证 Dash 大盘/板块分时 sparkline 卡片渲染"""
import asyncio
import json
import time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
OUT = Path(__file__).parent


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        api_log = []
        page.on("response", lambda r: api_log.append({
            "url": r.url, "status": r.status, "ms": int(time.time() * 1000),
        }))
        errs = []
        page.on("console", lambda m: errs.append({"type": m.type, "text": m.text[:300]}) if m.type == "error" else None)
        page.on("pageerror", lambda e: errs.append({"type": "pageerror", "text": str(e)[:300]}))

        await page.goto(BASE, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        await page.evaluate("showView('dash')")
        await page.wait_for_timeout(5000)

        snap = await page.evaluate("""
            () => {
                const grab = (id) => {
                    const el = document.getElementById(id);
                    if (!el) return null;
                    const tiles = el.querySelectorAll('.trend-tile');
                    return {
                        id, tileCount: tiles.length,
                        tiles: Array.from(tiles).slice(0, 6).map(t => ({
                            name: t.querySelector('.trend-tile-name')?.textContent || '',
                            pct: t.querySelector('.trend-tile-pct')?.textContent || '',
                            up: t.classList.contains('up'),
                            down: t.classList.contains('down'),
                            hasCanvas: !!t.querySelector('canvas'),
                            subText: t.querySelector('.trend-tile-sub')?.textContent?.replace(/\\s+/g, ' ').slice(0, 100) || '',
                        })),
                    };
                };
                return {
                    index: grab('index-trend-grid'),
                    sector: grab('sector-trend-grid'),
                };
            }
        """)
        await page.screenshot(path=str(OUT / "probe_trend_dash.png"), full_page=True)

        # Mobile viewport
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "probe_trend_mobile.png"), full_page=True)
        snap_m = await page.evaluate("""
            () => {
                const grab = (id) => {
                    const el = document.getElementById(id);
                    return el ? el.querySelectorAll('.trend-tile').length : 0;
                };
                return { index: grab('index-trend-grid'), sector: grab('sector-trend-grid') };
            }
        """)

        result = {
            "ts": time.time(),
            "snap": snap,
            "snap_mobile": snap_m,
            "trend_api": [a for a in api_log if 'index_trend' in a['url']],
            "errors": errs,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        Path(OUT / "probe_trend.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
