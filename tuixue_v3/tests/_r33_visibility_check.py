"""R33 visibilitychange → refresh."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    window.__clickCount = 0;
    window.__bv = { refresh: function() { window.__clickCount++; } };
    var _dataTs = 0;
    function renderPicks() {}
    document.addEventListener('visibilitychange', function(){
      if (document.visibilityState === 'visible' && _dataTs > 0) {
        var _awayAge = Math.floor(Date.now() / 1000) - _dataTs;
        if (_awayAge > 60 && window.__bv && window.__bv.refresh) {
          window.__bv.refresh(true);
        }
      }
    });
    window.__setAge = function(sec) {
      _dataTs = sec > 0 ? Math.floor(Date.now() / 1000) - sec : 0;
    };
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(f"<html><body><script>{js}</script></body></html>")

        # Set age to 30s, simulate visibility hidden→visible
        await page.evaluate("window.__setAge(30)")
        # Hidden
        await page.evaluate("Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true})")
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        # Visible
        await page.evaluate("Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true})")
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        await page.wait_for_timeout(50)
        cnt1 = await page.evaluate("window.__clickCount")
        print(f"30s age visibility change: refresh count = {cnt1}")
        assert cnt1 == 0, f"30s should NOT trigger refresh (only >60s), got {cnt1}"

        # Set age to 120s
        await page.evaluate("window.__setAge(120)")
        await page.evaluate("Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true})")
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        await page.evaluate("Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true})")
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        await page.wait_for_timeout(50)
        cnt2 = await page.evaluate("window.__clickCount")
        print(f"120s age visibility change: refresh count = {cnt2}")
        assert cnt2 == 1, f"120s should trigger refresh, got {cnt2}"

        # Set age to 600s, simulate another visibility change
        await page.evaluate("window.__setAge(600)")
        await page.evaluate("Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true})")
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        await page.evaluate("Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true})")
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        await page.wait_for_timeout(50)
        cnt3 = await page.evaluate("window.__clickCount")
        print(f"600s age visibility change: refresh count = {cnt3}")
        assert cnt3 == 2, f"600s should trigger another refresh, got {cnt3}"

        # 0 age — should NOT trigger
        await page.evaluate("window.__setAge(0)")
        await page.evaluate("Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true})")
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        await page.evaluate("Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true})")
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        await page.wait_for_timeout(50)
        cnt4 = await page.evaluate("window.__clickCount")
        print(f"0 age visibility change: refresh count = {cnt4}")
        assert cnt4 == 2, f"0 age should NOT trigger, got {cnt4}"

        print("[OK] R33 visibility-aware refresh works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())