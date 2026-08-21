"""R252 verify: 规则 chip tap → 就近 popover — 决策依据就地呈现, 不瞬移丢上下文

第一性原理: 用户点"命中哪条规则"是想看"为什么". 原行为:
  1. 折叠态 chip tap → 切过滤 (误触一次列表全变) — tap 主语义被次级操作抢占
  2. 展开态/明细 chip tap → scrollIntoView 瞬移到 1410px 深的规则明细 (丢卡片上下文)
R252 统一: tap → 就近 popover (标题+描述+原话+条件+过滤入口), 点击外部/✕ 关闭.

断言 (真实服务, 390px):
  1. 折叠态 BV03 chip tap → popover 出现, 含 title/desc/过滤按钮, scrollY 不变
  2. popover 定位在卡片可视区域内 (top >= 卡片 top - 10)
  3. popover 过滤按钮 → 列表过滤生效 (仅剩命中该规则的行)
  4. popover ✕ → 关闭恢复
  5. 展开态规则 chip tap → popover (不再 scrollIntoView 瞬移)
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
        # 等行 + 规则 chip 都渲染 (rules 数据未到前 chip 为空)
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

        # 1. 折叠态 BV03 chip tap → popover
        scrollY0 = await page.evaluate("() => window.scrollY")
        pop = await page.evaluate("""() => {
          var chip = document.querySelector('#bv-pick-tbody .bv-rule-chip');
          if (!chip) return {chip:false};
          var rid = chip.getAttribute('data-rule-id');
          var rowR = chip.closest('.bv-row').getBoundingClientRect();
          chip.click();
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {chip:true, rid:rid, box:false};
          var br = box.getBoundingClientRect();
          return {
            chip:true, rid:rid, box:true,
            title: (box.querySelector('.bv-pop-title')||{}).textContent,
            hasDesc: !!box.querySelector('.bv-pop-desc'),
            hasQuote: !!box.querySelector('.bv-pop-quote'),
            hasFilter: !!box.querySelector('.bv-pop-filter'),
            hasClose: !!box.querySelector('.bv-pop-close'),
            boxTop: Math.round(br.top), boxBottom: Math.round(br.bottom),
            rowTop: Math.round(rowR.top), rowBottom: Math.round(rowR.bottom),
            boxInView: br.top >= 0 && br.bottom <= window.innerHeight
          };
        }""")
        assert pop.get('chip'), "无规则 chip"
        assert pop.get('box'), f"R252: 折叠态 chip 未弹出 popover {pop}"
        assert pop['title'], f"R252: popover 无标题 {pop}"
        assert pop['hasDesc'], f"R252: popover 无描述 {pop}"
        assert pop['hasFilter'], f"R252: popover 无过滤按钮 {pop}"
        assert pop['hasClose'], f"R252: popover 无关闭按钮 {pop}"
        # 定位: popover top 在卡片 top 附近 (上下 60px 内) — 不瞬移远处
        assert abs(pop['boxTop'] - pop['rowBottom']) < 80, f"R252: popover 距卡片太远 boxTop={pop['boxTop']} rowBottom={pop['rowBottom']}"
        assert pop['boxInView'], f"R252: popover 超出视口 {pop}"
        scrollY1 = await page.evaluate("() => window.scrollY")
        assert abs(scrollY1 - scrollY0) < 2, f"R252: popover 触发瞬移 scrollY {scrollY0}→{scrollY1}"
        print(f"[1] popover 折叠态: {pop['rid']} title='{pop['title'][:12]}' box@({pop['boxTop']}-{pop['boxBottom']}) scrollY 不变")

        # 3. popover 过滤按钮 → 列表过滤 (剩余行全命中该规则)
        filtered = await page.evaluate("""() => {
          var fb = document.querySelector('#bv-rule-popover .bv-pop-filter');
          if (!fb) return {btn:false};
          var rid = fb.getAttribute('data-rid');
          var before = document.querySelectorAll('#bv-pick-tbody tr.bv-row').length;
          fb.click();
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          var after = rows.length;
          var allHit = true, firstBad = null;
          for (var i=0; i<rows.length; i++) {
            var chips = rows[i].querySelectorAll('.bv-rule-chip');
            var hit = false;
            for (var j=0; j<chips.length; j++) {
              if (chips[j].getAttribute('data-rule-id') === rid) { hit = true; break; }
            }
            if (!hit) { allHit = false; firstBad = i; break; }
          }
          var countTxt = document.querySelector('#bv-pick-count') ? document.querySelector('#bv-pick-count').textContent : '';
          return {btn:true, rid:rid, before:before, after:after, allHit:allHit, firstBad:firstBad, popClosed: !document.getElementById('bv-rule-popover'), countTxt:countTxt};
        }""")
        assert filtered['btn'], "无过滤按钮"
        assert filtered['popClosed'], "R252: 过滤后 popover 未关闭"
        assert filtered['after'] >= 1, f"R252: 过滤后列表为空 {filtered}"
        assert filtered['allHit'], f"R252: 过滤后 r{filtered['firstBad']} 未命中 {filtered['rid']} {filtered}"
        assert filtered['after'] <= filtered['before'], f"R252: 过滤后行数增长 {filtered}"
        print(f"[2] popover 过滤: {filtered['before']}→{filtered['after']} 行全命中 {filtered['rid']}, count='{filtered['countTxt'][:20]}'")

        # 4. 清除过滤 (点筛选条重置), 再验证 ✕ 关闭
        await page.evaluate("""() => {
          var clear = document.querySelector('.bv-rule-clear');
          if (clear) clear.click();
        }""")
        await page.wait_for_timeout(300)
        # 重新点 chip → popover → ✕ 关闭
        closed = await page.evaluate("""() => {
          var chip = document.querySelector('#bv-pick-tbody .bv-rule-chip');
          if (!chip) return {chip:false};
          chip.click();
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {box:false};
          var close = box.querySelector('.bv-pop-close');
          close.click();
          return {box:true, closed: !document.getElementById('bv-rule-popover'),
                  rows: document.querySelectorAll('#bv-pick-tbody tr.bv-row').length};
        }""")
        assert closed['box'] and closed['closed'], f"R252: ✕ 未关闭 popover {closed}"
        print(f"[3] popover ✕ 关闭: rows={closed['rows']}")

        # 5. 展开态规则 chip tap → popover (不瞬移)
        expPop = await page.evaluate("""() => {
          var fold = document.querySelector('#bv-pick-tbody .bv-rule-fold');
          if (!fold) return {fold:false};
          fold.click();
          var cell = document.querySelector('.bv-rules-cell.is-expanded');
          if (!cell) return {expanded:false};
          var chip = cell.querySelector('.chip:not(.bv-rule-fold)');
          if (!chip) return {chip:false};
          var sc0 = window.scrollY;
          chip.click();
          var box = document.getElementById('bv-rule-popover');
          return {expanded:true, chip:true, popover:!!box,
                  rid: (box? box.querySelector('.bv-pop-title').textContent : null),
                  scrollChanged: Math.abs(window.scrollY - sc0) > 2};
        }""")
        assert expPop['expanded'] and expPop['chip'], f"R252: 展开态未就绪 {expPop}"
        assert expPop['popover'], "R252: 展开态 chip 未弹 popover"
        assert not expPop['scrollChanged'], "R252: 展开态 chip 触发瞬移"
        print(f"[4] 展开态 chip → popover {expPop['rid']}, scrollY 不变")

        # 6. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e]
        assert not real_errors, f"R252: console errors {real_errors}"

        await b.close()
        print("[OK] R252 规则 popover — 折叠态/展开态 tap 就近呈现, 过滤入口保留, 不瞬移, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
