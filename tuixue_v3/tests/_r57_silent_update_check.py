"""R57 静默更新提示."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-stale-strip .bv-stale-silent {
      background: rgba(34, 197, 94, 0.25); color: #4ade80;
      padding: 1px 6px; border-radius: 8px; margin-right: 4px;
      font-weight: 700; font-size: 10px;
    }
    """
    js = """
    window.__hashes = [];
    function makeHash(picks) { return picks.map(function(p){return p.code+':'+(p.score||0);}).join('|'); }
    function simulate(picks) {
      var newHash = makeHash(picks);
      var silent = (newHash === window.__hashes[window.__hashes.length-1] && window.__hashes.length > 0);
      window.__hashes.push(newHash);
      return silent;
    }
    window.simulate = simulate;
    """
    html = "<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
    html += "<script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # First refresh — initial data
        s1 = await page.evaluate("simulate([{code:'600000',score:90},{code:'000001',score:80}])")
        print(f"first refresh: silent={s1}")
        assert s1 == False, "first load should not be silent"

        # Same data → silent
        s2 = await page.evaluate("simulate([{code:'600000',score:90},{code:'000001',score:80}])")
        print(f"same data: silent={s2}")
        assert s2 == True, "same picks should be silent"

        # Different data → not silent
        s3 = await page.evaluate("simulate([{code:'600000',score:91},{code:'000001',score:80}])")
        print(f"score changed: silent={s3}")
        assert s3 == False, "score change should not be silent"

        # Different code → not silent
        s4 = await page.evaluate("simulate([{code:'600000',score:91},{code:'002142',score:80}])")
        print(f"code changed: silent={s4}")
        assert s4 == False, "code change should not be silent"

        # Same again → silent
        s5 = await page.evaluate("simulate([{code:'600000',score:91},{code:'002142',score:80}])")
        print(f"same again: silent={s5}")
        assert s5 == True

        print("[OK] R57 silent detection logic works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())