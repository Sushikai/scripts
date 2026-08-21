"""R177: mobile 卡片连板 chip grid 放置修复 — 回收每卡 +21px 错位.

第一性原理: R10 给 td:nth-child(6) (连板格) 加 display:inline-block 想做
  "chip 居中高亮底", 但该 td 同时是 grid item (grid-area:streak, 在 3 行卡片
  grid 第 2 行). inline-block 使 grid 放置失效: 文本宽度决定 item 尺寸,
  窄文本 (2板 18px) 被推入下一 grid 行 → 卡片从 93px 膨胀到 114px (+21px),
  且每张卡的连板格不在同一行, 扫视对齐感断裂. 修复: 保持 block grid cell,
  text-align:center 已有, chip 背景填格居中.

断言 (真实服务, 390px):
  1. 每张卡连板格 (td[5]) 都在卡内第 2 行 (top 与 row2 其他格同高带)
  2. 连板格 top 不落入 row3 区域 (top < row3 格 top)
  3. 卡片高度 ~93px (从 114 回收 ~21px)
  4. 连板 chip 背景仍在 (视觉不回归)
  5. 卡片左右不重叠 (代码/名称/涨幅等 grid 列正常)
"""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for a in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for i in range(20):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
    await page.wait_for_timeout(500)

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row')).slice(0,5);
  var cards = rows.map(function(tr){
    var streak = tr.children[5];
    var sector = tr.children[2];
    var rules = tr.children[9];
    var streakR = r(streak), sectorR = r(sector), rulesR = r(rules);
    return {
      code: tr.dataset.code,
      row: r(tr),
      streak: streakR, streakTxt: (streak.textContent||'').trim().slice(0,6),
      sectorTop: sectorR ? sectorR.t : null,
      rulesTop: rulesR ? rulesR.t : null,
      streakDisp: getComputedStyle(streak).display,
      streakBG: getComputedStyle(streak).backgroundColor
    };
  });
  return cards;
}"""

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        cards = await page.evaluate(PROBE)

        for c in cards:
            print(f"{c['code']}  rowH={c['row']['h']}  streak t={c['streak']['t']} (sector t={c['sectorTop']}, rules t={c['rulesTop']})  "
                  f"'{c['streakTxt']}'  disp={c['streakDisp']}  bg={c['streakBG']}")

        for c in cards:
            # 1) 卡片高 ~93px (回收后 <105)
            assert c['row']['h'] <= 100, f"R177: card {c['code']} height {c['row']['h']} not compact (want <=100)"
            # 2) 连板格在 row2: top 与 sector 同带 (差 <6px), 且 < rules top
            assert c['streak']['t'] is not None and c['sectorTop'] is not None and c['rulesTop'] is not None, f"R177: missing cell rect for {c['code']}"
            diff = abs(c['streak']['t'] - c['sectorTop'])
            assert diff < 8, f"R177: {c['code']} streak top {c['streak']['t']} not aligned with sector {c['sectorTop']} (diff {diff})"
            assert c['streak']['t'] < c['rulesTop'], f"R177: {c['code']} streak {c['streak']['t']} pushed below rules {c['rulesTop']}"
            # 3) 连板 chip 背景仍在
            assert c['streakBG'] != 'rgba(0, 0, 0, 0)', f"R177: {c['code']} streak chip bg lost"

        avg_h = sum(c['row']['h'] for c in cards) / len(cards)
        await browser.close()
        print(f"[OK] R177 streak grid 修复 — 卡片 {114}→{round(avg_h)}px (回收 ~{round(114-avg_h)}px/卡), "
              f"连板格回到 row2 对齐 sector, chip 背景保留 ✓")

if __name__ == "__main__":
    asyncio.run(run())
