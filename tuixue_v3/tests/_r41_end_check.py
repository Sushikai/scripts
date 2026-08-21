"""R41 loadmore-end footer."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-loadmore-end { padding: 16px; font-size: 11px; text-align: center; }
    .bv-loadmore-end[hidden] { display: none !important; }
    """
    js = """
    var _picks = [];
    var _hasMore = true;
    var _end = document.querySelector('#bv-loadmore-end');
    function loadMore() {
      // simulate pagination: 15 → +15 → +15 = 45, then stop
      var before = _picks.length;
      for (var i = 0; i < 15; i++) _picks.push({code: 'X' + (before + i)});
      if (_picks.length >= 45) _hasMore = false;
    }
    function maybeShowEnd() {
      if (!_hasMore && _picks.length > 15) {
        _end.textContent = '已加载全部 ' + _picks.length + ' 只';
        _end.hidden = false;
      } else {
        _end.hidden = true;
      }
    }
    window.__loadMore = function(){
      loadMore();
      maybeShowEnd();
    };
    window.__reset = function(){
      _picks = [];
      _hasMore = true;
      _end.hidden = true;
    };
    """
    html = f"""<!DOCTYPE html><html><head><style>{css}</style></head>
<body>
<div class="bv-loadmore-end" id="bv-loadmore-end" hidden></div>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 600})
        await page.set_content(html)

        # Initial: hidden
        hidden = await page.get_attribute("#bv-loadmore-end", "hidden")
        assert hidden is not None

        # 1st batch (15 items) — still hidden
        await page.evaluate("window.__loadMore()")
        hidden1 = await page.get_attribute("#bv-loadmore-end", "hidden")
        assert hidden1 is not None, "after 1st batch footer should still be hidden"

        # 2nd batch (30 items) — still hidden
        await page.evaluate("window.__loadMore()")
        hidden2 = await page.get_attribute("#bv-loadmore-end", "hidden")
        assert hidden2 is not None, "after 2nd batch (30 items) footer should be hidden (only show on exhaustion)"

        # 3rd batch (45 items) — exhausted, footer shows
        await page.evaluate("window.__loadMore()")
        hidden3 = await page.get_attribute("#bv-loadmore-end", "hidden")
        text3 = await page.text_content("#bv-loadmore-end")
        assert hidden3 is None, "after exhaustion footer should be visible"
        assert "45" in text3
        print(f"after exhaustion: text={text3!r}")

        # Reset → hidden again
        await page.evaluate("window.__reset()")
        hidden4 = await page.get_attribute("#bv-loadmore-end", "hidden")
        assert hidden4 is not None

        print("[OK] R41 loadmore-end footer works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())