"""R38 strip shows remaining auto-refresh countdown."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    // Mock _autoRefresh
    var _autoRefresh = {
      remaining: function(name) {
        if (name === 'bv-live') return 17;
        return null;
      },
      interval: function(name) {
        if (name === 'bv-live') return 30;
        return null;
      }
    };
    var _dataTs = 0;
    function renderStrip(age) {
      var s = document.querySelector('#bv-stale-strip');
      s.hidden = false;
      if (age > 300) {
        s.className = 'bv-stale-strip is-very-stale';
        s.innerHTML = '📉 <b>数据已陈旧</b> · ' + Math.floor(age/60) + ' 分钟前 <span class="bv-stale-hint">点击刷新</span>';
      } else {
        s.className = 'bv-stale-strip is-stale';
        var remTxt = '';
        if (typeof _autoRefresh !== 'undefined' && _autoRefresh.remaining) {
          var rem = _autoRefresh.remaining('bv-live');
          if (rem !== null && rem !== undefined) remTxt = ' · 下次刷新 ' + rem + 's';
        }
        s.innerHTML = '📊 <b>数据略旧</b> · ' + age + 's' + remTxt + ' <span class="bv-stale-hint">点击刷新</span>';
      }
    }
    window.__render = renderStrip;
    """
    html = f"""<!DOCTYPE html><html><body>
<div class="bv-stale-strip" id="bv-stale-strip" hidden></div>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 200})
        await page.set_content(html)

        # 120s → should show remaining 17s
        await page.evaluate("window.__render(120)")
        html1 = await page.inner_html("#bv-stale-strip")
        text1 = await page.text_content("#bv-stale-strip")
        print(f"120s: {html1!r}")
        assert "120s" in text1
        assert "下次刷新" in text1
        assert "17s" in text1

        # 600s → very stale, no remaining
        await page.evaluate("window.__render(600)")
        html2 = await page.inner_html("#bv-stale-strip")
        text2 = await page.text_content("#bv-stale-strip")
        print(f"600s: {html2!r}")
        assert "数据已陈旧" in text2
        assert "下次刷新" not in text2  # very stale omits remaining

        print("[OK] R38 remaining countdown shown")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())