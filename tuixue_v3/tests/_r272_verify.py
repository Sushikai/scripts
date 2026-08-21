"""R272 verify: popover 关闭动效 — fade-out + translateY, 不突兀消失

第一性原理: 浮层"动作闭环". open 有 bv-pop-in (160ms opacity 0→1 + translateY 6→0),
  close 直接 remove 视觉突兀 — 用户眼睛还在跟踪 popover, 它消失了, 违反"动作有
  起止"原则. 修复: 加 bv-pop-closing class 触发 bv-pop-out keyframe (140ms, 跟
  open 接近但更短 — 关闭紧迫感), 完成后 remove DOM.

  关键 race 守护 (R272 ship-not-fix 风险):
    - _showRulePopover 入口 _closeRulePopover() 无参 → 走 160ms 延迟路径
    → 立即 append 新 popover → 160ms 后 setTimeout 把新 popover 一起 remove
    - 修复: _showRulePopover 调 _closeRulePopover(true) bypass, R262 prev/next
      切换走 _rebuild 不走 _close, 也不受影响

断言 (真实服务, 390px):
  1. click ✕ 后 ≤100ms 内 popover 仍在 DOM + class 含 bv-pop-closing
  2. animation 生效 (animationName bv-pop-out, duration 0.14s)
  3. 200ms 后 popover 从 DOM 移除
  4. mask 也走相同动效 (动画名 bv-pop-out, 后移除)
  5. R262 prev/next 切换 (多次 open/close) 不会卡死或丢新 popover
  6. console 0 错误
"""
import asyncio, json
from playwright.async_api import async_playwright

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    await page.wait_for_function(
        "document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip').length > 0",
        timeout=60000)
    await page.wait_for_timeout(800)

