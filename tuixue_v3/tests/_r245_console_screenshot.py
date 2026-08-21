"""R245: console 错误检查 + 首屏截图 (记录用)"""
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
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length >= 1"):
            break
        await page.wait_for_timeout(500)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        await load(page)
        await page.wait_for_timeout(1000)
        nonFavicon = [e for e in errors if "favicon" not in e]
        print(f"console errors: {len(nonFavicon)}")
        for e in nonFavicon[:5]:
            print(f"  ERR: {e[:120]}")
        await page.screenshot(path="tests/_r245_shot.png", full_page=False)
        print("[OK] screenshot saved")
        assert len(nonFavicon) == 0, f"console errors: {nonFavicon}"
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
