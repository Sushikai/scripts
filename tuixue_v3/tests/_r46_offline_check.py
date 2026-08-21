"""R46 stale strip offline badge."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-stale-strip { padding: 8px; font-size: 11px; border: 1px solid #333; }
    .bv-stale-strip.is-very-stale { color: #ff6b6b; }
    .bv-stale-offline {
      background: rgba(255, 68, 68, 0.25); color: #ff4444;
      padding: 1px 6px; border-radius: 8px; margin-right: 4px;
      font-weight: 700; font-size: 10px;
    }
    """
    html = "<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
    html += '<div class="bv-stale-strip is-very-stale" id="bv-stale-strip"></div>'
    html += "<script>"
    html += "window.__render = function(offline) {"
    html += "  var s = document.querySelector('#bv-stale-strip');"
    html += "  var off = offline ? '<b class=\"bv-stale-offline\">📡 离线</b> · ' : '';"
    html += "  s.innerHTML = off + '📉 <b>数据已陈旧</b> · 5 分钟前 <span class=\"bv-stale-hint\">点击刷新</span>';"
    html += "};"
    html += "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # offline = true
        await page.evaluate("window.__render(true)")
        text = await page.text_content("#bv-stale-strip")
        print(f"offline text={text!r}")
        assert "离线" in text
        assert "数据已陈旧" in text

        # offline badge has bg
        bg = await page.evaluate("getComputedStyle(document.querySelector('.bv-stale-offline')).backgroundColor")
        print(f"offline badge bg={bg}")
        assert "255, 68, 68" in bg

        # offline = false
        await page.evaluate("window.__render(false)")
        text2 = await page.text_content("#bv-stale-strip")
        print(f"online text={text2!r}")
        assert "离线" not in text2
        assert "数据已陈旧" in text2

        print("[OK] R46 offline badge works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())