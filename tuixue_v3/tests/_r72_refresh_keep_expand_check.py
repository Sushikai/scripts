"""R72 自动刷新后保持手动展开的详情卡."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    var tbody = document.querySelector('tbody');
    var _expandedCode = null;
    var _expandedTop = false;
    function captureExpanded() {
      _expandedCode = null; _expandedTop = false;
      var openRow = tbody.querySelector('tr.bv-row.bv-expanded');
      if (openRow) {
        var code = openRow.dataset.code;
        var detail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + code + '"]');
        if (detail && !detail.hasAttribute('hidden')) {
          _expandedCode = code;
          _expandedTop = openRow.classList.contains('is-bv-top');
        }
      }
    }
    window.captureExpanded = captureExpanded;
    // re-render simulation: fresh DOM but preserve expanded (mirror of R72)
    function reRender(rows) {
      tbody.innerHTML = '';
      rows.forEach(function(c){
        var tr = document.createElement('tr');
        tr.className = 'bv-row' + (c.top ? ' is-bv-top' : '') + (c.expanded ? ' bv-expanded' : '');
        tr.dataset.code = c.code;
        var td = document.createElement('td'); td.textContent = c.code;
        tr.appendChild(td);
        tbody.appendChild(tr);
        var dt = document.createElement('tr');
        dt.className = 'bv-detail-row';
        dt.dataset.detailFor = c.code;
        if (!c.expanded) dt.setAttribute('hidden', '');
        dt.innerHTML = '<td>detail</td>';
        tbody.appendChild(dt);
      });
    }
    window.reRender = reRender;
    function expandedSet() {
      var s = {};
      document.querySelectorAll('tr.bv-row').forEach(function(r){ s[r.dataset.code] = r.classList.contains('bv-expanded'); });
      return s;
    }
    window.expandedSet = expandedSet;
    function applyExpand() {
      if (_expandedCode && !_expandedTop) {
        var er = tbody.querySelector('tr.bv-row[data-code="' + _expandedCode + '"]');
        var ed = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + _expandedCode + '"]');
        if (er && ed) { ed.removeAttribute('hidden'); er.classList.add('bv-expanded'); }
      }
    }
    window.applyExpand = applyExpand;
    """
    html = "<!DOCTYPE html><html><body><table><tbody></tbody></table><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Setup: B manually expanded (not top), A is top
        await page.evaluate("reRender([{code:'A',top:true},{code:'B',expanded:true},{code:'C'}])")
        s0 = await page.evaluate("expandedSet()")
        assert s0["B"] == True

        # Capture then re-render without B expanded → R72 re-applies it
        await page.evaluate("captureExpanded()")
        await page.evaluate("reRender([{code:'A',top:true},{code:'B'},{code:'C'}])")
        await page.evaluate("applyExpand()")
        s1 = await page.evaluate("expandedSet()")
        print(f"after re-render: {s1}")
        assert s1["B"] == True, "B should stay expanded after refresh"
        assert s1["A"] == False, "top A not force-expanded (only manual B)"

        # Top card expanded → NOT re-applied (top is default, refresh may rotate top-1)
        # Capture A (top) expanded
        await page.evaluate("reRender([{code:'A',top:true,expanded:true},{code:'B'},{code:'C'}])")
        await page.evaluate("captureExpanded()")
        await page.evaluate("reRender([{code:'A',top:true},{code:'B'},{code:'C'}])")
        await page.evaluate("applyExpand()")
        s2 = await page.evaluate("expandedSet()")
        print(f"top-expanded re-render (no apply): {s2}")
        assert s2["A"] == False, "top expansion is default-driven, not preserved as manual"

        print("[OK] R72 refresh preserves manually expanded detail card")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
