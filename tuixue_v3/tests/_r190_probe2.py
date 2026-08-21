"""R190 probe: sector-bar 内部 padding 细节 — sector-pill / label / chg 各项具体值."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = el.getBoundingClientRect();
    return {h: Math.round(rect.height), w: Math.round(rect.width), mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight, gap: cs.gap, fs: cs.fontSize};
  }
  var sectorBar = document.querySelector('.view-bv .bv-sector-bar');
  var pills = document.querySelectorAll('.view-bv .bv-sector-pill');
  var sectorLabel = document.querySelector('.view-bv .bv-sector-bar-label');
  var sectorBarInner = document.querySelector('.view-bv .bv-sector-bar-inner');
  return {
    sectorBar: info(sectorBar),
    sectorBarInner: info(sectorBarInner),
    sectorLabel: info(sectorLabel),
    pills: Array.from(pills).slice(0, 4).map(info),
    pillsCount: pills.length,
  };
}"""

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for _ in range(20):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
    await page.wait_for_timeout(500)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for k, v in d.items():
            if isinstance(v, list):
                print(f"{k} ({d.get('pillsCount', '?')}):")
                for i, p in enumerate(v):
                    print(f"  [{i}]: h={p['h']} w={p['w']} pt={p.get('pt')} pb={p.get('pb')} pl={p.get('pl')} pr={p.get('pr')} fs={p.get('fs')}")
            else:
                if v:
                    print(f"{k}: h={v['h']} w={v['w']} pt={v.get('pt')} pb={v.get('pb')} pl={v.get('pl')} pr={v.get('pr')} gap={v.get('gap')}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())