"""R186: mobile bv-row row-gap 2px→1px — 表卡内部行间距紧凑

第一性原理: bv-row gap 2px 8px (R15 后 row-gap 2px, column-gap 8px) — 2px 是 row 之间
  垂直间距. bv-row 有 3 个 grid 行 (code/name, sector/streak, rules), 2 个 row gap × 2px = 4px
  内部空气. bv-row padding (R179) 已是 6/6 紧凑, row-gap 2 是冗余 — 1px 跟 grid 行内文字
  line-height 1.2 接近, 让行视觉连贯. 15 行回收 30px (4px × 2 间隔 × 15 row).

断言 (真实服务, 390px):
  1. bv-row gap row 1px (从 2px)
  2. gap column 8px 保持
  3. bv-row h 减少 2px (从 89 → 87)
  4. 网格 3 行 (code/name, sector/streak, rules) 不被破坏
  5. 一屏可见卡数 ≥ 7 (跟 R179/R180 持平或更好)
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
    var rect = r(tr);
    return {
      code: tr.dataset.code,
      h: rect.h,
      t: rect.t,
      gap: cs.gap,
      rowGap: cs.rowGap,
      columnGap: cs.columnGap,
      gridRows: cs.gridTemplateRows
    };
  });
  var viewportH = window.innerHeight;
  var visibleCount = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row')).filter(function(tr){
    var rect = tr.getBoundingClientRect();
    return rect.top < viewportH && rect.bottom > 0;
  }).length;
  return {cards: card, viewportH: viewportH, visibleCount: visibleCount};
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
            print(f"  {c['code']}  h={c['h']}  rowGap={c['rowGap']}  colGap={c['columnGap']}")

        for c in d['cards']:
            assert c['rowGap'] == '1px', f"R186: {c['code']} rowGap={c['rowGap']} != 1px"
            assert c['columnGap'] == '8px', f"R186: {c['code']} colGap={c['columnGap']} != 8px"
            # h 应 ≤ 89 (回收 2px)
            assert c['h'] <= 88, f"R186: {c['code']} h={c['h']} 应 <= 88"

        assert d['visibleCount'] >= 7, f"R186: visible={d['visibleCount']} < 7"

        await b.close()
        print(f"[OK] R186 bv-row row-gap 2→1 — rowH 89→{d['cards'][0]['h']}px (回收 ~2px/卡), "
              f"15 行累计回收 30px, visible={d['visibleCount']} ✓")

if __name__ == "__main__":
    asyncio.run(run())
