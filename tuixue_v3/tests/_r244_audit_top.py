"""R244 prep: 审计 top 区 (view-head/filter-bar/sector-bar/pick-head/count) 字号一致性

第一性原理: R243 用布局探针审计 bv-row 抓出 time 残留. 同样的审计方法
  用到 top 区 — R224-R242 的紧凑/字号统一 wave 全在 bv-row 内,
  top 区 (标题/过滤/板块条/计数) 的字号体系没有被系统审计过.
  本探针列出每个元素的 fs/fw/h, 找残留不一致.
"""
import asyncio, json
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
  var pick = function(sel){
    var el = document.querySelector('.view-bv ' + sel);
    if (!el) return null;
    var cs = getComputedStyle(el);
    var r = el.getBoundingClientRect();
    return {fs: cs.fontSize, fw: cs.fontWeight, lh: cs.lineHeight, ls: cs.letterSpacing,
            h: Math.round(r.height), text: (el.textContent||'').trim().slice(0,18)};
  };
  return {
    bv_title:       pick('.bv-title'),
    bv_meta:        pick('.bv-meta'),
    filter_bar:     pick('.bv-filter-bar'),
    filter_chip:    pick('.bv-filter-bar .bv-filter-chip'),
    filter_all:     pick('.bv-filter-bar .bv-filter-chip.is-active'),
    sector_bar:     pick('.bv-sector-bar'),
    sector_pill:    pick('.bv-sector-bar .bv-sector-pill'),
    pick_head:      pick('.bv-pick-head'),
    sort_btn:       pick('.bv-pick-head .bv-sort-btn'),
    count:          pick('.bv-pick-count'),
    refresh_btn:    pick('.bv-pick-head .bv-refresh'),
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for k, v in d.items():
            if v is None:
                print(f"{k}: MISSING")
            else:
                print(f"{k:<14} fs={v['fs']:<7} fw={v['fw']:<4} lh={v['lh']:<8} h={v['h']:<4} '{v['text']}'")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
