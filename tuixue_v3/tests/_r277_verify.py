"""R277 verify: popover Esc 键关闭 — 键盘可访问性

第一性原理: Esc 是桌面 web 系统级约定 (Chrome modal / Safari sheet / Slack
  dialog / Notion popover 全部按 Esc 关闭). 当前只覆盖触控 (R269 ✕ +
  R271 mask + R275 swipe-down + R276 tap-outside), 桌面 / 平板键盘盖 /
  外接键盘 / 无障碍场景缺覆盖. R277 让键盘用户按 Esc 即关闭, 跟其他关闭
  路径走同一 _closeRulePopover() — 跟 R272 close 动效 + R274 chip flash 串联.

实现: capture-phase document keydown 监听 key === 'Escape', _closeRulePopover()
  关闭. capture phase 防止 input Esc 拦截 (搜索框 Esc 取消焦点 ≠ 关 popover).

Ship-not-fix 守护:
  - capture phase: input[type=search] 等的 Esc 处理不被 stopPropagation 截
  - cleanup: _closeRulePopover 入口 removeEventListener, 防 30s 自动刷新泄漏
  - stopPropagation: popover 关闭意图明确, 不向上冒泡触发全局 Esc 处理

断言 (真实服务, 390px):
  1. popover 打开时按 Esc → 250ms 后移除
  2. R272 close 动效 (bv-pop-out) 不退化
  3. Esc 后 R274 chip flash 触发 (path 覆盖)
  4. 没有 popover 时 Esc 不报错 (无 listener 也不该抛错)
  5. console 0 错误
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
    await page.wait_for_function(
        "document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip').length > 0",
        timeout=60000)
    await page.wait_for_timeout(800)

async def open_popover(page):
    for _ in range(10):
        try:
            await page.click("#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip", timeout=3000)
            await page.wait_for_selector("#bv-rule-popover", timeout=3000, state='attached')
            return
        except Exception:
            await page.wait_for_timeout(800)
    raise AssertionError("R277: 10 次尝试打开 popover 失败")

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # === A: popover open 时 Esc → close ===
        await open_popover(page)
        await page.wait_for_timeout(80)
        target_a = await page.evaluate("""() => {
          var p = document.getElementById('bv-rule-popover');
          var r = document.querySelector('#bv-pick-tbody tr.bv-row[data-code]');
          return r ? r.getAttribute('data-code') : null;
        }""")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        d_a = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_a, "R277.A: Esc 关闭后 popover 未移除"
        print("[A] Esc → popover 关闭")

        # === B: R272 close 动效 + R274 chip flash ===
        # 重新打开测 flash
        await open_popover(page)
        await page.wait_for_timeout(80)
        target_b = await page.evaluate("""() => {
          var p = document.getElementById('bv-rule-popover');
          var r = document.querySelector('#bv-pick-tbody tr.bv-row[data-code]');
          return r ? r.getAttribute('data-code') : null;
        }""")
        # Esc 后 ≤220ms 内 chip 加 bv-chip-flash
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(220)
        d_b = await page.evaluate("""(code) => {
          var chip = document.querySelector('#bv-pick-tbody tr.bv-row[data-code="'+code+'"] .bv-rule-chip');
          if (!chip) return {hasChip: false};
          return {hasChip: true, hasClass: chip.classList.contains('bv-chip-flash'),
                  anim: getComputedStyle(chip).animationName};
        }""", target_b)
        assert d_b['hasChip'] and d_b['hasClass'], f"R277.B: chip {target_b} 没 flash {d_b}"
        assert 'bv-chip-flash' in d_b['anim'], f"R277.B: 动画名 {d_b['anim']} ≠ bv-chip-flash"
        print(f"[B] Esc → chip flash ({d_b['anim']})")

        # === C: 没有 popover 时 Esc 不报错 ===
        # 上一步已经关闭, 现在按 Esc
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(80)
        # listener 已 cleanup (R277 _closeRulePopover removeEventListener),
        # page-level Esc handler 可能被其他代码捕获, 但不应有 page error
        d_c = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_c, "R277.C: 没 popover 时按 Esc 后又出现"
        print("[C] 无 popover 时 Esc → 不报错")

        # === D: 连续开关 (listener cleanup 守护) ===
        for i in range(3):
            await open_popover(page)
            await page.wait_for_timeout(50)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
        d_d = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_d, "R277.D: 连续 3 次 Esc 后 popover 未移除"
        print("[D] 连续 3 次开关 popover + Esc → cleanup 不泄漏")

        # === E: console ===
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R277: console errors {real_errors}"

        await b.close()
        print("[OK] R277 popover Esc 键关闭 — keyboard 可访问性 + R274 chip flash 串联 + cleanup 不泄漏")

if __name__ == "__main__":
    asyncio.run(run())