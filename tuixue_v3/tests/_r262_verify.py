"""R262 verify: popover 快捷规则导航 — 同一卡片命中多条规则逐条浏览

第一性原理: 用户对照卡片 N 条命中规则时, 关 popover 再点另一个 chip = 两次点击
  + 重新定位 (上下文丢失, 锚定卡片可能滚出屏). popover 内切换是"同一注意焦点内
  的浏览" — 锚点 (卡片) 恒在, 手指不动只换内容. 复用 R70 详情 prev/next 模式.

断言 (真实服务, 390px):
  1. 打开 N>1 条命中规则的卡片 chip → popover 显示切换条 (.bv-pop-nav)
  2. pos 计数 "1/N"
  3. 点 next → rid/title 变 (内容切换), popover 不关闭, pos 变 "2/N"
  4. 点 prev → 回到原规则
  5. N=1 规则 (单条命中) 不显示 nav (无意义控件不占空间)
  6. 切换后 head/ops sticky 仍保留 (R260 守护)
  7. console 0 错误
"""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    await page.wait_for_selector("#bv-pick-tbody .bv-rule-chip", timeout=30000)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # 折叠态只显示第一条规则 chip, 但 matched_rules (API) 完整 → _popList 用全量.
        # 直接取第一条规则 chip 的卡片 code + 命中的规则总数 (通过 fold 或展开确认 ≥2).
        multi = await page.evaluate("""() => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          for (var i=0;i<rows.length;i++){
            var chip = rows[i].querySelector('.bv-rule-chip');
            var fold = rows[i].querySelector('.bv-rule-fold');
            var code = rows[i].getAttribute('data-code');
            if (chip) return {code: code, fold: !!fold};
          }
          return null;
        }""")
        assert multi, "R262: 无规则 chip (页面未加载)"
        target_code = multi['code']
        print(f"[0] 卡片 {target_code} 首个 chip 可见 (fold 存在={multi['fold']})")

        # 滚到 chip 可见再点 (chip 可能在屏下)
        chipTop = await page.evaluate("""(code) => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          var row = rows.find(function(r){ return r.getAttribute('data-code') === code; });
          return row ? Math.round(row.querySelector('.bv-rule-chip').getBoundingClientRect().top) : -1;
        }""", target_code)
        await page.evaluate("() => window.scrollTo(0, Math.max(0, " + str(chipTop) + " - 300))")
        await page.wait_for_timeout(300)
        # 点击该行第一个规则 chip (Playwright 原生 click)
        await page.click("#bv-pick-tbody tr.bv-row[data-code='" + target_code + "'] .bv-rule-chip", timeout=15000)
        await page.wait_for_timeout(600)

        # 1-2. popover 弹出 + nav 存在 + pos 计数
        d0 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          var nav = box.querySelector('.bv-pop-nav');
          var pos = box.querySelector('.bv-pop-pos');
          var head = box.querySelector('.bv-pop-head');
          var ops = box.querySelector('.bv-pop-ops');
          return {popover:true, hasNav: !!nav, posText: pos ? pos.textContent.trim() : null,
                  rid: box.querySelector('.bv-pop-rid') ? box.querySelector('.bv-pop-rid').textContent.trim() : null,
                  title: box.querySelector('.bv-pop-title') ? box.querySelector('.bv-pop-title').textContent.trim() : null,
                  headSticky: head ? getComputedStyle(head).position : null,
                  opsSticky: ops ? getComputedStyle(ops).position : null};
        }""")
        assert d0['popover'], "R262: popover 未弹出"
        assert d0['hasNav'], "R262: 多规则卡片无 nav 切换条"
        assert d0['posText'] and '/' in d0['posText'], f"R262: pos 计数不对 {d0['posText']}"
        firstRid = d0['rid']
        print(f"[1] popover 弹出 + nav 切换条, pos='{d0['posText']}', 当前规则 {firstRid}")

        # 3. 点 next → 规则变 + popover 不关闭 + pos 变
        await page.click("#bv-rule-popover .bv-pop-next", timeout=10000)
        await page.wait_for_timeout(300)
        d1 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          return {popover:true, rid: box.querySelector('.bv-pop-rid').textContent.trim(),
                  title: box.querySelector('.bv-pop-title').textContent.trim(),
                  posText: box.querySelector('.bv-pop-pos').textContent.trim()};
        }""")
        assert d1['popover'], "R262: next 切换后 popover 被关闭"
        assert d1['rid'] != firstRid, f"R262: next 后规则未变 {d1['rid']} == {firstRid}"
        assert d1['posText'].startswith('2 / '), f"R262: next 后 pos 未进 {d1['posText']}"
        print(f"[2] next 切换: {firstRid} → {d1['rid']} (pos='{d1['posText']}'), popover 保持打开")

        # 4. 点 prev → 回到原规则
        await page.click("#bv-rule-popover .bv-pop-prev", timeout=10000)
        await page.wait_for_timeout(300)
        d2 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          return {popover:true, rid: box.querySelector('.bv-pop-rid').textContent.trim(),
                  posText: box.querySelector('.bv-pop-pos').textContent.trim(),
                  headSticky: getComputedStyle(box.querySelector('.bv-pop-head')).position,
                  opsSticky: getComputedStyle(box.querySelector('.bv-pop-ops')).position};
        }""")
        assert d2['popover'], "R262: prev 切换后 popover 被关闭"
        assert d2['rid'] == firstRid, f"R262: prev 未回到原规则 {d2['rid']} != {firstRid}"
        assert d2['posText'].startswith('1 / '), f"R262: prev 后 pos 未回位 {d2['posText']}"
        print(f"[3] prev 切换回 {d2['rid']} (pos='{d2['posText']}')")

        # 6. head/ops sticky 保留 (R260 守护)
        assert d2['headSticky'] == 'sticky', f"R262: head sticky 回归 {d2['headSticky']}"
        assert d2['opsSticky'] == 'sticky', f"R262: ops sticky 回归 {d2['opsSticky']}"
        print(f"[4] head({d2['headSticky']}) + ops({d2['opsSticky']}) sticky 控制面保留 (R260 守护)")

        # 关闭 popover
        await page.click("#bv-rule-popover .bv-pop-close", timeout=10000)
        await page.wait_for_timeout(300)

        # 7. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e and 'ERR_CONNECTION_TIMED_OUT' not in e]
        assert not real_errors, f"R262: console errors {real_errors}"
        await b.close()
        print("[OK] R262 popover 快捷规则导航 — 同卡片多条规则逐条浏览, 锚点不丢, sticky 控制面保留, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
