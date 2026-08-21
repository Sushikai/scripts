"""R70 详情 prev/next 切换 — 同高度展开相邻 pick."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__lastBvExpand = null;
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
        return navTgtCode;
      }
      return null;
    }
    window.navClick = navClick;
    function expanded() {
      var r = {};
      document.querySelectorAll('tr.bv-row').forEach(function(x){ r[x.dataset.code] = x.classList.contains('bv-expanded'); });
      return r;
    }
    window.expanded = expanded;
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

        # Open B
        await page.evaluate("openDetail('B')")
        s0 = await page.evaluate("expanded()")
        assert s0["B"] == True

        # Next from B → C expanded, B collapsed
        r1 = await page.evaluate("navClick('B', 1)")
        s1 = await page.evaluate("expanded()")
        print(f"next B→: {r1}, states={s1}")
        assert r1 == "C"
        assert s1["C"] == True and s1["B"] == False and s1["A"] == False

        # Prev from C → B expanded, C collapsed
        r2 = await page.evaluate("navClick('C', -1)")
        s2 = await page.evaluate("expanded()")
        print(f"prev C←: {r2}, states={s2}")
        assert r2 == "B"
        assert s2["B"] == True and s2["C"] == False

        # Next from C (last) → null, no change
        r3 = await page.evaluate("navClick('C', 1)")
        s3 = await page.evaluate("expanded()")
        print(f"next C(last): {r3}")
        assert r3 is None

        # Prev from A (first) → null
        r4 = await page.evaluate("navClick('A', -1)")
        print(f"prev A(first): {r4}")
        assert r4 is None

        # Target detail row visible
        vis = await page.evaluate("!document.querySelector('tr.bv-detail-row[data-detail-for=\"B\"]').hasAttribute('hidden')")
        assert vis == True, "B detail should be visible after nav back to B"

        print("[OK] R70 prev/next switch between adjacent picks in place")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
