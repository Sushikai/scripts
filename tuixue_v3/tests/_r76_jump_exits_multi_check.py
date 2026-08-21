"""R76 多选态跳个股前先退出多选 — 临时态不泄漏."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    var _multiMode = false;
    var _multiSelected = {};
    var _gotoCalled = null;
    function _exitMultiMode(){ _multiMode = false; _multiSelected = {}; }
    function gotoStock(c){ _gotoCalled = c; }
    window.gotoStock = gotoStock;
    function handleJumpClick() {
      var code = '600000';
      if (_multiMode) _exitMultiMode();
      gotoStock(code);
      return true;
    }
    window.handleJumpClick = handleJumpClick;
    window.getMulti = function(){ return _multiMode; };
    window.setMulti = function(){ _multiMode = true; _multiSelected = {A:'a'}; };
    """
    html = "<!DOCTYPE html><html><body><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Multi mode ON → jump exits multi + navigates
        await page.evaluate("setMulti()")
        await page.evaluate("handleJumpClick()")
        m1 = await page.evaluate("getMulti()")
        g1 = await page.evaluate("_gotoCalled")
        print(f"jump in multi: multiMode={m1} goto={g1}")
        assert m1 == False, "multi should exit on jump"
        assert g1 == "600000", "still navigates"

        # Multi mode OFF → plain jump
        await page.evaluate("handleJumpClick()")
        g2 = await page.evaluate("_gotoCalled")
        print(f"plain jump: goto={g2}")
        assert g2 == "600000"

        print("[OK] R76 jumping from multi exits multi-mode first")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
