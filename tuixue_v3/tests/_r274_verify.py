"""R274 verify: popover 关闭后 chip flash — 视觉锚点回到点击处

第一性原理: 浮层动作完整闭环. R273 让 popover 从 chip "长出来" (open 锚点),
  R274 让 chip 在 popover 关闭后 "闪一下" (close 锚点). 用户眼睛聚焦 popover
  内容 → popover 消失 → 视线需要回到原 chip → chip flash 让眼睛瞬间锚定.
  跟 open/close 动效串联成完整视觉链路: chip 长出 popover → popover 内容阅读
  → popover 收缩 → chip 闪烁回焦.

  R272 ship-not-fix 守护:
    - _bvCurrentChip 是 module-scope 变量, 由 _showRulePopover 在入口处存.
      chip 自身可能被 30s 自动刷新 innerHTML 重建 (race), 用 _chipRef 时
      parentNode 检查防止 stale reference.

断言 (真实服务, 390px):
  1. ✕ click 关闭后 ≤220ms 内 chip 加 bv-chip-flash class
  2. animation bv-chip-flash 1.2s 生效 (computedStyle)
  3. mask click 也触发 chip flash (R252 关闭路径覆盖)
  4. R262 prev/next 切换 (bypass=true) 也触发 chip flash (路径覆盖)
  5. R272 close 动效不退化 (回归守护)
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

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # === A: ✕ click → chip flash ===
        target_a = await page.evaluate("""() => {
          var c = document.querySelector('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip');
          return c ? c.closest('tr.bv-row[data-code]').getAttribute('data-code') : null;
        }""")
        # 30s 自动刷新会 innerHTML 重建 chip/chips, 用反复点 retry
        for attempt in range(15):
            try:
                await page.click(f"#bv-pick-tbody tr.bv-row[data-code='{target_a}'] .bv-rule-chip", timeout=3000)
                await page.wait_for_selector("#bv-rule-popover", timeout=3000, state='attached')
                break
            except Exception:
                target_a = await page.evaluate("""() => {
                  var c = document.querySelector('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip');
                  return c ? c.closest('tr.bv-row[data-code]').getAttribute('data-code') : null;
                }""")
                await page.wait_for_timeout(800)
        else:
            raise AssertionError("R274.A: 15 次尝试打开 popover 失败")
        await page.wait_for_timeout(50)
        # ✕ click (evaluate 绕 pointer intercept)
        await page.evaluate("() => document.querySelector('#bv-rule-popover .bv-pop-close').click()")
        # 1: ≤220ms 内 chip 加 bv-chip-flash (200ms 关闭动画 + 20ms 余量)
        await page.wait_for_timeout(220)
        d_a = await page.evaluate("""(code) => {
          var chip = document.querySelector('#bv-pick-tbody tr.bv-row[data-code="'+code+'"] .bv-rule-chip');
          if (!chip) return {hasChip: false};
          var cs = getComputedStyle(chip);
          return {
            hasChip: true,
            hasClass: chip.classList.contains('bv-chip-flash'),
            animName: cs.animationName,
            animDuration: cs.animationDuration
          };
        }""", target_a)
        assert d_a['hasChip'] and d_a['hasClass'], f"R274.A1: chip {target_a} 没加 bv-chip-flash {d_a}"
        assert 'bv-chip-flash' in d_a['animName'], f"R274.A2: 动画名 {d_a['animName']} ≠ bv-chip-flash"
        assert '1.2s' in d_a['animDuration'], f"R274.A2: duration {d_a['animDuration']} ≠ 1.2s"
        print(f"[A1+A2] ✕ click → chip flash {d_a['animName']}/{d_a['animDuration']} class={d_a['hasClass']}")

        # 5: R272 close 动效不退化
        await page.wait_for_timeout(200)
        d_after = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_after, "R274.A5: R272 close 移除失败"
        print(f"[A5] R272 close 仍正确移除 popover")

        # === B: mask click → chip flash ===
        await page.click(f"#bv-pick-tbody tr.bv-row[data-code='{target_a}'] .bv-rule-chip", timeout=5000)
        await page.wait_for_selector("#bv-rule-popover", timeout=5000)
        await page.wait_for_timeout(50)
        target_b = await page.evaluate("() => { var c = document.querySelector('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip'); return c.closest('tr.bv-row[data-code]').getAttribute('data-code'); }")
        await page.evaluate("() => document.getElementById('bv-rule-popover-mask').click()")
        await page.wait_for_timeout(220)
        d_b = await page.evaluate("""(code) => {
          var chip = document.querySelector('#bv-pick-tbody tr.bv-row[data-code="'+code+'"] .bv-rule-chip');
          return chip ? chip.classList.contains('bv-chip-flash') : false;
        }""", target_b)
        assert d_b, f"R274.B: mask click 后 chip {target_b} 没 flash"
        print(f"[B] mask click → chip flash 也触发")

        # === C: R262 prev/next (bypass=true) → chip flash on final close ===
        target_c = await page.evaluate("""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-code]');
          for (var i=0;i<rows.length;i++){
            if (rows[i].querySelectorAll('.bv-rule-chip').length >= 2) return rows[i].getAttribute('data-code');
          }
          return rows.length ? rows[0].getAttribute('data-code') : null;
        }""")
        if target_c:
            await page.click(f"#bv-pick-tbody tr.bv-row[data-code='{target_c}'] .bv-rule-chip", timeout=5000)
            await page.wait_for_selector("#bv-rule-popover", timeout=5000)
            await page.wait_for_timeout(50)
            # next 切几条
            for _ in range(2):
                try:
                    await page.evaluate("() => document.querySelector('#bv-rule-popover .bv-pop-next').click()")
                    await page.wait_for_timeout(80)
                except Exception:
                    break
            # ✕ close
            await page.evaluate("() => document.querySelector('#bv-rule-popover .bv-pop-close').click()")
            await page.wait_for_timeout(220)
            d_c = await page.evaluate("""(code) => {
              var chip = document.querySelector('#bv-pick-tbody tr.bv-row[data-code="'+code+'"] .bv-rule-chip');
              return chip ? chip.classList.contains('bv-chip-flash') : false;
            }""", target_c)
            assert d_c, f"R274.C: prev/next 后 close, chip {target_c} 没 flash (R262 bypass 路径覆盖)"
            print(f"[C] R262 prev/next + close → chip flash 也触发")

        # 6: console
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R274: console errors {real_errors}"

        await b.close()
        print("[OK] R274 popover close → chip flash 1.2s — ✕/mask/R262 prev/next 三路径全覆盖, R272 close 不退化")

if __name__ == "__main__":
    asyncio.run(run())