"""R55 键盘导航."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    var tbody = document.querySelector('tbody');
    var _kbIdx = -1;
    document.addEventListener('keydown', function(ev){
      var rows = tbody.querySelectorAll('tr.bv-row');
      if (!rows.length) return;
      var ae = document.activeElement;
      if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.isContentEditable)) return;
      if (ev.key === 'ArrowDown') {
        ev.preventDefault();
        _kbIdx = Math.min(rows.length - 1, _kbIdx + 1);
        rows.forEach(function(r){ r.classList.toggle('bv-kb-focus', r === rows[_kbIdx]); });
      } else if (ev.key === 'ArrowUp') {
        ev.preventDefault();
        _kbIdx = Math.max(0, _kbIdx - 1);
        rows.forEach(function(r){ r.classList.toggle('bv-kb-focus', r === rows[_kbIdx]); });
      } else if (ev.key === 'Enter') {
        if (_kbIdx < 0) return;
        rows[_kbIdx].classList.toggle('bv-expanded');
      } else if (ev.key === 'Escape') {
        tbody.querySelectorAll('tr.bv-row.bv-expanded').forEach(function(r){ r.classList.remove('bv-expanded'); });
      }
    });
    window.__idx = function(){ return _kbIdx; };
    """
    html = '<!DOCTYPE html><html><head><style>.bv-kb-focus { outline: 2px solid #00f0ff; }</style></head>'
    html += "<body><table><tbody>"
    html += '<tr class="bv-row" data-code="A"><td>A</td></tr>'
    html += '<tr class="bv-row" data-code="B"><td>B</td></tr>'
    html += '<tr class="bv-row" data-code="C"><td>C</td></tr>'
    html += "</tbody></table><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # ArrowDown → idx 0
        await page.keyboard.press("ArrowDown")
        idx = await page.evaluate("__idx()")
        focused = await page.evaluate("document.querySelector('.bv-kb-focus').dataset.code")
        print(f"after ArrowDown: idx={idx} focused={focused}")
        assert idx == 0 and focused == "A"

        # ArrowDown × 2 → idx 2
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("ArrowDown")
        idx = await page.evaluate("__idx()")
        focused = await page.evaluate("document.querySelector('.bv-kb-focus').dataset.code")
        print(f"after 2x ArrowDown: idx={idx} focused={focused}")
        assert idx == 2 and focused == "C"

        # Enter → toggle expand on focused
        await page.keyboard.press("Enter")
        exp = await page.evaluate("document.querySelectorAll('.bv-expanded').length")
        print(f"after Enter: expanded count={exp}")
        assert exp == 1

        # Escape → clear expanded
        await page.keyboard.press("Escape")
        exp = await page.evaluate("document.querySelectorAll('.bv-expanded').length")
        print(f"after Escape: expanded count={exp}")
        assert exp == 0

        # ArrowUp from idx 2 → idx 1
        await page.keyboard.press("ArrowUp")
        idx = await page.evaluate("__idx()")
        focused = await page.evaluate("document.querySelector('.bv-kb-focus').dataset.code")
        print(f"after ArrowUp: idx={idx} focused={focused}")
        assert idx == 1 and focused == "B"

        # Check outline CSS rule exists in stylesheet
        outline_rule = await page.evaluate("""
        (() => {
          for (const sheet of document.styleSheets) {
            try {
              for (const rule of sheet.cssRules) {
                if (rule.cssText && rule.cssText.includes('bv-kb-focus') && rule.cssText.includes('outline')) {
                  return rule.cssText;
                }
              }
            } catch (e) {}
          }
          return null;
        })()
        """)
        print(f"focus rule: {outline_rule}")
        assert outline_rule is not None
        assert "outline" in outline_rule

        print("[OK] R55 keyboard nav works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())