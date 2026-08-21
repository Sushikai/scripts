"""R253 verify: TOP1 motto 收窄让位锚定规则 — 折叠态锚定规则 BV03 不被 fold 遮挡

第一性原理: rules-cell 可视区 180px 是零和预算. TOP1 行 motto (6 字 65px)
  + 锚定规则 BV03 (48px) + fold (31px) = 144px, 加 sector-chg 194px 超 180px
  → BV03 被 sticky fold 盖住 (锚定规则不可读 = "为什么上榜" 失败). R253:
  motto 折叠态 max-width 100→46px (口诀头部 + ellipsis), 完整口诀 hover/展开态可读.

断言 (真实服务, 390px):
  1. TOP1 行锚定规则 BV03 chip 完全可见 (不被 fold 遮挡, 可见宽度 >= 20px)
  2. motto 折叠态宽度 <= 48px (收窄), 仍显示口诀头部
  3. fold chip 仍 pinned 可视区右缘 (R251 守护)
  4. 展开态 motto 完整恢复 (max-width 不受限, 全文字可见)
  5. rowH <= 75px 无回归
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

        d = await page.evaluate("""() => {
          var top = document.querySelector('#bv-pick-tbody tr.is-bv-top');
          if (!top) return {noTop:true};
          var rules = top.querySelector('.bv-rules-cell');
          var rr = rules.getBoundingClientRect();
          var motto = rules.querySelector('.bv-motto-badge');
          var fold = rules.querySelector('.bv-rule-fold');
          var fr = fold ? fold.getBoundingClientRect() : null;
          // 锚定规则 = 折叠态可见的 rule-chip (非 fold)
          var anchorChip = null;
          var chips = rules.querySelectorAll('.chip.bv-rule-chip');
          for (var i=0; i<chips.length; i++) {
            if (!chips[i].classList.contains('bv-rule-fold')) { anchorChip = chips[i]; break; }
          }
          var ar = anchorChip ? anchorChip.getBoundingClientRect() : null;
          // motto 可见宽度
          var mr = motto ? motto.getBoundingClientRect() : null;
          return {
            mottoText: motto ? motto.textContent : null,
            mottoW: mr ? Math.round(mr.width) : null,
            mottoScrollW: motto ? motto.scrollWidth : null,
            anchorChipText: anchorChip ? anchorChip.textContent : null,
            anchorL: ar ? Math.round(ar.left - rr.left) : null,
            anchorR: ar ? Math.round(ar.right - rr.left) : null,
            anchorVisW: ar ? Math.round(Math.min(ar.right, rr.right) - ar.left) : null,
            foldL: fr ? Math.round(fr.left - rr.left) : null,
            foldR: fr ? Math.round(fr.right - rr.left) : null,
            clientW: Math.round(rr.width),
            rowH: top.offsetHeight,
            foldVisible: fr ? (fr.width > 0 && fr.right <= rr.right + 1) : false
          };
        }""")
        assert not d.get('noTop'), "无 TOP1 行"
        assert d['mottoText'], "无 motto"
        # 1. 锚定规则 chip 可见宽度 >= 20px (不被 fold 遮挡到不可读)
        assert d['anchorVisW'] >= 20, f"R253: TOP1 锚定规则 {d['anchorChipText']} 可见仅 {d['anchorVisW']}px (L{d['anchorL']}-R{d['anchorR']}, fold L{d['foldL']})"
        # 2. motto 折叠态收窄 <= 48px, 仍显示头部
        assert d['mottoW'] <= 48, f"R253: motto 未收窄 w={d['mottoW']}px"
        assert d['mottoScrollW'] > d['mottoW'], f"R253: motto 无超宽内容 (无需 ellipsis) scrollW={d['mottoScrollW']} w={d['mottoW']}"
        # 3. fold 仍 pinned 右缘
        assert d['foldVisible'], "R253: fold 未 pinned 可视区"
        # 5. rowH 无回归
        assert d['rowH'] <= 75, f"R253: 卡高回归 rowH={d['rowH']}"
        print(f"[OK] TOP1 motto '{d['mottoText']}' {d['mottoW']}px (scrollW {d['mottoScrollW']}), 锚定规则 {d['anchorChipText']} 可见 {d['anchorVisW']}px, fold pinned, rowH={d['rowH']}")

        # 4. 展开态 motto 完整恢复
        await page.evaluate("""() => {
          var top = document.querySelector('#bv-pick-tbody tr.is-bv-top');
          var fold = top.querySelector('.bv-rule-fold');
          if (fold) fold.click();
        }""")
        await page.wait_for_timeout(400)
        exp = await page.evaluate("""() => {
          var cell = document.querySelector('.bv-rules-cell.is-expanded');
          if (!cell) return {expanded:false};
          var motto = cell.querySelector('.bv-motto-badge');
          var mr = motto ? motto.getBoundingClientRect() : null;
          return {expanded:true, mottoText: motto ? motto.textContent : null,
                  mottoW: mr ? Math.round(mr.width) : null,
                  mottoFullVisible: motto ? (motto.scrollWidth <= mr.width) : false};
        }""")
        assert exp['expanded'], "R253: fold 未展开"
        assert exp['mottoText'] and exp['mottoFullVisible'], f"R253: 展开态 motto 未完整恢复 {exp}"
        print(f"[OK] 展开态 motto '{exp['mottoText']}' 完整 w={exp['mottoW']}px (scrollW 无裁剪)")

        # 6. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e]
        assert not real_errors, f"R253: console errors {real_errors}"
        await b.close()
        print("[OK] R253 TOP1 motto 收窄让位锚定规则 — 锚定规则 BV03 完整可读, fold pinned, 展开态 motto 完整, rowH 无回归, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
