"""R180: mobile bv-row margin-bottom 6→4 — 卡片间距紧凑收尾

第一性原理: bv-row margin-bottom 6px (R15 设的) 在 89px/卡 (R179 后) 占了 6.7%
  视觉开销. 卡片间视觉边界已由 hit-tier stripe (R113 3px) + 1px border (var(--line-1))
  + 不同 change 颜色给出, 6px 是过度留白 — 4px 仍给呼吸空间但视觉更紧凑.
  15 行 × 2px = 30px 回收 → 一屏多看 0.3 张卡.
  注: 改 margin-bottom 比 padding-bottom 安全 — padding 影响内部 grid 高度, margin
  是卡片间距.

断言 (真实服务, 390px):
  1. bv-row margin-bottom 4px (从 6px)
  2. 卡片 top - prevBottom = 4px (margin 准确)
  3. rowH 仍 85-92 (padding 不受影响)
  4. hit-tier stripe 仍可见 (margin 不影响 border/stripe)
  5. 一屏可见卡数 ≥ 7 (跟 R179 持平或更好)
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
  var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row')).slice(0,6);
  var card = rows.map(function(tr){
    var cs = getComputedStyle(tr);
    var box = getComputedStyle(tr).boxShadow;
    var rect = r(tr);
    return {
      code: tr.dataset.code,
      h: rect.h,
      t: rect.t,
      b: rect.b,
      mb: cs.marginBottom,
      mt: cs.marginTop,
      hasShadow: box && box !== 'none',
      gridRows: getComputedStyle(tr).gridTemplateRows
    };
  });
  // card 之间实际间距 (top of i+1 - bottom of i)
  var gaps = [];
  for (var i=1; i<card.length; i++) gaps.push(card[i]['t'] - card[i-1]['b']);
  // 一屏可见数
  var allRows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
  var viewportH = window.innerHeight;
  var visibleCount = allRows.filter(function(tr){
    var rect = tr.getBoundingClientRect();
    return rect.top < viewportH && rect.bottom > 0;
  }).length;
  return {cards: card, gaps: gaps, viewportH: viewportH, visibleCount: visibleCount};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"viewport h={d['viewportH']}, visible cards={d['visibleCount']}")
        for c in d['cards']:
            print(f"  {c['code']}  h={c['h']}  t={c['t']}  mb={c['mb']}  shadow={'y' if c['hasShadow'] else 'n'}")
        print(f"gaps (top[i+1] - bottom[i]): {d['gaps']}")

        for c in d['cards']:
            # 1) margin-bottom 6→4
            assert c['mb'] == '4px', f"R180: {c['code']} mb={c['mb']} != 4px"
            # 3) rowH 仍 ~89
            assert 85 <= c['h'] <= 92, f"R180: {c['code']} rowH={c['h']} out of range"
            # 4) hit-tier stripe 仍可见
            assert c['hasShadow'], f"R180: {c['code']} lost tier stripe shadow"

        # 2) gap = 4px (margin 准确)
        for i, g in enumerate(d['gaps']):
            assert 3 <= g <= 5, f"R180: gap[{i}]={g} not in 3-5px range"

        # 5) 一屏可见 ≥ 7
        assert d['visibleCount'] >= 7, f"R180: visible cards={d['visibleCount']} < 7"

        await b.close()
        print(f"[OK] R180 margin-bottom 6→4 — gap 6→4px (回收 ~2px/卡), "
              f"visible={d['visibleCount']} 卡, hit-tier stripe 保留 ✓")

if __name__ == "__main__":
    asyncio.run(run())
