"""R61 top + 当前 双卡展开."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__lastBvExpand = null;
    var tbody = document.querySelector('tbody');
    function expand(code, isTop) {
      var tr = tbody.querySelector('tr.bv-row[data-code="' + code + '"]');
      var detail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + code + '"]');
      // Close all first
      tbody.querySelectorAll('tr.bv-detail-row:not([hidden])').forEach(function(d){ d.setAttribute('hidden',''); });
      tbody.querySelectorAll('tr.bv-row.bv-expanded').forEach(function(r){ r.classList.remove('bv-expanded'); });
      var _topReopen = (code === 'A' && isTop);
      var _undo = false;
      var wasOpen = !detail.hasAttribute('hidden');
      var _now = Date.now();
      var _undo = wasOpen && !_topReopen && window.__lastBvExpand && window.__lastBvExpand.code === code &&
                  (_now - window.__lastBvExpand.ts) < 600;
      if (_undo) { window.__lastBvExpand = null; return 'undo'; }
      if (!wasOpen || _topReopen) {
        detail.removeAttribute('hidden');
        tr.classList.add('bv-expanded');
        // R61: top-1 + 当前双卡
        var firstRow = tbody.firstElementChild;
        if (firstRow && firstRow !== tr && firstRow.classList.contains('is-bv-top')) {
          var td = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + firstRow.dataset.code + '"]');
          if (td) td.removeAttribute('hidden');
          firstRow.classList.add('bv-expanded');
        }
        window.__lastBvExpand = {code: code, ts: _now};
        return 'expand';
      }
      return 'collapse';
    }
    window.expand = expand;
    function states() {
      var rows = {};
      document.querySelectorAll('tr.bv-row').forEach(function(r){
        rows[r.dataset.code] = r.classList.contains('bv-expanded');
      });
      return rows;
    }
    window.states = states;
    """
    html = "<!DOCTYPE html><html><body><table><tbody>"
    html += '<tr class="bv-row is-bv-top" data-code="A"><td>A</td></tr>'
    html += '<tr class="bv-detail-row" data-detail-for="A"><td>detail A</td></tr>'
    html += '<tr class="bv-row" data-code="B"><td>B</td></tr>'
    html += '<tr class="bv-detail-row" data-detail-for="B" hidden><td>detail B</td></tr>'
    html += '<tr class="bv-row" data-code="C"><td>C</td></tr>'
    html += '<tr class="bv-detail-row" data-detail-for="C" hidden><td>detail C</td></tr>'
    html += "</tbody></table><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Expand B → top A + B both visible
        r = await page.evaluate("expand('B', false)")
        s1 = await page.evaluate("states()")
        print(f"expand B: {r}, states={s1}")
        assert r == "expand"
        assert s1["A"] == True, "top A should stay expanded"
        assert s1["B"] == True, "B should be expanded"
        assert s1["C"] == False

        # Expand C → top A + C, B collapsed
        await page.evaluate("expand('C', false)")
        s2 = await page.evaluate("states()")
        print(f"expand C: states={s2}")
        assert s2["A"] == True
        assert s2["B"] == False, "B should collapse (accordion)"
        assert s2["C"] == True

        # Expand A (top itself) → A + nothing else
        await page.evaluate("expand('A', true)")
        s3 = await page.evaluate("states()")
        print(f"expand A: states={s3}")
        assert s3["A"] == True
        assert s3["B"] == False
        assert s3["C"] == False

        print("[OK] R61 top + current dual-card accordion works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())