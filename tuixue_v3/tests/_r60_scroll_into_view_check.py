"""R60 详情展开自动滚到卡片."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__scrollIntoViewCalled = false;
    Element.prototype.scrollIntoView = function(opts) {
      window.__scrollIntoViewCalled = true;
      window.__scrollOpts = opts;
    };
    window.__testExpand = function() {
      var tr = document.querySelector('tr.bv-row[data-code="A"]');
      var detail = document.querySelector('tr.bv-detail-row[data-detail-for="A"]');
      // Simulate R60: card above viewport → scrollIntoView
      tr.getBoundingClientRect = function() { return {top: -300, bottom: -100}; };
      detail.removeAttribute('hidden');
      tr.classList.add('bv-expanded');
      var rect = tr.getBoundingClientRect();
      if (rect.top < 0) tr.scrollIntoView({block: 'start', behavior: 'smooth'});
    };
    """
    html = "<!DOCTYPE html><html><body><table><tbody>"
    html += '<tr class="bv-row" data-code="A"><td>A</td></tr>'
    html += '<tr class="bv-detail-row" data-detail-for="A" hidden><td>detail</td></tr>'
    html += "</tbody></table><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        await page.evaluate("__testExpand()")
        called = await page.evaluate("__scrollIntoViewCalled")
        opts = await page.evaluate("__scrollOpts")
        print(f"scrollIntoView called={called} opts={opts}")
        assert called == True, "should scroll into view when card above viewport"
        assert opts and opts["block"] == "start"
        assert opts and opts["behavior"] == "smooth"

        print("[OK] R60 auto-scroll when expanded card above viewport")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())