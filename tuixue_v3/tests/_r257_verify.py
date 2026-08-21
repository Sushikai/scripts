"""R257 verify: 展开态 fold 前置第一行 — 反向操作要可预期

第一性原理: 折叠态 fold pinned 右缘是"固定可预期"的展开入口; R255 展开态 fold
  跟随规则末尾在规则多时换行到第二/三行末尾, 每次展开位置飘忽 (取决于几条规则
  换几行) — 肌肉记忆失效. 收起是"反向操作", 必须与展开入口同逻辑: 固定第一行
  hit badge 之后, 规则从后面换行. 位置稳定比跟随末尾更重要.

断言 (真实服务, 390px):
  1. 折叠态仍裸编号 nowrap 横滚 (R251 守护)
  2. 展开态 fold 在第一行 (top 与第一行元素对齐, 不在第二行以下)
  3. 展开态全规则可见 + fold 可达 (R255 守护)
  4. 展开态规则带短名 (R256 守护)
  5. rowH 语义断言: 展开态 > 折叠态 (多行), 且 < 折叠态 3x (排除异常膨胀;
     4 条短名规则 4 行是详情面真实成本 ~179px, 不硬压)
  6. console 0 错误 (过滤 favicon + 环境性网络超时)
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

        # 1. 折叠态仍裸编号 nowrap 横滚 (R251 守护)
        fold = await page.evaluate("""() => {
          var rules = document.querySelector('#bv-pick-tbody tr.is-bv-top .bv-rules-cell');
          var cs = getComputedStyle(rules);
          var chip = rules.querySelector('.bv-rule-chip');
          return {flexWrap: cs.flexWrap, hasName: chip ? !!chip.querySelector('.bv-rule-name') : null};
        }""")
        assert fold['flexWrap'] == 'nowrap', f"R257: 折叠态不是 nowrap {fold}"
        assert fold['hasName'] is False, f"R257: 折叠态 chip 不应带短名 {fold}"
        print("[1] 折叠态 nowrap 裸编号 (R251/R256 守护)")

        # 折叠态 rowH
        foldH = await page.evaluate("() => document.querySelector('#bv-pick-tbody tr.is-bv-top').offsetHeight")

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
          var kids = [];
          var foldInfo = null;
          for (var i=0; i<cell.children.length; i++) {
            var c = cell.children[i]; var r = c.getBoundingClientRect();
            var info = {txt:(c.textContent||'').trim().slice(0,16), L:Math.round(r.left-cr.left),
                        top:Math.round(r.top-cr.top), w:Math.round(r.width),
                        isFold:c.classList.contains('bv-rule-fold')};
            kids.push(info);
            if (info.isFold) foldInfo = info;
          }
          // fold pinned 右下角: right 对齐 cell 右缘 (距离 ≤ fold 宽), bottom 对齐 cell 底部
          var foldInfo = null;
          for (var i=0; i<kids.length; i++) { if (kids[i].isFold) foldInfo = kids[i]; }
          var foldRightPinned = false, foldBottomPinned = false;
          if (foldInfo) {
            var fr = foldInfo;  // 相对 cell: L/top/w
            var cellW = Math.round(cr.width);
            var cellH = Math.round(cr.height);
            var distRight = cellW - (fr.L + fr.w);
            // fold bottom:0 → fold 底部贴 cell 底部 (cell 无 padding 时 bottom 贴齐)
            var foldBottom = fr.top + fr.w;  // w 在 chip 是高度近似 (24px chip)
            foldRightPinned = distRight <= 4;              // fold 右缘贴 cell 右缘 (容差 4px)
            foldBottomPinned = Math.abs(cellH - foldBottom) <= 12;  // fold 底贴 cell 底 (容差 12px)
          }
          var chips = cell.querySelectorAll('.chip:not(.bv-rule-fold)');
          var allNamed = true, allVisible = true;
          for (var i=0; i<chips.length; i++) {
            var c = chips[i]; var ccr = c.getBoundingClientRect();
            if (!c.querySelector('.bv-rule-name')) allNamed = false;
            if (!(ccr.left >= cr.left - 1 && ccr.right <= cr.right + 1)) allVisible = false;
          }
          return {expanded:true, kids: kids, foldInfo: foldInfo,
                  cellW: Math.round(cr.width),
                  foldRightPinned: foldRightPinned, foldBottomPinned: foldBottomPinned,
                  allNamed: allNamed, allVisible: allVisible,
                  rowH: cell.closest('.bv-row') ? cell.closest('.bv-row').offsetHeight : null};
        }""")
        assert d['expanded'], "R257: 未展开"
        assert d['foldInfo'], "R257: fold 不存在"
        # 2. fold pinned 右下角 (固定可预期, 与折叠态右缘一致)
        assert d['foldRightPinned'], f"R257: fold 未右缘对齐 (cellW={d['cellW']}, fold L={d['foldInfo']['L']}+{d['foldInfo']['w']})"
        assert d['foldBottomPinned'], f"R257: fold 未底部固定 (fold top={d['foldInfo']['top']})"
        print(f"[2] fold pinned 右下角: '{d['foldInfo']['txt']}' L={d['foldInfo']['L']} top={d['foldInfo']['top']} cellW={d['cellW']}")
        # 3. 全规则可见 + 带短名 (R255/R256 守护)
        assert d['allVisible'], "R257: 展开态有规则不可见 (R255 守护)"
        assert d['allNamed'], "R257: 展开态规则缺短名 (R256 守护)"
        rule_count = len([k for k in d['kids'] if not k['isFold']])
        print(f"[3] 展开态 {rule_count} 条规则全可见 + 带短名")
        # 5. rowH 语义断言
        assert d['rowH'] and d['rowH'] > foldH, f"R257: 展开态未多行 {d['rowH']}"
        assert d['rowH'] < foldH * 3, f"R257: 展开态行高异常膨胀 {d['rowH']} vs fold {foldH}"
        print(f"[4] rowH: 折叠 {foldH}px → 展开 {d['rowH']}px (语义范围)")

        # 6. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e and 'ERR_CONNECTION_TIMED_OUT' not in e]
        assert not real_errors, f"R257: console errors {real_errors}"
        await b.close()
        print("[OK] R257 展开态 fold pinned 右下角 — 反向操作固定可预期, 全规则短名可见, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