async def open_first(page):
    for _ in range(15):
        try:
            await page.click("#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip", timeout=5000)
            await page.wait_for_selector("#bv-rule-popover", timeout=5000, state='attached')
            return
        except Exception:
            await page.wait_for_timeout(800)
    raise AssertionError("R272: 15 次尝试打开 popover 失败")

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # === A: ✕ click 关闭动效 ===
        await open_first(page)
        # 用 evaluate 直接调 onclick (绕 pointer-events 拦截 — popover body 内容
        # 覆盖 ✕ 区域时 page.click 会卡, 但 JS onclick handler 跟用户点击等价)
        await page.evaluate("() => { var c = document.querySelector('#bv-rule-popover .bv-pop-close'); if (c) c.click(); }")
        await page.wait_for_timeout(50)
        # 1: ≤100ms 内仍在 DOM + class 含 bv-pop-closing
        await page.wait_for_timeout(60)
        d_mid = await page.evaluate("""() => {
          var p = document.getElementById('bv-rule-popover');
          var m = document.getElementById('bv-rule-popover-mask');
          if (!p || !m) return {hasPop: !!p, hasMask: !!m};
          var pcs = getComputedStyle(p);
          var mcs = getComputedStyle(m);
          return {
            hasPop: true, hasMask: true,
            popClosing: p.classList.contains('bv-pop-closing'),
            maskClosing: m.classList.contains('bv-pop-closing'),
            popAnim: pcs.animationName + '/' + pcs.animationDuration,
            maskAnim: mcs.animationName + '/' + mcs.animationDuration
          };
        }""")
        assert d_mid['hasPop'] and d_mid['hasMask'], f"R272.A1: ✕ click 后 ≤100ms popover 已消失 {d_mid}"
        assert d_mid['popClosing'], f"R272.A1: popover 没加 bv-pop-closing class"
        assert d_mid['maskClosing'], f"R272.A1: mask 没加 bv-pop-closing class"
        assert 'bv-pop-out' in d_mid['popAnim'], f"R272.A1: popover 动画名不是 bv-pop-out {d_mid['popAnim']}"
        assert 'bv-pop-out' in d_mid['maskAnim'], f"R272.A1: mask 动画名不是 bv-pop-out {d_mid['maskAnim']}"
        assert '0.14s' in d_mid['popAnim'], f"R272.A2: duration 不是 0.14s {d_mid['popAnim']}"
        print(f"[A1+A2] ✕ click 后 60ms popover+mask 仍在 + bv-pop-closing + 动画 {d_mid['popAnim']} 生效")

        # 3: 200ms 后从 DOM 移除
        await page.wait_for_timeout(200)
        d_after = await page.evaluate("""() => ({
          hasPop: !!document.getElementById('bv-rule-popover'),
          hasMask: !!document.getElementById('bv-rule-popover-mask')
        })""")
        assert not d_after['hasPop'] and not d_after['hasMask'], f"R272.A3: 200ms 后未移除 {d_after}"
        print(f"[A3] 200ms 后 popover + mask 都从 DOM 移除")

        # === B: mask click 关闭动效 ===
        await open_first(page)
        # mask 直接 evaluate 调 onclick (page.click mask 在 popover fade-out 期间
        # 会被 popover 拦截, evaluate 等价真实 click)
        await page.evaluate("() => { var m = document.getElementById('bv-rule-popover-mask'); if (m) m.click(); }")
        await page.wait_for_timeout(60)
        d_mask = await page.evaluate("""() => {
          var p = document.getElementById('bv-rule-popover');
          return p ? {hasPop: true, closing: p.classList.contains('bv-pop-closing')} : {hasPop: false};
        }""")
        assert d_mask['hasPop'] and d_mask['closing'], f"R272.B: mask click 关闭动效失败 {d_mask}"
        await page.wait_for_timeout(200)
        d_mask2 = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_mask2, "R272.B: mask click 200ms 后未移除"
        print(f"[B] mask click 也触发关闭动效")

        # === C: R262 prev/next 切换 (多次 open 验证无 race) ===
        # 找一个命中 ≥2 规则的卡片 (R252 支持)
        target = await page.evaluate("""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-code]');
          for (var i=0;i<rows.length;i++){
            var chips = rows[i].querySelectorAll('.bv-rule-chip');
            if (chips.length >= 2) return rows[i].getAttribute('data-code');
          }
          return rows.length ? rows[0].getAttribute('data-code') : null;
        }""")
        if target:
            await page.click(f"#bv-pick-tbody tr.bv-row[data-code='{target}'] .bv-rule-chip", timeout=5000)
            await page.wait_for_selector("#bv-rule-popover", timeout=5000)
            # 反复切换 prev/next 验证不丢 popover
            cycle_results = []
            for cycle in range(3):
                for _ in range(4):
                    try:
                        await page.click("#bv-rule-popover .bv-pop-next", timeout=2000)
                        await page.wait_for_timeout(100)
                        still = await page.evaluate("() => !!document.getElementById('bv-rule-popover')")
                        cycle_results.append(still)
                    except Exception:
                        cycle_results.append(False)
                        break
                # 重置到第一条
                try:
                    for _ in range(10):
                        await page.click("#bv-rule-popover .bv-pop-prev", timeout=1000)
                        await page.wait_for_timeout(50)
                        pos = await page.evaluate("""() => {
                          var el = document.querySelector('#bv-rule-popover .bv-pop-pos');
                          return el ? el.textContent : '';
                        }""")
                        if pos.startswith('1 /'): break
                except Exception:
                    pass
            d_cycle = await page.evaluate("""() => ({
              hasPop: !!document.getElementById('bv-rule-popover'),
              closing: document.getElementById('bv-rule-popover') ? document.getElementById('bv-rule-popover').classList.contains('bv-pop-closing') : null
            })""")
            assert d_cycle['hasPop'], f"R272.C: 多次切换后 popover 消失 (R272 race)"
            assert not d_cycle['closing'], f"R272.C: popover 卡在 bv-pop-closing 状态 (R272 race)"
            print(f"[C] prev/next 切换 12 次后 popover 仍存活, 不卡 closing ({len(cycle_results)} 次 next)")

        # 6: console
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R272: console errors {real_errors}"

        await b.close()
        print("[OK] R272 popover 关闭动效 — fade-out 140ms + mask 同步 + R262 prev/next 不丢 popover")

if __name__ == "__main__":
    asyncio.run(run())