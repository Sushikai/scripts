"""R35 age tick updates strip every second."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-stale-strip { padding: 8px 12px; }
    .bv-stale-strip[hidden] { display: none !important; }
    .bv-stale-strip.is-stale { color: #ffb547; border: 1px solid rgba(255,181,71,0.4); }
    .bv-stale-strip.is-very-stale { color: #ff6b6b; border: 1px solid rgba(255,107,107,0.4); }
    """
    js = """
    var _dataTs = 0;
    var _ageTick = null;
    function renderStrip() {
      var strip = document.querySelector('#bv-stale-strip');
      if (!strip) return;
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
          if (!_ageTick) {
            _ageTick = setInterval(function(){
              var s = document.querySelector('#bv-stale-strip');
              if (!s || s.hidden || !_dataTs) return;
              var a = Math.floor(Date.now() / 1000) - _dataTs;
              if (a <= 60) {
                s.hidden = true; s.textContent = '';
                if (_ageTick) { clearInterval(_ageTick); _ageTick = null; }
                return;
              }
              if (a > 300) {
                s.className = 'bv-stale-strip is-very-stale';
                s.textContent = '⚠️ 快照已 ' + Math.floor(a/60) + ' 分钟前 — 数据陈旧, 点击刷新';
              } else {
                s.className = 'bv-stale-strip is-stale';
                s.textContent = '⚠️ 快照 ' + a + ' 秒前 — 数据已陈旧, 点击刷新';
              }
            }, 1000);
          }
        } else {
          strip.hidden = true; strip.className = 'bv-stale-strip'; strip.textContent = '';
          if (_ageTick) { clearInterval(_ageTick); _ageTick = null; }
        }
      }
    }
    window.__setAge = function(sec) {
      _dataTs = sec > 0 ? Math.floor(Date.now() / 1000) - sec : 0;
      renderStrip();
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

        # Set age to 120s
        await page.evaluate("window.__setAge(120)")
        text1 = await page.text_content("#bv-stale-strip")
        print(f"t=0: {text1!r}")
        assert "120" in text1

        # Wait 2 seconds, check tick
        await page.wait_for_timeout(2200)
        text2 = await page.text_content("#bv-stale-strip")
        print(f"t=2: {text2!r}")
        assert "122" in text2, f"age should tick from 120 to ~122, got {text2!r}"

        # Wait 3 more seconds
        await page.wait_for_timeout(3100)
        text3 = await page.text_content("#bv-stale-strip")
        print(f"t=5: {text3!r}")
        assert "125" in text3, f"age should be ~125, got {text3!r}"

        # Verify tick stops when fresh (set age to 30s)
        await page.evaluate("window.__setAge(30)")
        hidden = await page.get_attribute("#bv-stale-strip", "hidden")
        print(f"30s hidden={hidden!r}")
        assert hidden is not None, "strip should be hidden when fresh"

        print("[OK] R35 age tick works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())