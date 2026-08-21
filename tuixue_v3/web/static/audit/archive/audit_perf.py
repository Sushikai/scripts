#!/usr/bin/env python3
"""性能基线: 每个 view 从 showView() 到数据渲染完成的时间 (秒开秒加载目标)"""
import asyncio
import time
from collections import defaultdict
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def measure_view(page, name, fn, data_ready_expr):
    """返回 {view_ms, data_ms, api_calls}"""
    start = time.perf_counter()
    try:
        await page.evaluate(f"() => {{ try {{ {fn}; }} catch(e) {{ console.error('PREP:' + e.message); }} }}")
    except Exception as e:
        return {"name": name, "view_ms": None, "data_ms": None, "err": f"prep {type(e).__name__}"}
    # 等 data_ready 条件 (最多 12s)
    try:
        await page.wait_for_function(data_ready_expr, timeout=12000)
        data_ms = (time.perf_counter() - start) * 1000
    except Exception:
        data_ms = None
    return {"name": name, "data_ms": data_ms}


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        # 单 session, warm cache: 先全跑一遍 (SW 缓存 warm), 再正式测
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_function("typeof showView === 'function'", timeout=15000)
        await page.wait_for_timeout(5000)

        views = [
            ("dash", "showView('dash')",
             "document.querySelector('#ts-stamp')?.textContent?.includes('已刷新')"),
            ("stock-600519", "showView('stock'); loadStockDetail('600519')",
             "document.querySelector('.view-stock')?.innerText?.length > 500"),
            ("watchlist", "showView('watchlist')",
             "document.querySelectorAll('#watchlist-tbody tr, .wl-row, [data-code]').length > 5"),
            ("dragons", "showView('dragons')",
             "document.querySelector('.view-dragons')?.innerText?.length > 100"),
            ("sector", "showView('sector', {arg: '半导体'})",
             "document.querySelector('.view-sector')?.innerText?.length > 200"),
            ("all_stocks", "showView('all_stocks')",
             "document.querySelector('.view-all_stocks')?.innerText?.length > 200"),
            ("screener", "showView('screener')",
             "document.querySelector('.view-screener')?.innerText?.length > 200"),
            ("review", "showView('review')",
             "document.querySelectorAll('.review-row, #review-tbody tr, .view-review tr').length > 5"),
            ("weekly_bull", "showView('weekly_bull')",
             "document.querySelectorAll('.wb-card').length > 10"),
            ("yaogu", "showView('yaogu')",
             "document.querySelectorAll('.data-table tbody tr').length > 10"),
            ("dexin", "showView('dexin')",
             "document.querySelector('.view-dexin')?.innerText?.length > 200"),
            ("ai-review", "showView('ai-review')",
             "document.querySelector('.view-ai-review')?.innerText?.length > 50"),
            ("strategy_picker", "showView('strategy_picker')",
             "document.querySelector('.view-strategy_picker')?.querySelectorAll('.card').length > 10"),
            ("optimize", "showView('optimize')",
             "document.querySelector('.view-optimize')?.innerText?.length > 100"),
            ("laws", "showView('laws')",
             "document.querySelectorAll('.law-item, .law-row, .view-laws li').length > 10"),
            ("sources", "showView('sources')",
             "document.querySelectorAll('.source-card, .src-card, .view-sources .card').length > 5"),
        ]

        # warm pass
        for name, fn, expr in views:
            try:
                await page.evaluate(f"() => {{ try {{ {fn}; }} catch(e) {{}} }}")
                await page.wait_for_timeout(2500)
            except Exception:
                pass

        # measure pass ×2 (取较快值模拟已 warm 的第二次访问)
        results = defaultdict(list)
        for round_no in range(2):
            for name, fn, expr in views:
                r = await measure_view(page, name, fn, expr)
                if r.get("data_ms"):
                    results[name].append(r["data_ms"])

        print(f"{'view':<20} {'best_ms':>8} {'avg_ms':>8} {'status':<8}")
        for name in [v[0] for v in views]:
            ms_list = results.get(name, [])
            if ms_list:
                best = min(ms_list)
                avg = sum(ms_list) / len(ms_list)
                status = "OK" if best <= 1000 else ("SLOW" if best <= 3000 else "VERY_SLOW")
                print(f"{name:<20} {best:>8.0f} {avg:>8.0f} {status:<8}")
            else:
                print(f"{name:<20} {'--':>8} {'--':>8} {'NO_DATA':<8}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())