#!/usr/bin/env python3
"""单 page session, 切各 view, 不重新 goto — 避免 in-flight abort 噪音"""
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

        # 单 session: 一次性加载
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_function("typeof showView === 'function'", timeout=15000)
        await page.wait_for_timeout(4000)  # 首屏稳定

        views = [
            ("dash", "showView('dash')"),
            ("stock-600519", "showView('stock'); loadStockDetail('600519')"),
            ("stock-000001", "showView('stock'); loadStockDetail('000001')"),
            ("watchlist", "showView('watchlist')"),
            ("dragons", "showView('dragons')"),
            ("sector-半导体", "showView('sector', {arg: '半导体'})"),
            ("all_stocks", "showView('all_stocks')"),
            ("screener", "showView('screener')"),
            ("review", "showView('review')"),
            ("weekly_bull", "showView('weekly_bull')"),
            ("ai-review", "showView('ai-review')"),
            ("strategy_picker", "showView('strategy_picker')"),
            ("optimize", "showView('optimize')"),
            ("laws", "showView('laws')"),
            ("sources", "showView('sources')"),
            ("yaogu", "showView('yaogu')"),
            ("dexin", "showView('dexin')"),
            ("bull-trace", "showView('bull-trace')"),
        ]

        # 用 console.error 监听
        err_log = []
        page.on("console", lambda m: err_log.append({"type": m.type, "text": m.text[:300]}) if m.type == "error" else None)

        # install error counter
        await page.evaluate("""
            () => {
                window._errCount = 0;
                window.addEventListener('error', () => window._errCount++);
            }
        """)

        err_count_by_view = {}

        for name, fn in views:
            err_before = await page.evaluate("() => window._errCount || 0")
            try:
                await page.evaluate(f"() => {{ try {{ {fn}; }} catch(e) {{ console.error('PREP_ERR_' + '{name}' + ': ' + e.message); }} }}")
            except Exception as e:
                print(f"[{name}] PREP: {type(e).__name__}: {str(e)[:80]}")
            await page.wait_for_timeout(3500)
            state = await page.evaluate("""
                () => ({
                    view: _currentViewName,
                    visible: document.querySelector('.view:not([hidden])')?.dataset?.view || ''
                })
            """)
            err_after = await page.evaluate("() => window._errCount || 0")
            delta = err_after - err_before
            print(f"[{name}] view={state['view']} visible={state['visible']} new_errs={delta}")
            err_count_by_view[name] = delta

        # 打印所有 console.error
        print(f"\n=== console.error ({len(err_log)}) ===")
        for e in err_log:
            print(f"  [{e['type']}] {e['text']}")

        await browser.close()
        print(f"\n=== summary ===")
        for k, v in err_count_by_view.items():
            if v > 0:
                print(f"  {k}: {v} errors")


if __name__ == "__main__":
    asyncio.run(main())