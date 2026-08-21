"""R186 probe: 全页 padding/margin 汇总, 找下一处可压缩点."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, t: rect.t, b: rect.b, mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight};
  }
  // 顶部空间预算
  var viewHead = document.querySelector('.view-bv .view-head');
  var filterBar = document.querySelector('.view-bv .bv-filter-bar');
  var sectorBar = document.querySelector('.view-bv .bv-sector-bar');
  var pickCard = document.querySelector('.bv-pick-card');
  var firstRow = document.querySelector('#bv-pick-tbody tr.bv-row');
  // 其他 card 头部
  var creedCard = document.querySelector('.bv-creed-card');
  var rulesCard = document.querySelector('.bv-rules-card');
  return {
    viewHead: info(viewHead),
    filterBar: info(filterBar),
    sectorBar: info(sectorBar),
    pickCard: info(pickCard),
    firstRow: info(firstRow),
    creedCard: info(creedCard),
    rulesCard: info(rulesCard),
    // 累计顶部到首行
    chain: pickCard && firstRow ? {
      viewHead_h: viewHead.getBoundingClientRect().height,
      viewHead_mb: getComputedStyle(viewHead).marginBottom,
      filterBar_h: filterBar.getBoundingClientRect().height,
      filterBar_pt: getComputedStyle(filterBar).paddingTop,
      filterBar_pb: getComputedStyle(filterBar).paddingBottom,
      sectorBar_h: sectorBar.getBoundingClientRect().height,
      sectorBar_pt: getComputedStyle(sectorBar).paddingTop,
      sectorBar_pb: getComputedStyle(sectorBar).paddingBottom,
      pickCard_pt: getComputedStyle(pickCard).paddingTop,
      bvrow_pt: getComputedStyle(firstRow).paddingTop,
      pickTop: pickCard.getBoundingClientRect().top,
      firstRowTop: firstRow.getBoundingClientRect().top
    } : null
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
        if d['chain']:
            c = d['chain']
            print(f"=== Vertical budget to first row top={c['firstRowTop']} ===")
            print(f"  viewHead: h={c['viewHead_h']} mb={c['viewHead_mb']}")
            print(f"  filterBar: h={c['filterBar_h']} pt={c['filterBar_pt']} pb={c['filterBar_pb']}")
            print(f"  sectorBar: h={c['sectorBar_h']} pt={c['sectorBar_pt']} pb={c['sectorBar_pb']}")
            print(f"  pickCard: t={c['pickTop']} pt={c['pickCard_pt']}")
            print(f"  bv-row pt={c['bvrow_pt']}")
        print(f"\n=== Card padding comparison ===")
        for k in ['pickCard','creedCard','rulesCard']:
            v = d[k]
            if v:
                print(f"  {k}: h={v['h']} pt={v['pt']} pb={v['pb']} pl={v['pl']} pr={v['pr']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
