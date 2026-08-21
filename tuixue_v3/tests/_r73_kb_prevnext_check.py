"""R73 键盘 ←/→ = 详情 prev/next, 复用同一 _lastSortedCodes 顺序."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__lastBvExpand = null;
    var tbody = document.querySelector('tbody');
    var _lastSortedCodes = ['A', 'B', 'C'];
    window._lastSortedCodes = _lastSortedCodes;
    var _kbIdx = 1;  // focused on B (index 1)
    function handleKey(key) {
      var rows = tbody.querySelectorAll('tr.bv-row');
      var _kbCur = _kbIdx >= 0 && _kbIdx < rows.length ? rows[_kbIdx].dataset.code : null;
      if (!_kbCur) return null;
      var _kbNavIdx = _lastSortedCodes.indexOf(_kbCur);
      var _kbTarget = _kbNavIdx + (key === 'ArrowRight' ? 1 : -1);
      if (_kbTarget >= 0 && _kbTarget < _lastSortedCodes.length) {
        var _kbTgtCode = _lastSortedCodes[_kbTarget];
        tbody.querySelectorAll('tr.bv-detail-row:not([hidden])').forEach(function(d){ d.setAttribute('hidden',''); });
        tbody.querySelectorAll('tr.bv-row.bv-expanded').forEach(function(r){ r.classList.remove('bv-expanded'); });
        var _kbTgtRow = tbody.querySelector('tr.bv-row[data-code="' + _kbTgtCode + '"]');
        var _kbTgtDetail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + _kbTgtCode + '"]');
        if (_kbTgtRow) { _kbTgtRow.classList.add('bv-expanded'); _kbTgtRow.classList.add('bv-kb-focus'); }
        if (_kbTgtDetail) _kbTgtDetail.removeAttribute('hidden');
        _kbIdx = _kbTarget;  // R73: 焦点索引跟随目标 (与真实实现一致)
        return _kbTgtCode;
      }
      return null;
    }
    window.handleKey = handleKey;
    function expandedSet() {
      var s = {};
      document.querySelectorAll('tr.bv-row').forEach(function(r){ s[r.dataset.code] = r.classList.contains('bv-expanded'); });
      return s;
    }
    window.expandedSet = expandedSet;
    """
    html = "<!DOCTYPE html><html><body><table><tbody>"
    for c in ['A', 'B', 'C']:
        html += '<tr class="bv-row" data-code="' + c + '"><td>' + c + '</td></tr>'
        html += '<tr class="bv-detail-row" data-detail-for="' + c + '" hidden><td>detail</td></tr>'
    html += "</tbody></table><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # B focused → ArrowRight → C expanded
        r1 = await page.evaluate("handleKey('ArrowRight')")
        s1 = await page.evaluate("expandedSet()")
        print(f"Right from B: {r1}, states={s1}")
        assert r1 == "C"
        assert s1["C"] == True and s1["B"] == False

        # ArrowLeft from C → B
        await page.evaluate("handleKey('ArrowLeft')")
        s2 = await page.evaluate("expandedSet()")
        print(f"Left from C: states={s2}")
        assert s2["B"] == True and s2["C"] == False

        # Focused row keeps bv-kb-focus on target
        kb = await page.evaluate("document.querySelector('.bv-kb-focus') ? document.querySelector('.bv-kb-focus').dataset.code : null")
        print(f"kb focus on: {kb}")
        assert kb == "B"

        print("[OK] R73 keyboard arrows reuse prev/next order")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
