"""R71 prev/next 切换后自动滚到目标卡可见."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__scrollCalls = [];
    window.__navScroll = null;
    Element.prototype.scrollIntoView = function(opts) {
      window.__scrollCalls.push({tag: this.getAttribute('data-code'), opts: opts});
    };
    var tbody = document.querySelector('tbody');
    var _lastSortedCodes = ['A', 'B', 'C'];
    window._lastSortedCodes = _lastSortedCodes;
    function navClick(fromCode, dir) {
      var navIdx = _lastSortedCodes.indexOf(fromCode);
      var navTarget = navIdx + dir;
      if (navTarget >= 0 && navTarget < _lastSortedCodes.length) {
        var navTgtCode = _lastSortedCodes[navTarget];
        var openD = tbody.querySelector('tr.bv-detail-row:not([hidden])');
        if (openD) openD.setAttribute('hidden', '');
        var tgtRow = tbody.querySelector('tr.bv-row[data-code="' + navTgtCode + '"]');
        var tgtDetail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + navTgtCode + '"]');
        if (tgtRow) {
          tbody.querySelectorAll('tr.bv-row.bv-expanded').forEach(function(r){ r.classList.remove('bv-expanded'); });
          tgtRow.classList.add('bv-expanded');
        }
        if (tgtDetail) tgtDetail.removeAttribute('hidden');
        window.__lastBvExpand = {code: navTgtCode, ts: Date.now()};
        if (tgtRow) {
          // R71: 切到新卡自动滚到可见
          var rect = tgtRow.getBoundingClientRect();
          window.__navRect = {top: rect.top, bottom: rect.bottom};
          if (rect.top < 0 || rect.bottom > (window.innerHeight || 500)) {
            tgtRow.scrollIntoView({block: 'nearest', behavior: 'smooth'});
          }
        }
        return navTgtCode;
      }
      return null;
    }
    window.navClick = navClick;
    function setRect(code, top, bottom) {
      var tr = tbody.querySelector('tr.bv-row[data-code="' + code + '"]');
      tr.getBoundingClientRect = function() { return {top: top, bottom: bottom}; };
    }
    window.setRect = setRect;
    function openDetail(code) {
      document.querySelector('tr.bv-detail-row[data-detail-for="' + code + '"]').removeAttribute('hidden');
      document.querySelector('tr.bv-row[data-code="' + code + '"]').classList.add('bv-expanded');
    }
    window.openDetail = openDetail;
    """
    html = "<!DOCTYPE html><html><body><table><tbody>"
    for c in ['A', 'B', 'C']:
        html += '<tr class="bv-row" data-code="' + c + '"><td>' + c + '</td></tr>'
        html += '<tr class="bv-detail-row" data-detail-for="' + c + '" hidden><td>detail ' + c + '</td></tr>'
    html += "</tbody></table><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # B expanded; C is below viewport (bottom 900 > 500) → nav should scroll C
        await page.evaluate("openDetail('B')")
        await page.evaluate("setRect('B', 200, 280)")
        await page.evaluate("setRect('C', 700, 900)")
        await page.evaluate("navClick('B', 1)")
        calls = await page.evaluate("__scrollCalls")
        print(f"scroll calls={calls}")
        assert len(calls) == 1, "should scroll once to target"
        assert calls[0]["tag"] == "C", "scroll target should be C"
        assert calls[0]["opts"]["block"] == "nearest"
        assert calls[0]["opts"]["behavior"] == "smooth"

        # B expanded; C already visible (bottom 300 < 500) → NO scroll
        await page.evaluate("setRect('C', 200, 300)")
        await page.evaluate("navClick('B', 1)")
        calls2 = await page.evaluate("__scrollCalls")
        print(f"visible: scroll calls={len(calls2)}")
        assert len(calls2) == 1, "no scroll when target already visible"

        print("[OK] R71 nav scrolls target into view only when offscreen")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
