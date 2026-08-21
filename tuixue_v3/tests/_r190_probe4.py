"""R190 probe: view-head typography + 行间距细节."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = el.getBoundingClientRect();
    return {h: Math.round(rect.height), w: Math.round(rect.width), mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight, fs: cs.fontSize, lh: cs.lineHeight, cls: el.className.toString().slice(0,40)};
  }
  var viewHead = document.querySelector('.view-bv .view-head');
  var display = document.querySelector('.view-bv .view-head .display');
  var caption = document.querySelector('.view-bv .view-head .caption');
  var h1 = document.querySelector('.view-bv .view-head h1');
  var vhFirstChild = viewHead ? viewHead.querySelector('div:first-child') : null;
  var refreshBtn = document.querySelector('.view-bv .view-head .btn-refresh');
  var vhAllKids = viewHead ? Array.from(viewHead.querySelectorAll('*')).slice(0, 10).map(info) : [];
  return {
    viewHead: info(viewHead),
    display: info(display),
    caption: info(caption),
    h1: info(h1),
    vhFirstChild: info(vhFirstChild),
    refreshBtn: info(refreshBtn),
    vhAllKids: vhAllKids,
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
                print(f"{k}:")
                for i, c in enumerate(v):
                    print(f"  [{i}] <{c.get('cls','')[:20]}>: h={c.get('h')} w={c.get('w')} mt={c.get('mt')} mb={c.get('mb')} pt={c.get('pt')} pb={c.get('pb')} fs={c.get('fs')} lh={c.get('lh')}")
            elif v:
                print(f"{k}: h={v.get('h')} w={v.get('w')} mb={v.get('mb')} mt={v.get('mt')} pb={v.get('pb')} pt={v.get('pt')} pl={v.get('pl')} pr={v.get('pr')} fs={v.get('fs')} lh={v.get('lh')} cls={v.get('cls','')}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())