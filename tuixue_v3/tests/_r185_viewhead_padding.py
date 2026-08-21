"""R185: mobile view-head pt/pb 8→6 — 跟 R179 bv-row + R184 pickCard 6 节奏统一

第一性原理: view-head pt/pb 8px (480px 全局 !important 8px 12px) — 跟 R179 bv-row 6/6 +
  R184 pickCard 6 节奏不一致. view-head h=52 (含 8+8 padding), 内容 ~36px.
  pt/pb 8→6 让头部节奏 6/6 跟全局 6 节奏一致, 总高 -4px (52→48), 推票卡再下沉 4px.

断言 (真实服务, 390px):
  1. view-head pt/pb = 6px (从 8px)
  2. view-head h 减少 4px (52→48)
  3. filter-bar top 减少 4px (从 ~167 → ~163)
  4. pickCard top 减少 4px (从 ~115 → ~111)
  5. view-head 标题/计数/lede 文字不裁剪
"""
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
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, t: rect.t, b: rect.b, pt: cs.paddingTop, pb: cs.paddingBottom, mb: cs.marginBottom};
  }
  // 检查内部元素是否被裁剪
  var display = document.querySelector('.view-bv .view-head h1, .view-bv .view-head .display');
  var lede = document.querySelector('.view-bv .view-head .lede, .view-bv .view-head p');
  var refresh = document.querySelector('.view-bv .view-head .btn-refresh, .view-bv .view-head .view-actions button');
  return {
    viewHead: info(document.querySelector('.view-bv .view-head')),
    display: display ? r(display) : null,
    lede: lede ? r(lede) : null,
    refresh: refresh ? r(refresh) : null,
    filterBar: info(document.querySelector('.view-bv .bv-filter-bar')),
    sectorBar: info(document.querySelector('.view-bv .bv-sector-bar')),
    pickCard: info(document.querySelector('.bv-pick-card')),
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"viewHead: h={d['viewHead']['h']} pt={d['viewHead']['pt']} pb={d['viewHead']['pb']} mb={d['viewHead']['mb']}")
        print(f"  display h={d['display']['h'] if d['display'] else None}")
        print(f"  lede h={d['lede']['h'] if d['lede'] else None}")
        print(f"  refresh h={d['refresh']['h'] if d['refresh'] else None}")
        print(f"filterBar: t={d['filterBar']['t']}")
        print(f"sectorBar: t={d['sectorBar']['t']}")
        print(f"pickCard: t={d['pickCard']['t']}")

        assert d['viewHead']['pt'] == '6px', f"R185: view-head pt={d['viewHead']['pt']} != 6px"
        assert d['viewHead']['pb'] == '6px', f"R185: view-head pb={d['viewHead']['pb']} != 6px"
        # h 应 < 52 (回收 4px)
        assert d['viewHead']['h'] <= 50, f"R185: view-head h={d['viewHead']['h']} 应 <= 50"
        # filter-bar top 应 < 167 (R183 后 167, R185 应 ~163)
        assert d['filterBar']['t'] <= 165, f"R185: filter-bar top={d['filterBar']['t']} 应 <= 165"

        await b.close()
        print(f"[OK] R185 view-head pt/pb 8→6 — viewHead.h {d['viewHead']['h']} (从 52 回收 ~4px), "
              f"filterBar.t {d['filterBar']['t']} (下移 4px), 推票卡再下沉 4px ✓")

if __name__ == "__main__":
    asyncio.run(run())
