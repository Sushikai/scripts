"""R32 strip click → refresh."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-stale-strip { padding: 8px 12px; border-radius: 6px; font-weight: 600; }
    .bv-stale-strip[hidden] { display: none !important; }
    .bv-stale-strip.is-stale { color: #ffb547; border: 1px solid rgba(255,181,71,0.4); }
    .bv-stale-strip.is-very-stale { color: #ff6b6b; border: 1px solid rgba(255,107,107,0.4); }
    .bv-stale-strip[data-bv-clickable="1"] { cursor: pointer; }
    """
    js = """
    window.__clickCount = 0;
    window.__bv = { refresh: function() { window.__clickCount++; } };
    var _dataTs = 0;
    function renderPicks() {
      var strip = document.querySelector('#bv-stale-strip');
      if (!strip.dataset.bvClickable) {
        strip.dataset.bvClickable = '1';
        strip.title = '点击刷新';
        strip.addEventListener('click', function(){
          if (window.__bv && window.__bv.refresh) window.__bv.refresh(true);
        });
      }
      if (_dataTs > 0) {
        var age = Math.floor(Date.now() / 1000) - _dataTs;
        if (age > 60) {
          strip.hidden = false;
          if (age > 300) {
            strip.className = 'bv-stale-strip is-very-stale';
            strip.textContent = '⚠️ 快照已 ' + Math.floor(age/60) + ' 分钟前 — 数据陈旧, 点击刷新';
          } else {
            strip.className = 'bv-stale-strip is-stale';
            strip.textContent = '⚠️ 快照 ' + age + ' 秒前 — 数据已陈旧, 点击刷新';
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
<section>
  <div class="bv-stale-strip" id="bv-stale-strip" hidden></div>
</section>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(html)

        # 30s — hidden, no click
        await page.evaluate("window.__setAge(30)")
        hidden = await page.get_attribute("#bv-stale-strip", "hidden")
        assert hidden is not None, "30s strip should be hidden"

        # 120s — visible + clickable
        await page.evaluate("window.__setAge(120)")
        hidden = await page.get_attribute("#bv-stale-strip", "hidden")
        cls = await page.get_attribute("#bv-stale-strip", "class")
        cursor = await page.evaluate("getComputedStyle(document.querySelector('#bv-stale-strip')).cursor")
        assert hidden is None, "120s strip should be visible"
        assert "is-stale" in cls
        assert cursor == "pointer", f"cursor should be pointer, got {cursor!r}"
        print(f"120s cls={cls!r} cursor={cursor!r}")

        # click triggers refresh
        await page.click("#bv-stale-strip")
        await page.wait_for_timeout(50)
        cnt = await page.evaluate("window.__clickCount")
        print(f"click count after 1 click = {cnt}")
        assert cnt == 1

        # 600s — very stale
        await page.evaluate("window.__setAge(600)")
        cls = await page.get_attribute("#bv-stale-strip", "class")
        text = await page.text_content("#bv-stale-strip")
        assert "is-very-stale" in cls
        assert "分钟" in text
        print(f"600s cls={cls!r} text={text!r}")

        # very stale also clickable
        await page.click("#bv-stale-strip")
        await page.wait_for_timeout(50)
        cnt = await page.evaluate("window.__clickCount")
        print(f"click count after 2nd click = {cnt}")
        assert cnt == 2

        await page.evaluate("window.__setAge(120)")
        await page.screenshot(path="/tmp/bv_r32.png", full_page=True)
        print("[OK] R32 strip click works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())