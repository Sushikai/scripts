"""R40 loadmore indicator."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-loadmore { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 14px; font-size: 12px; }
    .bv-loadmore[hidden] { display: none !important; }
    .bv-loadmore-spin { width: 12px; height: 12px; border: 2px solid #888; border-top-color: transparent; border-radius: 50%; animation: bv-loadmore-spin 0.8s linear infinite; }
    @keyframes bv-loadmore-spin { to { transform: rotate(360deg); } }
    """
    js = """
    var _picks = [{code:'A'},{code:'B'},{code:'C'}];
    var _loadingMore = false;
    function loadLivePick() {
      _loadingMore = true;
      var lm = document.querySelector('#bv-loadmore');
      if (lm) lm.hidden = false;
      return new Promise(function(resolve){
        setTimeout(function(){
          _picks.push({code:'D'},{code:'E'});
          _loadingMore = false;
          resolve();
        }, 500);
      }).then(function(){
        if (lm) lm.hidden = true;
      });
    }
    function onScroll(){
      if (_loadingMore || !_picks.length) return;
      var sc = window.scrollY;
      var vh = window.innerHeight;
      var docH = document.documentElement.scrollHeight;
      if (sc + vh >= docH - 200) loadLivePick();
    }
    window.addEventListener('scroll', onScroll, {passive: true });
    """
    html = f"""<!DOCTYPE html><html><head><style>{css}</style></head>
<body style="height: 3000px">
<div style="height: 2800px">space</div>
<div class="bv-loadmore" id="bv-loadmore" hidden>
  <span class="bv-loadmore-spin"></span>
  <span>加载中…</span>
</div>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 600})
        await page.set_content(html)
        await page.wait_for_timeout(50)

        # Initially hidden
        hidden = await page.get_attribute("#bv-loadmore", "hidden")
        print(f"initial hidden={hidden!r}")
        assert hidden is not None, "loadmore should be initially hidden"

        # scroll to bottom
        await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        await page.wait_for_timeout(50)
        # Should be visible now (loadLivePick in progress)
        hidden2 = await page.get_attribute("#bv-loadmore", "hidden")
        text = await page.text_content("#bv-loadmore")
        print(f"after scroll hidden={hidden2!r} text={text!r}")
        assert hidden2 is None, "loadmore should be visible"
        assert "加载中" in text

        # Wait for loadLivePick to complete
        await page.wait_for_timeout(800)
        hidden3 = await page.get_attribute("#bv-loadmore", "hidden")
        print(f"after complete hidden={hidden3!r}")
        assert hidden3 is not None, "loadmore should hide after completion"

        # Verify spinner exists
        spin = await page.query_selector(".bv-loadmore-spin")
        assert spin is not None, "spinner should be present"

        print("[OK] R40 loadmore indicator works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())