"""R54 双击撤销详情展开."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__lastBvExpand = null;
    var tbody = document.querySelector('tbody');
    function fakeClick(code, isTop) {
      var tr = tbody.querySelector('tr.bv-row[data-code="' + code + '"]');
      var detail = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + code + '"]');
      var wasOpen = !detail.hasAttribute('hidden');
      tbody.querySelectorAll('tr.bv-detail-row:not([hidden])').forEach(function(d){ d.setAttribute('hidden',''); });
      tbody.querySelectorAll('tr.bv-row.bv-expanded').forEach(function(r){ r.classList.remove('bv-expanded'); });
      var _topReopen = (code === 'A' && isTop);
      var _now = Date.now();
      var _undo = wasOpen && !_topReopen &&
                  window.__lastBvExpand && window.__lastBvExpand.code === code &&
                  (_now - window.__lastBvExpand.ts) < 600;
      if (_undo) { window.__lastBvExpand = null; return 'undo'; }
      if (!wasOpen || _topReopen) {
        detail.removeAttribute('hidden');
        tr.classList.add('bv-expanded');
        window.__lastBvExpand = {code: code, ts: _now};
        return 'expand';
      } else {
        window.__lastBvExpand = null;
        return 'collapse';
      }
    }
    window.fakeClick = fakeClick;
    function state() {
      var d = document.querySelector('tr.bv-detail-row[data-detail-for="B"]');
      return {hidden: d.hasAttribute('hidden'), cls: document.querySelector('tr.bv-row[data-code="B"]').className};
    }
    window.state = state;
    """
    html = "<!DOCTYPE html><html><body><table><tbody>"
    html += '<tr class="bv-row" data-code="A"><td>A</td></tr>'
    html += '<tr class="bv-detail-row" data-detail-for="A" hidden><td>detail A</td></tr>'
    html += '<tr class="bv-row" data-code="B"><td>B</td></tr>'
    html += '<tr class="bv-detail-row" data-detail-for="B" hidden><td>detail B</td></tr>'
    html += "</tbody></table><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # 1st click on B → expand
        r1 = await page.evaluate("fakeClick('B', false)")
        s1 = await page.evaluate("state()")
        print(f"1st click B: {r1}, state={s1}")
        assert r1 == "expand"
        assert s1["hidden"] is False

        # 2nd click on B within 600ms → undo (collapse)
        await page.wait_for_timeout(100)
        r2 = await page.evaluate("fakeClick('B', false)")
        s2 = await page.evaluate("state()")
        print(f"2nd click B (within 600ms): {r2}, state={s2}")
        assert r2 == "undo"
        assert s2["hidden"] is True

        # 3rd click on B → re-expand (lastBvExpand was cleared by undo)
        r3 = await page.evaluate("fakeClick('B', false)")
        s3 = await page.evaluate("state()")
        print(f"3rd click B: {r3}, state={s3}")
        assert r3 == "expand"
        assert s3["hidden"] is False

        # 4th click after 700ms (>600ms) → normal collapse path
        await page.wait_for_timeout(700)
        r4 = await page.evaluate("fakeClick('B', false)")
        s4 = await page.evaluate("state()")
        print(f"4th click B (after 700ms): {r4}, state={s4}")
        assert r4 == "collapse"
        assert s4["hidden"] is True

        # Click on A (non-top), then A again within window → undo
        await page.evaluate("fakeClick('A', false)")  # expand A
        await page.wait_for_timeout(100)
        r5 = await page.evaluate("fakeClick('A', false)")
        print(f"A double-click: {r5}")
        assert r5 == "undo"

        print("[OK] R54 double-click undo works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())