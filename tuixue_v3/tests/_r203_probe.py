"""R203 probe: scan top zone + bv-row internals for next squeezable target"""
import asyncio
from playwright.async_api import async_playwright

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

PROBE = r"""() => {
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = el.getBoundingClientRect();
    return {h: Math.round(rect.height*10)/10, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight, lh: cs.lineHeight, fs: cs.fontSize};
  }
  // Top zone chain
  var vh = document.querySelector('.view-bv .view-head');
  var fb = document.querySelector('.view-bv .bv-filter-bar');
  var sb = document.querySelector('.view-bv .bv-sector-bar');
  var pc = document.querySelector('.view-bv > .bv-pick-card');
  var ph = document.querySelector('.view-bv > .bv-pick-card .card-head');
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  var cells = row ? row.querySelectorAll('td, .bv-cell') : [];
  var cellsInfo = [];
  cells.forEach((c, i) => {
    var cs = getComputedStyle(c);
    var rect = c.getBoundingClientRect();
    cellsInfo.push({i: i, h: Math.round(rect.height*10)/10, w: Math.round(rect.width*10)/10, lh: cs.lineHeight, fs: cs.fontSize});
  });
  return {
    vh: info(vh), fb: info(fb), sb: info(sb),
    pc: info(pc), ph: info(ph),
    row: info(row),
    cells: cellsInfo,
    cellCount: cells.length
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print("=== TOP ZONE CHAIN ===")
        for k in ['vh','fb','sb','pc','ph']:
            v = d[k]
            if v:
                print(f"  {k}: h={v['h']} pt={v['pt']} pb={v['pb']} lh={v.get('lh','-')} fs={v.get('fs','-')}")
        print(f"\n=== BV-ROW ===")
        r = d['row']
        print(f"  row: h={r['h']} pt={r['pt']} pb={r['pb']} pl={r['pl']} pr={r['pr']}")
        print(f"  cells ({d['cellCount']}):")
        for c in d['cells']:
            print(f"    [{c['i']}] h={c['h']} w={c['w']} lh={c['lh']} fs={c['fs']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())