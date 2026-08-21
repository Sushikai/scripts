#!/usr/bin/env python3
"""用户视角的视图巡检:模拟真实路径 (点 ticker → 进 stock 等) 而非直接 showView"""
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
        err_log, api_log = [], []

        page.on("response", lambda r: api_log.append({"url": r.url, "status": r.status, "ok": r.ok}))
        page.on("console", lambda m: err_log.append({"type": m.type, "text": m.text[:300]}) if m.type in ("error",) else None)

        await page.goto(BASE, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # 1) dash 默认就是 dash 视图
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(OUT / "user-01-dash.png"), full_page=False)

        # 2) 通过 tickerbar 点击进 stock
        await page.evaluate("""
            () => {
                const t = document.querySelector('.tk-item');
                if (t) t.click();
            }
        """)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=str(OUT / "user-02-stock-via-ticker.png"), full_page=False)

        # 3) 进入 watchlist
        await page.evaluate("showView('watchlist')")
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(OUT / "user-03-watchlist.png"), full_page=False)

        # 4) 进入 dragons
        await page.evaluate("showView('dragons')")
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(OUT / "user-04-dragons.png"), full_page=False)

        # 5) 进入 all_stocks
        await page.evaluate("showView('all_stocks')")
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(OUT / "user-05-all_stocks.png"), full_page=False)

        # 6) 进入 sector (需传 sector 名)
        await page.evaluate("showView('sector', { arg: '半导体' })")
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(OUT / "user-06-sector.png"), full_page=False)

        # 汇总
        fails = [a for a in api_log if not a["ok"]]
        print(f"\n=== API ===")
        print(f"total={len(api_log)} failed={len(fails)}")
        for f in fails[:20]:
            print(f"  ✗ {f['status']} {f['url']}")
        print(f"\n=== errors ===")
        for e in err_log[:10]:
            print(f"  [{e['type']}] {e['text']}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())