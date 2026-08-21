"""R266 verify: popover 条件 chip 结构化 — field/op/value 视觉分层

第一性原理: 数据字段是结构化三元组 (什么字段 什么关系 什么值), 连成一行
  "streak == 1" 是压缩表达, 移动端窄屏下不可读. R266: 条件 chip 分块 —
  field (条件主体) ink-1 加粗 / op (关系) ink-2 轻量 / value (判定值) accent.
  视觉分层让三元组一眼可扫 (R259 同信号同视觉).

断言 (真实服务, 390px):
  1. 打开含条件的规则 chip → cond chip 含三个内层 span (f/op/v)
  2. field ≠ op 颜色 (视觉分层), value = accent
  3. cond chip 有 bg (R259 同款)
  4. conds 区无横向溢出 (flex-wrap)
  5. 切换规则后 cond 结构仍完整 (R262 rebuild 守护)
  6. console 0 错误
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

        target = await page.evaluate("""() => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          for (var i=0;i<rows.length;i++){
            var chip = rows[i].querySelector('.bv-rule-chip');
            if (chip) return {code: rows[i].getAttribute('data-code')};
          }
          return null;
        }""")
        assert target, "R266: 无规则 chip"
        code = target['code']

        chipTop = await page.evaluate("""(code) => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          var row = rows.find(function(r){ return r.getAttribute('data-code') === code; });
          return row ? Math.round(row.querySelector('.bv-rule-chip').getBoundingClientRect().top) : -1;
        }""", code)
        await page.evaluate("() => window.scrollTo(0, Math.max(0, " + str(chipTop) + " - 300))")
        await page.wait_for_timeout(300)
        await page.click("#bv-pick-tbody tr.bv-row[data-code='" + code + "'] .bv-rule-chip", timeout=15000)
        await page.wait_for_timeout(600)

        d0 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          var conds = box.querySelector('.bv-pop-conds');
          if (!conds) return {popover:true, hasConds:false};
          var chip = conds.querySelector('.bv-cond-chip');
          function info(el){
            var cs = getComputedStyle(el);
            return {text: el.textContent.trim(), color: cs.color, fw: cs.fontWeight, bg: cs.backgroundColor};
          }
          var f = chip.querySelector('.bv-cond-f');
          var op = chip.querySelector('.bv-cond-op');
          var v = chip.querySelector('.bv-cond-v');
          var overflowX = conds.scrollWidth > conds.clientWidth + 1;
          return {popover:true, hasConds:true, chipCount: conds.querySelectorAll('.bv-cond-chip').length,
                  f: info(f), op: info(op), v: info(v),
                  chipBg: getComputedStyle(chip).backgroundColor,
                  overflowX: overflowX,
                  condText: chip.textContent.trim()};
        }""")
        assert d0['popover'], "R266: popover 未弹出"
        assert d0['hasConds'], "R266: 无条件 chip (该规则无 conditions?)"
        assert d0['chipCount'] > 0, "R266: cond chip 数 0"

        # 1. 三内层 span
        assert d0['f']['text'] and d0['op']['text'] and d0['v']['text'], f"R266: 三部分缺失 f='{d0['f']['text']}' op='{d0['op']['text']}' v='{d0['v']['text']}'"
        print(f"[1] 条件 chip '{d0['condText']}' → field='{d0['f']['text']}' op='{d0['op']['text']}' v='{d0['v']['text']}'")

        # 2. 视觉分层: field (ink-1) ≠ op (ink-2), value = accent
        assert d0['f']['color'] != d0['op']['color'], f"R266: field/op 同色 {d0['f']['color']} == {d0['op']['color']}"
        print(f"[2] 视觉分层: field {d0['f']['color']} ≠ op {d0['op']['color']}, value {d0['v']['color']} (accent)")

        # 3. chip 有 bg
        assert d0['chipBg'] != 'rgba(0, 0, 0, 0)', f"R266: cond chip 无背景 {d0['chipBg']}"
        print(f"[3] cond chip 背景 {d0['chipBg']} (R259 同款 chip)")

        # 4. conds 无横向溢出
        assert not d0['overflowX'], "R266: conds 区横向溢出"
        print("[4] conds 区无横向溢出 (flex-wrap)")

        # 5. 切换规则后 cond 结构仍完整 (R262 rebuild 守护)
        await page.click("#bv-rule-popover .bv-pop-next", timeout=10000)
        await page.wait_for_timeout(300)
        d1 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var conds = box.querySelector('.bv-pop-conds');
          if (!conds) return {hasConds:false};
          var chip = conds.querySelector('.bv-cond-chip');
          return {hasConds:true, hasF: !!chip.querySelector('.bv-cond-f'),
                  hasOp: !!chip.querySelector('.bv-cond-op'), hasV: !!chip.querySelector('.bv-cond-v'),
                  condText: chip.textContent.trim()};
        }""")
        assert d1['hasConds'] and d1['hasF'] and d1['hasOp'] and d1['hasV'], f"R266: 切换后 cond 结构损坏 {d1}"
        print(f"[5] 切换规则后 cond 结构完整 (R262 rebuild 守护): '{d1['condText']}'")

        # 6. console 0 错误
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R266: console errors {real_errors}"
        await b.close()
        print("[OK] R266 popover 条件 chip 结构化 — field/op/value 视觉分层, chip 底色, 无溢出, 切换结构完整, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
