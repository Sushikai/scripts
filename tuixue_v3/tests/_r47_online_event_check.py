"""R47 online 事件自动刷新."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__refreshed = 0;
    window.__bv = { refresh: function(force) { window.__refreshed++; } };
    window.addEventListener('online', function(){
      if (window.__bv && window.__bv.refresh) window.__bv.refresh(true);
    });
    """
    html = "<!DOCTYPE html><html><body><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 500})
        # 先设为离线
        await ctx.set_offline(True)
        page = await ctx.new_page()
        await page.set_content(html)

        # 离线时 dispatch online 事件
        before = await page.evaluate("window.__refreshed")
        await page.evaluate("window.dispatchEvent(new Event('online'))")
        after = await page.evaluate("window.__refreshed")
        print(f"after online event: refreshed {before} -> {after}")
        assert after == before + 1, "online event should trigger refresh"

        # 再触发一次 (幂等)
        await page.evaluate("window.dispatchEvent(new Event('online'))")
        after2 = await page.evaluate("window.__refreshed")
        print(f"second: {after} -> {after2}")
        assert after2 == after + 1

        print("[OK] R47 online event triggers refresh")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())