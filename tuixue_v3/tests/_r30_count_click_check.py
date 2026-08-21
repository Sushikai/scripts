"""R30 count click → refresh."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .view-bv { display: block; padding: 12px; background: #0f0f12; color: #ddd; font-family: sans-serif; }
    #bv-pick-count { font-size: 12px; color: #aaa; padding: 4px 0; cursor: pointer; }
    #bv-pick-count[data-bv-clickable="1"]:hover { text-decoration: underline dashed; }
    #bv-pick-count.is-stale { color: #ffb547; }
    """
    js = """
    window.__clickCount = 0;
    window.__bv = { refresh: function() { window.__clickCount++; } };
    function renderPicks() {
      var count = document.querySelector('#bv-pick-count');
      count.textContent = '(扫描 ≥1 / 命中 1 · 🟢早盘 快照 12:30)';
      if (!count.dataset.bvClickable) {
        count.dataset.bvClickable = '1';
        count.title = '点击刷新';
        count.addEventListener('click', function(){
          if (window.__bv && window.__bv.refresh) window.__bv.refresh(true);
        });
      }
    }
    renderPicks();
    """
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
<section class="view-bv">
  <div id="bv-pick-count">(加载中)</div>
</section>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(html)
        await page.evaluate("renderPicks()")

        # verify clickable flag set
        clickable = await page.get_attribute("#bv-pick-count", "data-bv-clickable")
        print(f"clickable={clickable!r}")
        assert clickable == "1", "clickable flag should be set"

        # verify cursor
        cursor = await page.evaluate("getComputedStyle(document.querySelector('#bv-pick-count')).cursor")
        print(f"cursor={cursor!r}")
        assert cursor == "pointer", "cursor should be pointer"

        # verify title
        title = await page.get_attribute("#bv-pick-count", "title")
        print(f"title={title!r}")
        assert title == "点击刷新"

        # verify click triggers refresh
        before = await page.evaluate("window.__clickCount")
        print(f"initial clickCount = {before}")
        await page.click("#bv-pick-count")
        await page.wait_for_timeout(50)
        cnt = await page.evaluate("window.__clickCount")
        print(f"clickCount after 1 click = {cnt}")
        assert cnt == 1, f"expected 1 click, got {cnt}"

        # second click
        await page.click("#bv-pick-count")
        await page.wait_for_timeout(50)
        cnt2 = await page.evaluate("window.__clickCount")
        print(f"clickCount after 2 clicks = {cnt2}")
        assert cnt2 == 2, f"expected 2, got {cnt2}"

        # styles applied
        has_hover_rule = await page.evaluate("""
          (function(){
            var sheets = document.styleSheets;
            for (var i = 0; i < sheets.length; i++) {
              try {
                var rules = sheets[i].cssRules;
                for (var j = 0; j < rules.length; j++) {
                  if (rules[j].selectorText && rules[j].selectorText.indexOf('hover') >= 0) return true;
                }
              } catch(e) {}
            }
            return false;
          })()
        """)
        print(f"has hover rule: {has_hover_rule}")
        assert has_hover_rule

        print("[OK] R30 count click → refresh works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
