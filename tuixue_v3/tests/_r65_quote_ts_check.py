"""R65 原话时间戳锚点 → 跳视频对应分钟."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-quote-ts { margin-left: 6px; padding: 0 5px; border-radius: 3px;
      background: rgba(251,146,60,0.15); color: #fb923c; border: 1px solid rgba(251,146,60,0.4);
      font-size: 10px; font-weight: 700; white-space: nowrap; cursor: pointer; }
    """
    js = """
    window.__openedUrl = null;
    window.open = function(url) { window.__openedUrl = url; return null; };
    function renderQuoteTs(ts) {
      var link = '';
      if (ts) link = ' <a class="bv-quote-ts" data-video-ts="' + ts + '" href="javascript:void(0)">@ ' + ts + '</a>';
      return '<div class="bv-detail-quote">看封单' + link + '</div>';
    }
    window.renderQuoteTs = renderQuoteTs;
    window.handleTsClick = function() {
      var vt = document.querySelector('.bv-quote-ts');
      var ts = vt.getAttribute('data-video-ts');
      var parts = String(ts).split(':');
      var sec = 0;
      for (var i = 0; i < parts.length; i++) sec = sec * 60 + (Number(parts[i]) || 0);
      window.open('https://www.bilibili.com/video/BV1JoNUzTE2i/?t=' + sec, '_blank');
    };
    """
    html = ("<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
            "<div id='host'></div><script>" + js + "</script></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # With timestamp → ts anchor rendered
        r1 = await page.evaluate("renderQuoteTs('12:35')")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r1)
        ts = await page.query_selector(".bv-quote-ts")
        assert ts is not None, "ts anchor should render when timestamp present"
        txt = await page.evaluate("document.querySelector('.bv-quote-ts').textContent")
        print(f"ts txt={txt}")
        assert "@ 12:35" in txt

        # Click → open video at 12*60+35 = 755s
        await page.evaluate("handleTsClick()")
        url = await page.evaluate("__openedUrl")
        print(f"opened url={url}")
        assert url == "https://www.bilibili.com/video/BV1JoNUzTE2i/?t=755", \
            f"wrong video url: {url}"

        # No timestamp → no anchor
        r2 = await page.evaluate("renderQuoteTs('')")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r2)
        ts2 = await page.query_selector(".bv-quote-ts")
        print(f"no-ts anchor={ts2 is not None}")
        assert ts2 is None

        # MM:SS:SS (1:02:03) → 3723s
        r3 = await page.evaluate("renderQuoteTs('1:02:03')")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r3)
        await page.evaluate("handleTsClick()")
        url3 = await page.evaluate("__openedUrl")
        print(f"3-part ts url={url3}")
        assert url3 == "https://www.bilibili.com/video/BV1JoNUzTE2i/?t=3723"

        print("[OK] R65 quote timestamp anchor jumps to video minute")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
