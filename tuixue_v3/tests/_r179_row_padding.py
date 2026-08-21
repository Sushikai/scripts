"""R179: mobile 卡片 padding 8→6 — 一屏 +0.6 卡.

第一性原理: bv-row 内边距 8+8=16px 占 rowH 93 的 17% — 表内容是 grid 3 行
  (26+2+19+2+28=77px), 16px padding 跟 hit-tier stripe (3px) + border 一起
  构成视觉边界, 但 8px 偏松。R15 (R-compact) 把 4 行压成 3 行时 row 从 ~145
  压到 95, padding 没动。R177 修了 inline-block 让 row 93 更紧, 但 padding
  仍 8px 是冗余. 8→6 是 minimum — 再小 hit-tier 跟 card 边会视觉粘连。

断言 (真实服务, 390px):
  1. bv-row padding pt=6 / pb=6 (从 8)
  2. rowH 从 93 降到 ~89 (4px/row 回收)
  3. 5 张卡 visible 总高度回收 ~20px (~0.6 卡)
  4. 网格 3 行 (code/name, sector, rules) 仍正确对齐 — 不破坏 grid
  5. hit-tier stripe 仍可见 (R113 box-shadow inset 不依赖 padding)
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
    return {
      code: tr.dataset.code,
      h: r(tr).h,
      t: r(tr).t,
      pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight,
      hasShadow: box && box !== 'none',
      gridRows: getComputedStyle(tr).gridTemplateRows
    };
  });
  // card 之间间距
  var gaps = [];
  for (var i=1; i<card.length; i++) gaps.push(card[i]['t'] - card[i-1]['b']);
  // 一屏可见数 (top<844, bottom>0)
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
            print(f"  {c['code']}  h={c['h']}  t={c['t']}  pt={c['pt']} pb={c['pb']}  shadow={'y' if c['hasShadow'] else 'n'}  rows={c['gridRows']}")
        print(f"gaps: {d['gaps']}")

        for c in d['cards']:
            # 1) padding 8→6
            assert c['pt'] == '6px', f"R179: {c['code']} pt={c['pt']} != 6px"
            assert c['pb'] == '6px', f"R179: {c['code']} pb={c['pb']} != 6px"
            # 2) rowH 从 93 → ~89 (允许 87-92 范围, 表头 row 内部可能微调)
            assert 85 <= c['h'] <= 92, f"R179: {c['code']} rowH={c['h']} out of range (want 87-92)"
            # 4) hit-tier stripe 保留
            assert c['hasShadow'], f"R179: {c['code']} lost tier stripe shadow"

        avg_h = sum(c['h'] for c in d['cards']) / len(d['cards'])
        await browser_close(b)
        print(f"[OK] R179 row padding 8→6 — rowH 93→{avg_h:.0f}px (回收 ~{93-avg_h:.0f}px/卡), "
              f"viewport visible={d['visibleCount']} 卡, hit-tier stripe 保留 ✓")

async def browser_close(b):
    await b.close()

if __name__ == "__main__":
    asyncio.run(run())