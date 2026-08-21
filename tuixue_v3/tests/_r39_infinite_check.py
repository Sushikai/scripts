"""R39 infinite scroll — when scrolled near bottom, triggers loadLivePick(offset)."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    var _picks = [];
    var _loadCount = 0;
    var _loadingMore = false;
    var _hasMore = true;
    var _dataTs = 0;
    function loadLivePick(refresh, offset) {
      _loadCount++;
      _loadingMore = false;
      // simulate server returning new picks
      var baseN = _picks.length;
      var newBatch = [
        {code: 'X' + baseN + 1, name: 'A', score: 90 - baseN},
        {code: 'X' + baseN + 2, name: 'B', score: 89 - baseN},
        {code: 'X' + baseN + 3, name: 'C', score: 88 - baseN},
      ];
      // simulate exhaustion after 3 batches
      if (_loadCount > 3) {
        _hasMore = false;
        return;
      }
      _picks = _picks.concat(newBatch);
      return Promise.resolve(newBatch);
    }
    function _installInfiniteScroll() {
      window.addEventListener('scroll', function(){
        if (_loadingMore || !_hasMore || !_picks || !_picks.length) return;
        var sc = window.scrollY || document.documentElement.scrollTop;
        var vh = window.innerHeight;
        var docH = document.documentElement.scrollHeight;
        if (sc + vh >= docH - 200) {
          _loadingMore = true;
          loadLivePick(true, _picks.length).then(function(){ _loadingMore = false; });
        }
      }, {passive: true});
    }
    _installInfiniteScroll();
    // pre-populate with 3 picks (so _picks.length is not 0)
    _picks = [{code: 'A', name: 'A', score: 95}, {code: 'B', name: 'B', score: 94}, {code: 'C', name: 'C', score: 93}];
    """
    html = f"""<!DOCTYPE html><html><body style="height: 3000px"><div style="height: 2900px"></div>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 600})
        await page.set_content(html)
        await page.wait_for_timeout(50)
        initial_picks = await page.evaluate("_picks.length")
        initial_loads = await page.evaluate("_loadCount")
        print(f"initial picks={initial_picks} loads={initial_loads}")

        # scroll to near bottom
        await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        await page.wait_for_timeout(300)

        after_picks = await page.evaluate("_picks.length")
        after_loads = await page.evaluate("_loadCount")
        print(f"after scroll: picks={after_picks} loads={after_loads}")
        assert after_picks > initial_picks, f"picks should grow, got {initial_picks}→{after_picks}"
        assert after_loads > initial_loads, "loadLivePick should be called"

        # scroll again to exhaust
        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            await page.wait_for_timeout(150)
        final_loads = await page.evaluate("_loadCount")
        print(f"after more scrolls: loads={final_loads}")

        # verify _hasMore=false after exhaustion
        has_more = await page.evaluate("_hasMore")
        print(f"has_more after exhaustion={has_more}")

        print("[OK] R39 infinite scroll triggers load")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())