"""R184: mobile pickCard pt/pb 8→6 — 跟 bv-row padding 节奏统一

第一性原理: pickCard pt/pb=8 (R178 后) 跟 bv-row pt/pb=6 (R179 后) 不一致 —
  外层包卡比内层卡 padding 多 2px, 视觉重量倒挂. pickCard 8→6 让外层包卡跟内层卡
  padding 节奏统一 (外=内 同 6). 推票卡总高 -4px (card 145 → 141).

断言 (真实服务, 390px):
  1. pickCard pt=6 / pb=6 (从 8)
  2. pickCard 高度减少 4px
  3. pickHead top 从 123 减少 2px (从 123 → 121)
  4. 推票卡首行 top 减少 4px (从 251 → 247)
  5. 其他 card (creed/rules/cat/backtest) 仍 12px 不变
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
    for _ in range(30):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 3:
            break
    await page.wait_for_timeout(500)

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, t: rect.t, b: rect.b, pt: cs.paddingTop, pb: cs.paddingBottom};
  }
  var firstRow = document.querySelector('#bv-pick-tbody tr.bv-row');
  // 其他 card 不应该被影响 (creed/rules/cat/backtest)
  var allCards = Array.from(document.querySelectorAll('.view-bv > .card'));
  var otherCards = allCards.filter(function(c){ return !c.classList.contains('bv-pick-card'); }).map(info);
  return {
    pickCard: info(document.querySelector('.bv-pick-card')),
    pickHead: info(document.querySelector('.bv-pick-card .card-head')),
    firstRow: info(firstRow),
    otherCards: otherCards
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"pickCard: h={d['pickCard']['h']} pt={d['pickCard']['pt']} pb={d['pickCard']['pb']}")
        print(f"pickHead: t={d['pickHead']['t']}")
        print(f"firstRow: t={d['firstRow']['t']}")
        for i, oc in enumerate(d['otherCards']):
            if oc:
                print(f"other card {i}: h={oc['h']} pt={oc['pt']} pb={oc['pb']}")

        assert d['pickCard']['pt'] == '6px', f"R184: pickCard pt={d['pickCard']['pt']} != 6px"
        assert d['pickCard']['pb'] == '6px', f"R184: pickCard pb={d['pickCard']['pb']} != 6px"
        # pickHead top 应减少 2px (从 ~123 到 ~121)
        assert d['pickHead']['t'] <= 122, f"R184: pickHead top={d['pickHead']['t']} 应 <= 122"
        # firstRow top 应减少 4px (从 ~251 到 ~247)
        assert d['firstRow']['t'] <= 249, f"R184: firstRow top={d['firstRow']['t']} 应 <= 249"
        # 其他 card 不变 (12px)
        for oc in d['otherCards']:
            if oc:
                assert oc['pt'] == '12px', f"R184: other card pt={oc['pt']} 应保持 12px"
                assert oc['pb'] == '12px', f"R184: other card pb={oc['pb']} 应保持 12px"

        await b.close()
        print(f"[OK] R184 pickCard pt/pb 8→6 — pickHead top {d['pickHead']['t']} (回收 2px), "
              f"firstRow top {d['firstRow']['t']} (回收 ~4px), 其他 card 12px 不变 ✓")

if __name__ == "__main__":
    asyncio.run(run())
