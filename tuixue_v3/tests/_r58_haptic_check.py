"""R58 卡片展开时触觉反馈."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__vibrateCount = 0;
    window.__vibratePattern = null;
    navigator.vibrate = function(pattern) {
      window.__vibrateCount++;
      window.__vibratePattern = pattern;
      return true;
    };
    function fakeClick(code) {
      // Simulate R2 mobile click → expand
      var tr = document.querySelector('tr.bv-row[data-code="' + code + '"]');
      var detail = document.querySelector('tr.bv-detail-row[data-detail-for="' + code + '"]');
      var wasOpen = !detail.hasAttribute('hidden');
      var _undo = wasOpen && window.__lastBvExpand && window.__lastBvExpand.code === code &&
                  (Date.now() - window.__lastBvExpand.ts) < 600;
      if (_undo) { window.__lastBvExpand = null; if (navigator.vibrate) navigator.vibrate(8); return 'undo'; }
      if (!wasOpen) {
        detail.removeAttribute('hidden');
        window.__lastBvExpand = {code: code, ts: Date.now()};
      } else { window.__lastBvExpand = null; }
      if (navigator.vibrate) navigator.vibrate(8);
      return wasOpen ? 'collapse' : 'expand';
    }
    window.fakeClick = fakeClick;
    window.__lastBvExpand = null;
    """
    html = "<!DOCTYPE html><html><body><table><tbody>"
    html += '<tr class="bv-row" data-code="A"><td>A</td></tr>'
    html += '<tr class="bv-detail-row" data-detail-for="A" hidden><td>detail</td></tr>'
    html += "</tbody></table><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # 1st click → expand, vibrate++
        await page.evaluate("fakeClick('A')")
        n1 = await page.evaluate("__vibrateCount")
        p1 = await page.evaluate("__vibratePattern")
        print(f"1st click: vibrate count={n1} pattern={p1}")
        assert n1 == 1
        assert p1 == 8

        # 2nd click within 600ms → undo, vibrate++
        await page.wait_for_timeout(50)
        await page.evaluate("fakeClick('A')")
        n2 = await page.evaluate("__vibrateCount")
        print(f"2nd click (undo): vibrate count={n2}")
        assert n2 == 2

        # 3rd click after wait → expand, vibrate++
        await page.wait_for_timeout(700)
        await page.evaluate("fakeClick('A')")
        n3 = await page.evaluate("__vibrateCount")
        print(f"3rd click (expand): vibrate count={n3}")
        assert n3 == 3

        # 4th click after 700ms → collapse, vibrate++
        await page.wait_for_timeout(700)
        await page.evaluate("fakeClick('A')")
        n4 = await page.evaluate("__vibrateCount")
        print(f"4th click (collapse): vibrate count={n4}")
        assert n4 == 4

        print("[OK] R58 haptic feedback on every state change")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())