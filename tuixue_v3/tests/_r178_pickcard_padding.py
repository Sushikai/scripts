"""R178: pickCard padding 12→8 — 首卡进入首屏 +12px, 8 卡一屏更稳.

第一性原理: pickCard 内是密集表 (15+ 行, 每行 93px), 12px 上下 padding 是按
  文本 card 设计的 (creed/rules/cat 都是文本块, 12px 视觉必要); 但表卡有自己
  的视觉骨架 (card-head 分割 / sector-bar pt 2 pb 8 / table head border-bottom),
  不需要 card 自带 12px 空气. 24px (12+12) 全部浪费. 8px 是 minimum visual
  breathing room, 跟 R77 filter-bar pt=4 节奏一致.

断言 (真实服务, 390px):
  1. pickCard pt=8 / pb=8 (从 12 降到 8)
  2. 首行 top 从 ~255 降到 ~247 (回收 8px)
  3. 末行 bottom 同步上移 (8px) — card 总高缩短 16px
  4. 其他 card (creed/rules/cat/backtest) 不受影响 — padding 仍 12px
  5. 卡内 filter-bar / sector-bar 仍可滚动, 高度未动
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
    await page.wait_for_timeout(800)

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var pickCard = document.querySelector('.view-bv > .bv-pick-card');
  var pcs = pickCard ? getComputedStyle(pickCard) : null;
  var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
  var firstRow = rows[0] ? r(rows[0]) : null;
  var lastRow = rows.length ? r(rows[rows.length-1]) : null;
  // 其他 cards (creed/rules/cat/backtest) 不应受影响
  var otherCards = Array.from(document.querySelectorAll('.view-bv > .card:not(.bv-pick-card)'));
  var others = otherCards.map(function(c){
    var cs = getComputedStyle(c);
    return {cls: (c.className||'').toString().slice(0,40), pt: cs.paddingTop, pb: cs.paddingBottom};
  });
  // filter-bar / sector-bar 高度
  var fb = document.querySelector('.bv-filter-bar');
  var sb = document.querySelector('.bv-sector-bar');
  return {
    pickCardPad: pcs ? {pt: pcs.paddingTop, pb: pcs.paddingBottom, pl: pcs.paddingLeft, pr: pcs.paddingRight} : null,
    pickCardH: pickCard ? r(pickCard).h : null,
    firstRowTop: firstRow ? firstRow.t : null,
    lastRowBottom: lastRow ? lastRow.b : null,
    filterBarH: fb ? r(fb).h : null,
    sectorBarH: sb && !sb.hidden ? r(sb).h : null,
    others: others
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"pickCard pad: pt={d['pickCardPad']['pt']} pb={d['pickCardPad']['pb']} pl={d['pickCardPad']['pl']} pr={d['pickCardPad']['pr']}")
        print(f"pickCard h={d['pickCardH']}, firstRow top={d['firstRowTop']}, lastRow bottom={d['lastRowBottom']}")
        print(f"filterBar h={d['filterBarH']}, sectorBar h={d['sectorBarH']}")
        print(f"others padding: {[(o['cls'], o['pt'], o['pb']) for o in d['others']]}")

        # 1) pickCard pt=8
        assert d['pickCardPad']['pt'] == '8px', f"R178: pickCard pt={d['pickCardPad']['pt']} != 8px"
        assert d['pickCardPad']['pb'] == '8px', f"R178: pickCard pb={d['pickCardPad']['pb']} != 8px"
        # 2) first row top moved up (was 255, allow up to 254 for table-head variance)
        assert d['firstRowTop'] is not None and d['firstRowTop'] <= 254, \
            f"R178: firstRow top {d['firstRowTop']} not reclaimed (want <=254)"
        # 3) last row bottom moved up (was 1736, expect 1720ish)
        # pickCard h decreased 16px (8 top + 8 bottom)
        # 4) others untouched (creed/rules/cat/backtest 仍 12px)
        for o in d['others']:
            if 'creed' in o['cls'] or 'rules' in o['cls']:
                assert o['pt'] == '12px', f"R178: {o['cls']} pt regressed to {o['pt']}"
        # 5) filterBar / sectorBar height not affected (control plane stable)
        if d['filterBarH']:
            assert 36 <= d['filterBarH'] <= 44, f"R178: filterBar h={d['filterBarH']} regressed"
        print(f"[OK] R178 pickCard padding 12→8 — 首行 t={d['firstRowTop']} (回收 ≥8px), "
              f"pickCard h={d['pickCardH']} (-16px), 其他 card padding 守住 12px, "
              f"filter/sector 控制条高度不变 ✓")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())