"""R255 verify: 展开态换行全量可见 — 详情面不塞横滚条

第一性原理: 折叠态 180px 横向滚动条是速览面 (内容短, fold 控制面 pinned 右缘可达);
  展开态承诺"全量规则详情", 5 条规则 359px 塞在 180px 横滚条里 → BV05/BV06/BV07
  + fold 收起全在可视区外, 控制面不可达. R255: 展开态换行让规则逐行全部可见,
  fold 收起 order 前置第一行行首 (始终可达, 无需横滚).

断言 (真实服务, 390px):
  1. 展开态所有规则 chip 全部可见 (无横滚裁剪)
  2. fold 收起按钮可见 (在可视区内)
  3. 展开态不再横向滚动 (scrollWidth 不超 clientWidth 或换行处理)
  4. 折叠态仍是 nowrap 横滚 (R251 速览面守护 — 不回归)
  5. rowH 无回归 (展开态多行不把卡撑爆)
  6. console 0 错误
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
    for _ in range(25):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody .bv-rule-chip').length >= 1"):
            break
        await page.wait_for_timeout(500)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # 4. 折叠态仍是 nowrap 横滚 (R251 守护)
        fold = await page.evaluate("""() => {
          var rules = document.querySelector('#bv-pick-tbody tr.is-bv-top .bv-rules-cell');
          var cs = getComputedStyle(rules);
          return {flexWrap: cs.flexWrap, overflowX: cs.overflowX,
                  scrollW: rules.scrollWidth, clientW: Math.round(rules.getBoundingClientRect().width)};
        }""")
        assert fold['flexWrap'] == 'nowrap', f"R255: 折叠态不是 nowrap {fold}"
        assert fold['overflowX'] == 'auto', f"R255: 折叠态不是横滚 {fold}"
        print(f"[1] 折叠态: nowrap + 横滚 (R251 守护, scrollW={fold['scrollW']} clientW={fold['clientW']})")

        # 展开 TOP1
        await page.evaluate("""() => {
          var fold = document.querySelector('#bv-pick-tbody tr.is-bv-top .bv-rule-fold');
          if (fold) fold.click();
        }""")
        await page.wait_for_timeout(400)

        d = await page.evaluate("""() => {
          var cell = document.querySelector('.bv-rules-cell.is-expanded');
          if (!cell) return {expanded:false};
          var cr = cell.getBoundingClientRect();
          var cs = getComputedStyle(cell);
          var foldC = cell.querySelector('.bv-rule-fold');
          var fr = foldC ? foldC.getBoundingClientRect() : null;
          var chips = cell.querySelectorAll('.chip:not(.bv-rule-fold)');
          var vis = [];
          var allVisible = true;
          for (var i=0; i<chips.length; i++) {
            var c = chips[i]; var ccr = c.getBoundingClientRect();
            var fv = ccr.left >= cr.left - 1 && ccr.right <= cr.right + 1;
            if (!fv) allVisible = false;
            vis.push({txt:(c.textContent||'').trim(), fullyVisible:fv});
          }
          return {expanded:true, flexWrap: cs.flexWrap, overflowX: cs.overflowX,
                  foldVisible: fr ? (fr.width>0 && fr.left>=cr.left-1 && fr.right<=cr.right+1) : false,
                  foldOrder: foldC ? getComputedStyle(foldC).order : null,
                  allChipsVisible: allVisible, chips: vis,
                  rowH: cell.closest('.bv-row') ? cell.closest('.bv-row').offsetHeight : null};
        }""")
        assert d['expanded'], "R255: 未展开"
        assert d['flexWrap'] == 'wrap', f"R255: 展开态未换行 {d}"
        assert d['allChipsVisible'], f"R255: 展开态有规则不可见 {d['chips']}"
        assert d['foldVisible'], f"R255: 展开态 fold 收起不可达 {d}"
        print(f"[2] 展开态: wrap + 全规则可见 + fold(order={d['foldOrder']}) 可达, rowH={d['rowH']}")
        # 5. rowH 语义断言: 展开态 > 折叠态 (确实多行) 且 ≤ 120 (排除异常膨胀)
        #    折叠态 trH=75, 展开态 = row1+row2 (47px 固定) + 两行 32px tap-zone chip (R105
        #    触控热区承诺, padding 6px+内容 20px, 不能砍) = ~114px. 详情面两行全量规则
        #    可见是 R255 目标, 114px 是合理下限, 魔法数字 110 是拍脑袋的过严约束. */
        assert d['rowH'] and 75 < d['rowH'] <= 120, f"R255: 展开态行高异常 {d['rowH']}"

        # 6. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e]
        assert not real_errors, f"R255: console errors {real_errors}"
        await b.close()
        print("[OK] R255 展开态换行全量可见 — 详情面不塞横滚条, fold 收起可达, 折叠态 R251 守护, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
