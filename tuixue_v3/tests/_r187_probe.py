"""R187 probe: bv-row 内部 grid 行 line-height & font-size — 找 typography 紧凑点."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, t: rect.t, b: rect.b, fs: cs.fontSize, lh: cs.lineHeight, mt: cs.marginTop, mb: cs.marginBottom};
  }
  var firstRow = document.querySelector('#bv-pick-tbody tr.bv-row');
  if (!firstRow) return null;
  // 3 个 grid 行: code/name (行 1), sector/streak (行 2), rules (行 3)
  return {
    row: info(firstRow),
    codeCell: info(firstRow.querySelector('td:nth-child(1)')),
    codeLink: info(firstRow.querySelector('td:nth-child(1) a')),
    nameCell: info(firstRow.querySelector('td:nth-child(2)')),
    changeCell: info(firstRow.querySelector('td:nth-child(3)')),
    sectorCell: info(firstRow.querySelector('td:nth-child(6)')),
    sectorName: info(firstRow.querySelector('.bv-sector-name')),
    rulesCell: info(firstRow.querySelector('td:nth-child(7)')),
    ruleChip: info(firstRow.querySelector('.bv-rule-chip')),
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
        for k,v in d.items():
            if v:
                print(f"{k}: h={v['h']} t={v['t']} fs={v['fs']} lh={v['lh']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
