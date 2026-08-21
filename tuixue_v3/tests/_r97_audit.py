"""R97 审计: R95+R96 后首屏垂直预算 + 推票卡可读性. """
import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        for attempt in range(5):
            try:
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            n = await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length")
            if n > 0:
                break
        import json
        info = await page.evaluate("""() => {
          var out = { vh: window.innerHeight };
          var sels = [['.view-bv .bv-title','title'],['.view-bv .bv-phase-banner','banner'],
            ['.bv-creed-card','creed'],['#bv-filter-bar','filter'],['#bv-sector-bar','sector'],
            ['#bv-pick-tbody tr.bv-row:first-child','card1'],
            ['#bv-pick-tbody tr.bv-row:nth-child(2)','card2']];
          sels.forEach(function(s){
            var el = document.querySelector(s[0]);
            if (!el) { out[s[1]] = 'MISSING'; return; }
            var r = el.getBoundingClientRect();
            out[s[1]] = { top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height) };
          });
          var c1 = document.querySelector('#bv-pick-tbody tr.bv-row:first-child');
          if (c1) {
            var cr = c1.getBoundingClientRect();
            out.card1Visible = cr.bottom <= window.innerHeight && cr.top >= 0;
            out.card2Visible = (function(){
              var c2 = document.querySelector('#bv-pick-tbody tr.bv-row:nth-child(2)');
              if (!c2) return null;
              var r2 = c2.getBoundingClientRect();
              return r2.top < window.innerHeight && r2.bottom > 0;
            })();
          }
          out.picksRendered = document.querySelectorAll('#bv-pick-tbody tr.bv-row').length;
          return out;
        }""")
        print(json.dumps(info, ensure_ascii=False, indent=1))
        await page.screenshot(path="tests/_r97_shot.png", full_page=False)
        await browser.close()

asyncio.run(run())
