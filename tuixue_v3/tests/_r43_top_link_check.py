"""R43 end-top link."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-loadmore-end { padding: 16px; text-align: center; font-size: 11px; }
    .bv-loadmore-end .bv-end-top { color: #00f0ff; font-weight: 600; cursor: pointer; margin-left: 4px; }
    .bv-loadmore-end .bv-end-top:hover { text-decoration: underline; }
    """
    js = """
    window.__scrollY = 0;
    function renderEnd(total) {
      var end = document.querySelector('#bv-loadmore-end');
      end.innerHTML = '已加载全部 ' + total + ' 只 · <a class="bv-end-top" href="javascript:void(0)">↑ 返回顶部</a>';
      end.hidden = false;
      var topLink = end.querySelector('.bv-end-top');
      topLink.dataset.bvClickable = '1';
      topLink.addEventListener('click', function(){
        window.scrollTo({top: 0, behavior: 'smooth'});
        window.__scrollY = 0;
      });
    }
    window.__render = renderEnd;
    """
    html = f"""<!DOCTYPE html><html><head><style>{css}</style></head>
<body style="height: 3000px">
<div style="height: 2900px"></div>
<div class="bv-loadmore-end" id="bv-loadmore-end" hidden></div>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        await page.evaluate("window.__render(45)")
        text = await page.text_content("#bv-loadmore-end")
        print(f"text={text!r}")
        assert "已加载全部 45 只" in text
        assert "返回顶部" in text

        # Check color
        color = await page.evaluate("getComputedStyle(document.querySelector('.bv-end-top')).color")
        print(f"link color={color}")
        assert "rgb(0, 240, 255)" in color or "00f0ff" in color.lower()

        # Click → scrolls to top
        await page.evaluate("window.scrollTo(0, 1000)")
        await page.wait_for_timeout(50)
        # Use evaluate to call scroll directly (avoid Playwright's auto-scroll-into-view)
        await page.evaluate("document.querySelector('.bv-end-top').click()")
        await page.wait_for_timeout(700)  # wait for smooth scroll
        new_y = await page.evaluate("window.scrollY")
        print(f"after click: scrollY={new_y}")
        assert new_y < 100, f"should scroll to top, got {new_y}"

        # Cursor pointer
        cursor = await page.evaluate("getComputedStyle(document.querySelector('.bv-end-top')).cursor")
        print(f"cursor={cursor}")
        assert cursor == "pointer"

        print("[OK] R43 end-top link works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())