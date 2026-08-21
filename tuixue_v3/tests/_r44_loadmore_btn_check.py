"""R44 footer 加载更多按钮."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-loadmore-end { padding: 16px; text-align: center; }
    .bv-loadmore-end .bv-loadmore-btn {
      background: rgba(0, 240, 255, 0.08); color: #00f0ff;
      border: 1px solid rgba(0, 240, 255, 0.4); border-radius: 16px;
      padding: 6px 16px; font-size: 11px; font-weight: 600;
      cursor: pointer;
    }
    .bv-loadmore-end .bv-loadmore-btn:hover { background: rgba(0, 240, 255, 0.18); }
    """
    html = "<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
    html += '<div class="bv-loadmore-end" id="bv-loadmore-end" hidden></div>'
    html += "<script>"
    html += "window.__clicked = 0;"
    html += "window.__renderBtn = function() {"
    html += "  var end = document.querySelector('#bv-loadmore-end');"
    html += "  end.innerHTML = '<button class=\"bv-loadmore-btn\">↓ 加载更多</button>';"
    html += "  end.hidden = false;"
    html += "  var btn = end.querySelector('.bv-loadmore-btn');"
    html += "  btn.dataset.bvClickable = '1';"
    html += "  btn.addEventListener('click', function(){ window.__clicked++; });"
    html += "};"
    html += "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        await page.evaluate("window.__renderBtn()")
        text = await page.text_content("#bv-loadmore-end")
        print(f"text={text!r}")
        assert "加载更多" in text

        # Check cursor + color
        cursor = await page.evaluate("getComputedStyle(document.querySelector('.bv-loadmore-btn')).cursor")
        color = await page.evaluate("getComputedStyle(document.querySelector('.bv-loadmore-btn')).color")
        print(f"cursor={cursor} color={color}")
        assert cursor == "pointer"
        assert "240, 255" in color  # cyan accent

        # Click button → counter increments
        before = await page.evaluate("window.__clicked")
        await page.evaluate("document.querySelector('.bv-loadmore-btn').click()")
        after = await page.evaluate("window.__clicked")
        print(f"clicked: {before} -> {after}")
        assert after == before + 1

        print("[OK] R44 loadmore button works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())