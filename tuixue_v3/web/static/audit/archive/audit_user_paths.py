#!/usr/bin/env python3
"""逐 user-path 测,带显式 click"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
OUT = Path(__file__).parent


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        err_log = []
        page.on("console", lambda m: err_log.append({"type": m.type, "text": m.text[:300]}) if m.type in ("error",) else None)
        await page.goto(BASE, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

        # 测试 ticker click
        before = await page.evaluate("({view: _currentViewName, code: _currentStockCode})")
        print(f"before click: {before}")
        await page.evaluate("""
            () => {
                const tk = document.querySelector('.tk-clickable');
                console.log('ticker found:', !!tk, tk?.dataset?.code);
                tk?.click();
            }
        """)
        await page.wait_for_timeout(6000)
        after = await page.evaluate("({view: _currentViewName, code: _currentStockCode, hero: document.querySelector('.stock-hero-name, .hero-name')?.textContent || 'no hero'})")
        print(f"after click: {after}")
        await page.screenshot(path=str(OUT / "user-A-ticker-click.png"), full_page=False)

        # 测试 showView('stock') + loadStockDetail 直接调用
        await page.evaluate("""
            () => {
                showView('stock');
                loadStockDetail('600519');
            }
        """)
        await page.wait_for_timeout(6000)
        after2 = await page.evaluate("({view: _currentViewName, code: _currentStockCode, hero: document.querySelector('.stock-hero-name, .hero-name')?.textContent || 'no hero'})")
        print(f"after direct: {after2}")
        await page.screenshot(path=str(OUT / "user-B-stock-direct.png"), full_page=False)

        # 测试 sidebar click 个股
        await page.evaluate("""
            () => {
                showView('watchlist');
            }
        """)
        await page.wait_for_timeout(5000)
        wl_dom = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('#watchlist-tbody tr');
                return { count: rows.length, sample: rows[0]?.innerText?.slice(0, 200) || '' };
            }
        """)
        print(f"watchlist rows: {wl_dom}")
        await page.screenshot(path=str(OUT / "user-C-watchlist.png"), full_page=False)

        # 测试 dragons
        await page.evaluate("showView('dragons')")
        await page.wait_for_timeout(5000)
        dr = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('#dragons-top10 tr, .dragon-row, [data-code]');
                return { count: rows.length };
            }
        """)
        print(f"dragons: {dr}")
        await page.screenshot(path=str(OUT / "user-D-dragons.png"), full_page=False)

        # 测试 sector 传 arg
        await page.evaluate("showView('sector', { arg: '半导体' })")
        await page.wait_for_timeout(5000)
        sec = await page.evaluate("""
            () => {
                const v = document.querySelector('.view-sector:not([hidden])');
                return {
                    visible: !!v,
                    zt_count: document.querySelectorAll('.zt-row, .sector-zt-row').length,
                    sample: document.querySelector('.view-sector')?.innerText?.slice(0, 300) || '',
                };
            }
        """)
        print(f"sector 半导体: {sec}")
        await page.screenshot(path=str(OUT / "user-E-sector.png"), full_page=False)

        # 测试 all_stocks
        await page.evaluate("showView('all_stocks')")
        await page.wait_for_timeout(5000)
        ast = await page.evaluate("""
            () => {
                const v = document.querySelector('.view-all_stocks:not([hidden])');
                return {
                    visible: !!v,
                    cards: document.querySelectorAll('.as-card, .stocks-card, [data-code]').length,
                    rows: document.querySelectorAll('.stocks-table tbody tr, .as-tbody tr').length,
                };
            }
        """)
        print(f"all_stocks: {ast}")
        await page.screenshot(path=str(OUT / "user-F-all_stocks.png"), full_page=False)

        print(f"\n=== errors ({len(err_log)}) ===")
        for e in err_log[:15]:
            print(f"  [{e['type']}] {e['text']}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())