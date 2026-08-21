#!/usr/bin/env python3
"""Probe: 点 K-line tab 后,验证 #kline-chart 真的有内容"""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        page = await ctx.new_page()
        err_log = []
        page.on("console", lambda m: err_log.append({"type": m.type, "text": m.text[:300]}) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: err_log.append({"type": "pageerror", "text": str(e)[:300]}))
        await page.goto("http://127.0.0.1:7799", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        # 进 stock + 直接切 K-line tab
        await page.evaluate("showView('stock'); loadStockDetail('600519');")
        await page.wait_for_timeout(6000)
        # 直接 click kline tab
        result = await page.evaluate("""
            () => {
                const btn = document.querySelector('.chart-tab[data-tab="kline"]');
                if (!btn) return {error: 'kline tab button not found', tabs: Array.from(document.querySelectorAll('.chart-tab')).map(b => b.dataset.tab)};
                btn.click();
                return {clicked: true, activeTab: document.querySelector('.chart-tab.active')?.dataset.tab};
            }
        """)
        print(f"after click: {result}")
        await page.wait_for_timeout(8000)
        # 验证 kline chart 出现且绘制
        state = await page.evaluate("""
            () => {
                const chart = document.querySelector('#kline-chart');
                const cvs = chart?.querySelector('canvas');
                return {
                    container_w: chart?.clientWidth || 0,
                    container_h: chart?.clientHeight || 0,
                    canvas_w: cvs?.width || 0,
                    canvas_h: cvs?.height || 0,
                    visible: chart ? !chart.closest('section')?.hidden && getComputedStyle(chart).display !== 'none' : false,
                };
            }
        """)
        print(f"kline state: {state}")
        # scroll kline chart into view before screenshot
        await page.evaluate("document.querySelector('#kline-chart')?.scrollIntoView({block: 'center'})")
        await page.wait_for_timeout(800)
        await page.screenshot(path="/Users/kaikai/scripts/tuixue_v3/web/static/audit/probe-kline-tab.png", full_page=False)
        print(f"errors: {err_log}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())