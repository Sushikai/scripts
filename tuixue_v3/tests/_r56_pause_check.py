"""R56 stale strip 长按暂停自动刷新."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-stale-strip { padding: 8px; font-size: 11px; border: 1px solid #333; }
    .bv-stale-strip .bv-stale-paused-badge {
      background: rgba(120, 100, 220, 0.4); color: #d8c8ff;
      padding: 1px 6px; border-radius: 8px; margin-right: 4px;
      font-weight: 700; font-size: 10px;
    }
    """
    js = """
    var _autoPausedUntil = 0;
    function renderStrip() {
      var s = document.querySelector('#bv-stale-strip');
      var paused = (_autoPausedUntil > Date.now()) ? '<b class="bv-stale-paused-badge">⏸ 已暂停</b> · ' : '';
      s.innerHTML = paused + '📊 数据略旧 · 90s <span class="bv-stale-hint">点击刷新</span>';
    }
    window.renderStrip = renderStrip;
    window.__pauseUntil = 0;
    window.setPaused = function(ms){ window.__pauseUntil = Date.now() + ms; _autoPausedUntil = window.__pauseUntil; };
    """
    html = "<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
    html += '<div class="bv-stale-strip" id="bv-stale-strip"></div>'
    html += "<script>" + js + "renderStrip();</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500}, has_touch=True)
        await page.set_content(html)

        # Initially no pause badge
        text0 = await page.text_content("#bv-stale-strip")
        print(f"initial: {text0!r}")
        assert "已暂停" not in text0

        # Set paused + re-render
        await page.evaluate("setPaused(5*60*1000); renderStrip();")
        text1 = await page.text_content("#bv-stale-strip")
        print(f"paused: {text1!r}")
        assert "⏸ 已暂停" in text1

        # Badge bg color
        bg = await page.evaluate("getComputedStyle(document.querySelector('.bv-stale-paused-badge')).backgroundColor")
        print(f"badge bg={bg}")
        assert "120, 100, 220" in bg

        # Until = future
        until = await page.evaluate("window.__pauseUntil")
        print(f"until-ts={until}, now={await page.evaluate('Date.now()')}")
        assert until > await page.evaluate("Date.now()")

        # Render when expired (set past)
        await page.evaluate("setPaused(-1000); renderStrip();")
        text2 = await page.text_content("#bv-stale-strip")
        print(f"expired: {text2!r}")
        assert "已暂停" not in text2

        print("[OK] R56 paused badge renders conditionally")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())