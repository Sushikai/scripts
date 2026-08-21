"""R190 screenshot: view-head 视觉验证."""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for _ in range(20):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
    await page.wait_for_timeout(500)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        # 截取 view-head 区域
        vh = await page.query_selector('.view-bv .view-head')
        await vh.screenshot(path="/tmp/r190_viewhead.png")
        await page.screenshot(path="/tmp/r190_top.png", clip={"x":0,"y":0,"width":390,"height":250})
        await b.close()
        print("[OK] /tmp/r190_viewhead.png + /tmp/r190_top.png")

if __name__ == "__main__":
    asyncio.run(run())