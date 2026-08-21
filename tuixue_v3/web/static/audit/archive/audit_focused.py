#!/usr/bin/env python3
"""精确路径：每个 view 单独 fresh start,只切一次"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
OUT = Path(__file__).parent


async def view_test(page, name, prep_fn=None, wait_ms=6000):
    """进 view,跑 prep_fn,等 wait_ms,截图"""
    await page.goto(BASE, wait_until="commit", timeout=60000)
    # 等 showView 全局可用 (app.js 加载完成)
    try:
        await page.wait_for_function("typeof showView === 'function'", timeout=15000)
    except Exception:
        # app.js 没加载完, 跳过
        return {"view": "ERR: app.js not loaded", "ticker_ts": "", "visible_view": ""}
    if prep_fn:
        await page.evaluate(prep_fn)
    else:
        if name != "dash":
            await page.evaluate(f"showView('{name}')")
    await page.wait_for_timeout(wait_ms)
    state = await page.evaluate("""
        ({name}) => ({
            view: _currentViewName,
            ticker_ts: document.getElementById('ts-stamp')?.textContent || '',
            visible_view: document.querySelector('.view:not([hidden])')?.dataset?.view || '',
        })
    """, {"name": name})
    # 截图容错: fonts 没加载完时 30s 默认超时, 这里缩短 + 跳过 — 巡检目的不是看图
    try:
        await page.screenshot(path=str(OUT / f"focus-{name}.png"), full_page=False, timeout=8000)
    except Exception as e:
        print(f"  [screenshot skip {name}] {type(e).__name__}: {str(e)[:80]}")
    return state


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        err_log = []
        page.on("console", lambda m: err_log.append({"type": m.type, "text": m.text[:200]}) if m.type in ("error",) else None)

        # 1. dash (default)
        s = await view_test(page, "dash")
        print(f"dash: {s}")

        # 2. stock via ticker click
        s = await view_test(page, "stock-ticker", """
            () => {
                const tk = document.querySelector('.tk-clickable');
                if (tk) tk.click();
            }
        """, wait_ms=7000)
        print(f"stock-via-ticker: {s}")

        # 3. stock via direct
        s = await view_test(page, "stock-direct", """
            () => {
                showView('stock');
                loadStockDetail('600519');
            }
        """, wait_ms=7000)
        print(f"stock-direct 600519: {s}")

        # 4. watchlist
        s = await view_test(page, "watchlist", wait_ms=7000)
        wl = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('#watchlist-tbody tr, .watchlist-row, [data-code]');
                const placeholder = document.querySelector('.wl-loading, .wl-empty, .loading-state');
                return {
                    rows: rows.length,
                    placeholder: placeholder?.innerText?.slice(0, 100) || 'no placeholder',
                    sample: rows[0]?.innerText?.slice(0, 150) || '',
                };
            }
        """)
        print(f"watchlist: {wl}")

        # 5. dragons
        s = await view_test(page, "dragons", wait_ms=7000)
        dr = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('#dragons-top10 tr, .dragons-table tbody tr');
                return { count: rows.length, sample: rows[0]?.innerText?.slice(0, 100) || '' };
            }
        """)
        print(f"dragons: {dr}")

        # 6. sector
        s = await view_test(page, "sector-半导体", """
            () => {
                showView('sector', { arg: '半导体' });
            }
        """, wait_ms=7000)
        sec = await page.evaluate("""
            () => {
                const v = document.querySelector('.view-sector:not([hidden])');
                const rows = v?.querySelectorAll('tr, .sector-row');
                return {
                    rows: rows?.length || 0,
                    text: v?.innerText?.slice(0, 300) || '',
                };
            }
        """)
        print(f"sector 半导体: {sec}")

        # 7. all_stocks
        s = await view_test(page, "all_stocks", wait_ms=8000)
        ast = await page.evaluate("""
            () => {
                const cards = document.querySelectorAll('.as-card, .stocks-card, .as-tbody tr, .stocks-table tbody tr');
                return { count: cards.length };
            }
        """)
        print(f"all_stocks: {ast}")

        # 8. screener
        s = await view_test(page, "screener", wait_ms=7000)
        sc = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('#scr-tbody tr, .scr-tbody tr');
                return { count: rows.length };
            }
        """)
        print(f"screener: {sc}")

        # 9. review
        s = await view_test(page, "review", wait_ms=7000)
        rv = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('.review-row, #review-tbody tr');
                return { count: rows.length };
            }
        """)
        print(f"review: {rv}")

        # 10. weekly_bull
        s = await view_test(page, "weekly_bull", wait_ms=7000)
        wb = await page.evaluate("""
            () => {
                const cards = document.querySelectorAll('.wb-card, .weekly-card, .bull-row');
                return { count: cards.length };
            }
        """)
        print(f"weekly_bull: {wb}")

        # 11. ai-review
        s = await view_test(page, "ai-review", wait_ms=5000)
        ar = await page.evaluate("""
            () => document.querySelector('.view-ai-review')?.innerText?.slice(0, 200) || ''
        """)
        print(f"ai-review: {ar}")

        # 12. strategy_picker
        s = await view_test(page, "strategy_picker", wait_ms=7000)
        sp = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('.sp-row, .strategy-picker-row, .strategy-row');
                return { count: rows.length };
            }
        """)
        print(f"strategy_picker: {sp}")

        # 13. optimize
        s = await view_test(page, "optimize", wait_ms=7000)
        op = await page.evaluate("""
            () => {
                const rows = document.querySelectorAll('.opt-row, .optimize-row');
                return { count: rows.length };
            }
        """)
        print(f"optimize: {op}")

        # 14. laws
        s = await view_test(page, "laws", wait_ms=5000)
        lw = await page.evaluate("""
            () => document.querySelectorAll('.law-item, .law-row, ol li').length
        """)
        print(f"laws: {lw}")

        # 15. sources
        s = await view_test(page, "sources", wait_ms=5000)
        sr = await page.evaluate("""
            () => document.querySelectorAll('.source-card, .src-card').length
        """)
        print(f"sources: {sr}")

        # 16. yaogu (妖股页 R100 验证)
        s = await view_test(page, "yaogu", wait_ms=8000)
        yg = await page.evaluate("""
            () => ({
                signals: document.querySelectorAll('#yg-bt-result table tr').length,
                has_exit_hard_stop: !!document.querySelector('#yg-bt-exit option[value="hard_stop"]'),
                has_sl_input: !!document.querySelector('#yg-bt-sl'),
                sl_visible: document.getElementById('yg-bt-sl-wrap')?.style.display === 'inline-block',
                stocks_rows: document.querySelectorAll('.data-table tbody tr').length,
            })
        """)
        print(f"yaogu: {s} | check: {yg}")

        print(f"\n=== errors ({len(err_log)}) ===")
        for e in err_log[:20]:
            print(f"  [{e['type']}] {e['text']}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())