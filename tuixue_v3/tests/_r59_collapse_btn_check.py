"""R59 详情行折叠按钮."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-detail-collapse {
      position: absolute; top: 6px; right: 8px;
      background: rgba(255,255,255,0.08); color: #aaa;
      border: 1px solid #333; border-radius: 12px;
      padding: 2px 10px; font-size: 11px; cursor: pointer;
    }
    .bv-detail-inner { position: relative; }
    """
    js = """
    var tbody = document.querySelector('tbody');
    window.__lastBvExpand = null;
    tbody.onclick = function(ev) {
      var cb = ev.target.closest('.bv-detail-collapse');
      if (cb) {
        ev.stopPropagation();
        ev.preventDefault();
        var cc = cb.getAttribute('data-collapse-for');
        var d = tbody.querySelector('tr.bv-detail-row[data-detail-for="' + cc + '"]');
        if (d) d.setAttribute('hidden', '');
        var r = tbody.querySelector('tr.bv-row[data-code="' + cc + '"]');
        if (r) r.classList.remove('bv-expanded');
        window.__lastBvExpand = null;
        return;
      }
    };
    """
    html = "<!DOCTYPE html><html><head><style>" + css + "</style></head><body><table><tbody>"
    html += '<tr class="bv-row bv-expanded" data-code="A"><td>A</td></tr>'
    html += '<tr class="bv-detail-row" data-detail-for="A"><td><div class="bv-detail-inner">'
    html += '<button class="bv-detail-collapse" data-collapse-for="A">✕ 收起</button>'
    html += '</div></td></tr>'
    html += "</tbody></table><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Button exists + cursor pointer
        btn = await page.query_selector(".bv-detail-collapse")
        assert btn is not None, "collapse button should exist"
        cursor = await page.evaluate("getComputedStyle(document.querySelector('.bv-detail-collapse')).cursor")
        print(f"button cursor={cursor}")
        assert cursor == "pointer"

        # Initially expanded
        hidden1 = await page.evaluate("document.querySelector('tr.bv-detail-row').hasAttribute('hidden')")
        print(f"initial hidden={hidden1}")
        assert hidden1 == False

        # Click collapse → hidden + no bv-expanded
        await page.evaluate("document.querySelector('.bv-detail-collapse').click()")
        hidden2 = await page.evaluate("document.querySelector('tr.bv-detail-row').hasAttribute('hidden')")
        expanded = await page.evaluate("document.querySelectorAll('.bv-expanded').length")
        print(f"after collapse: hidden={hidden2} expanded={expanded}")
        assert hidden2 == True
        assert expanded == 0

        print("[OK] R59 collapse button hides detail + clears expanded")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())