#!/usr/bin/env python3
"""50 轮 切 view 压力测试 — 看是否还有遗漏的脚本错误"""
import asyncio
from collections import Counter
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        rounds = 50
        all_errs = []
        for i in range(rounds):
            ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await ctx.new_page()
            err_log = []
            page.on("console", lambda m: err_log.append(m.text[:300]) if m.type == "error" else None)

            await page.goto(BASE, wait_until="commit", timeout=60000)
            try:
                await page.wait_for_function("typeof showView === 'function'", timeout=15000)
            except Exception:
                await ctx.close()
                continue
            await page.wait_for_timeout(3000)

            views = [
                "showView('stock'); loadStockDetail('600519');",
                "showView('watchlist');",
                "showView('dragons');",
                "showView('sector', {arg: '半导体'});",
                "showView('all_stocks');",
                "showView('screener');",
                "showView('review');",
                "showView('yaogu');",
                "showView('dexin');",
                "showView('optimize');",
            ]
            for fn in views:
                try:
                    await page.evaluate(f"() => {{ try {{ {fn}; }} catch(e) {{}} }}")
                except Exception:
                    pass
                await page.wait_for_timeout(800)

            # filter benign
            real_errs = [e for e in err_log if 'ERR_NAME_NOT_RESOLVED' not in e]
            if real_errs:
                # 去重
                for e in real_errs:
                    all_errs.append(e)
                print(f"[round {i+1}] {len(real_errs)} ERRORS:")
                for e in real_errs[:3]:
                    print(f"  {e[:200]}")
            await ctx.close()

        print(f"\n=== summary: {len(all_errs)} total errors across {rounds} rounds ===")
        cnt = Counter(all_errs)
        for k, v in cnt.most_common(10):
            print(f"  ×{v}: {k[:200]}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())