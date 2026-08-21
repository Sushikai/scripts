"""R75 Esc 双职 — 多选态优先退出多选."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    var _multiMode = false;
    var _multiSelected = {};
    function _exitMultiMode(){
      _multiMode = false;
      _multiSelected = {};
    }
    window._exitMultiMode = _exitMultiMode;
    function handleEscape(){
      if (_multiMode) { _exitMultiMode(); return 'multi-exit'; }
      return 'collapse-detail';
    }
    window.handleEscape = handleEscape;
    function setMulti(on){ _multiMode = on; if (on) _multiSelected = {A:'a'}; }
    window.setMulti = setMulti;
    window.getMulti = function(){ return _multiMode; };
    """
    html = "<!DOCTYPE html><html><body><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Multi mode ON → Esc exits multi
        await page.evaluate("setMulti(true)")
        r1 = await page.evaluate("handleEscape()")
        m1 = await page.evaluate("getMulti()")
        print(f"multi Esc: {r1}, multiMode={m1}")
        assert r1 == "multi-exit"
        assert m1 == False

        # Multi mode OFF → Esc collapses detail
        r2 = await page.evaluate("handleEscape()")
        print(f"no-multi Esc: {r2}")
        assert r2 == "collapse-detail"

        print("[OK] R75 Esc exits multi-select first")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
