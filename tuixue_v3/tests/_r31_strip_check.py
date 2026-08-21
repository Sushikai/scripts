"""R31 stale strip."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-stale-strip[hidden] { display: none !important; }
    .bv-stale-strip.is-stale { color: #ffb547; border: 1px solid rgba(255,181,71,0.4); padding: 8px; }
    .bv-stale-strip.is-very-stale { color: #ff6b6b; border: 1px solid rgba(255,107,107,0.4); padding: 8px; }
    #bv-pick-count { font-size: 12px; padding: 4px 0; cursor: pointer; }
    """
    js = """
    var _phase = 'early', _dataTs = 0, _picks = [{code: 'X'}];
    function renderPicks() {
      var count = document.querySelector('#bv-pick-count');
      var strip = document.querySelector('#bv-stale-strip');
      if (_dataTs > 0) {
        var age = Math.floor(Date.now() / 1000) - _dataTs;
        if (age > 60) {
          strip.hidden = false;
          if (age > 300) {
            strip.className = 'bv-stale-strip is-very-stale';
            strip.textContent = '⚠️ 快照已 ' + Math.floor(age/60) + ' 分钟前 — 数据陈旧';
          } else {
            strip.className = 'bv-stale-strip is-stale';
            strip.textContent = '⚠️ 快照 ' + age + ' 秒前 — 数据已陈旧';
          }
        } else {
          strip.hidden = true; strip.className = 'bv-stale-strip'; strip.textContent = '';
        }
      } else {
        strip.hidden = true; strip.className = 'bv-stale-strip'; strip.textContent = '';
      }
    }
    window.__setAge = function(sec) {
      _dataTs = sec > 0 ? Math.floor(Date.now() / 1000) - sec : 0;
      renderPicks();
    };
    """
    html = f"""<!DOCTYPE html><html><head><style>{css}</style></head>
<body>
<section class="view-bv">
  <h3>🎯 实时推票 <span id="bv-pick-count"></span></h3>
  <div class="bv-stale-strip" id="bv-stale-strip" hidden></div>
</section>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(html)

        # 30s — strip hidden
        await page.evaluate("window.__setAge(30)")
        hidden = await page.get_attribute("#bv-stale-strip", "hidden")
        text = await page.text_content("#bv-stale-strip")
        print(f"30s: hidden={hidden!r} text={text!r}")
        assert hidden is not None, "30s strip should be hidden"
        assert text == "" or text is None

        # 120s — stale
        await page.evaluate("window.__setAge(120)")
        hidden = await page.get_attribute("#bv-stale-strip", "hidden")
        cls = await page.get_attribute("#bv-stale-strip", "class")
        text = await page.text_content("#bv-stale-strip")
        print(f"120s: hidden={hidden!r} cls={cls!r} text={text!r}")
        assert hidden is None, "120s strip should be visible"
        assert "is-stale" in cls
        assert "120" in text

        # 600s — very stale
        await page.evaluate("window.__setAge(600)")
        cls = await page.get_attribute("#bv-stale-strip", "class")
        text = await page.text_content("#bv-stale-strip")
        print(f"600s: cls={cls!r} text={text!r}")
        assert "is-very-stale" in cls
        assert "分钟" in text

        # back to fresh
        await page.evaluate("window.__setAge(10)")
        hidden = await page.get_attribute("#bv-stale-strip", "hidden")
        text = await page.text_content("#bv-stale-strip")
        print(f"10s: hidden={hidden!r} text={text!r}")
        assert hidden is not None

        await page.evaluate("window.__setAge(120)")
        await page.screenshot(path="/tmp/bv_r31_strip.png", full_page=True)
        print("[OK] R31 strip works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())