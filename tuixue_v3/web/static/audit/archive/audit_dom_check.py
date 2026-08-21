#!/usr/bin/env python3
"""看 ai-review / strategy_picker 切过去后的 DOM"""
import asyncio
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.goto(BASE, wait_until="commit", timeout=60000)
        await page.wait_for_function("typeof showView === 'function'", timeout=15000)
        await page.wait_for_timeout(3000)

        for view in ['ai-review', 'strategy_picker', 'review', 'weekly_bull']:
            await page.evaluate(f"() => showView('{view}')")
            await page.wait_for_timeout(4000)
            info = await page.evaluate("""
                (v) => {
                    const sec = document.querySelector('.view-' + v);
                    return {
                        visible: !sec?.hidden,
                        innerText_len: sec?.innerText?.length || 0,
                        innerText_preview: sec?.innerText?.slice(0, 200) || '',
                        table_rows: sec?.querySelectorAll('tr').length || 0,
                        card_count: sec?.querySelectorAll('.card').length || 0,
                    };
                }
            """, view)
            print(f"[{view}] {info}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())