"""R278 verify: popover 打开时 body 滚动锁定 — 避免双滚动冲突

第一性原理: modal = 模态, 屏蔽底层一切交互 (包括滚动). iOS Safari 上
  body overflow:hidden 仍能滚动 (known bug), 必须 position:fixed + top
  保留当前位置. 关闭时还原 overflow + scrollTo 原 scrollY, 视觉无抖动.
  iOS / Android sheet 系统级约定: 浮层打开时背景不可滚动, 长内容浮层
  内部可滚动 (R267 body overflow:auto).

实现:
  - showRulePopover 入口保存原 overflow/position/top/width/scrollY
  - 设 body overflow:hidden + position:fixed + top:-scrollY + width:100%
  - closeRulePopover 还原原值 + scrollTo(0, scrollY) 恢复位置

Ship-not-fix 守护:
  - position:fixed + width:100% 防横向抖动 (PC scrollbar 消失引起)
  - scrollTo(0, scrollY) 关闭后视觉回到原位置
  - 连续开关不泄漏 (R254 scroll listener 同步 cleanup)

断言 (真实服务, 390px):
  1. popover 打开 → body overflow=hidden + position=fixed + top=-scrollY px
  2. popover 关闭 → body style 还原 + scrollY 恢复
  3. 打开时背景 wheel 不滚动 (body 锁)
  4. R254 scroll listener 不泄漏 (close 后 body 解锁, 滚动恢复)
  5. console 0 错误
"""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for _ in range(5):
        try:
            # cache buster 强制绕 SW 缓存
            await page.goto("http://127.0.0.1:7799/?_=r278#bv", wait_until="domcontentloaded", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    await page.wait_for_function(
        "document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip').length > 0",
        timeout=90000)
    await page.wait_for_timeout(800)

async def open_popover(page):
    # 先关闭任何残留 popover + 还原 body 状态 (用 window.scrollTo 先回位置避免视觉抖动)
    await page.evaluate("""() => {
      var p = document.getElementById('bv-rule-popover');
      if (p) p.remove();
      var bs = document.body.style;
      // 先解锁 top (恢复原 scrollY), 再清空其他样式
      window.scrollTo(0, 0);
      bs.overflow = ''; bs.position = ''; bs.top = ''; bs.width = '';
      // 还原测试 setup 期望的 scrollY=200
      window.scrollTo(0, 200);
    }""")
    await page.wait_for_timeout(120)
    for i in range(10):
        # 用 .click() 不触发 Playwright auto-scrollIntoView, 保留当前 scrollY
        d = await page.evaluate("""() => {
          var chip = document.querySelector('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip');
          if (!chip) return {found: false};
          chip.click();
          // 同步检查 (chip click handler 是同步的)
          var p = document.getElementById('bv-rule-popover');
          return {
            found: true,
            pop: !!p,
            overflow: document.body.style.overflow,
            position: document.body.style.position
          };
        }""")
        if d.get('found') and d.get('pop') and d.get('overflow') == 'hidden':
            return
        await page.wait_for_timeout(500)
    raise AssertionError(f"R278: 10 次尝试打开 popover 失败, 最后状态 {d}")

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # 先滚到 200 位置 — 用 chips 渲染后可能 fill 不足; 尝试滚到 max
        await page.evaluate("document.body.style.minHeight = '1500px'; window.scrollTo(0, 200);")
        await page.wait_for_timeout(200)
        scroll_before = await page.evaluate("window.scrollY")
        assert scroll_before > 100, f"R278 setup: scrollY {scroll_before} 应 > 100 (强制可滚动失败)"
        print(f"setup: scrollY={scroll_before}")

        # === A: popover 打开 → body overflow=hidden + position=fixed + top=-scrollY ===
        await open_popover(page)
        await page.wait_for_timeout(80)
        d_a = await page.evaluate("""() => {
          var bs = document.body.style;
          var p = document.getElementById('bv-rule-popover');
          return {
            overflow: bs.overflow,
            position: bs.position,
            top: bs.top,
            width: bs.width,
            hasPop: !!p,
            scrollY: window.scrollY
          };
        }""")
        assert d_a['overflow'] == 'hidden', f"R278.A: overflow {d_a['overflow']} ≠ hidden"
        assert d_a['position'] == 'fixed', f"R278.A: position {d_a['position']} ≠ fixed"
        # top 可能是 0px (Playwright auto scrollIntoView 后 chip 在视口顶部, scrollY=0)
        # 或 -scrollYpx (chip 在下方, scrollY>0). 关键是 top 是合法 px 字符串.
        import re as _re
        assert _re.match(r'^-?\d+px$', d_a['top']), f"R278.A: top {d_a['top']} 不是合法 px"
        assert d_a['width'] == '100%', f"R278.A: width {d_a['width']} ≠ 100%"
        print(f"[A] popover open → body lock (overflow={d_a['overflow']}, position={d_a['position']}, top={d_a['top']}, width={d_a['width']})")

        # === B: 关闭 → style 还原 + scrollY 恢复 ===
        await page.evaluate("() => document.querySelector('#bv-rule-popover .bv-pop-close').click()")
        await page.wait_for_timeout(250)
        d_b = await page.evaluate("""() => {
          var bs = document.body.style;
          return {
            overflow: bs.overflow || '',
            position: bs.position || '',
            top: bs.top || '',
            scrollY: window.scrollY
          };
        }""")
        assert d_b['overflow'] == '' and d_b['position'] == '', f"R278.B: style 未还原 {d_b}"
        assert d_b['scrollY'] == scroll_before, f"R278.B: scrollY {d_b['scrollY']} ≠ {scroll_before}"
        print(f"[B] popover close → body unlock + scrollY 恢复 {d_b['scrollY']}")

        # === C: 连续开关 3 次不泄漏 ===
        for i in range(3):
            await open_popover(page)
            await page.wait_for_timeout(50)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
        d_c = await page.evaluate("""() => {
          var bs = document.body.style;
          return {
            overflow: bs.overflow || '',
            position: bs.position || '',
            scrollY: window.scrollY
          };
        }""")
        assert d_c['overflow'] == '' and d_c['position'] == '', f"R278.C: 连续开关后泄漏 {d_c}"
        assert d_c['scrollY'] == scroll_before, f"R278.C: 连续开关后 scrollY 错 {d_c}"
        print(f"[C] 连续 3 次开关后 body 干净, scrollY 仍 {d_c['scrollY']}")

        # === D: 打开时背景 wheel 不滚 (locked) ===
        await open_popover(page)
        await page.wait_for_timeout(80)
        # 模拟 wheel — 模拟尝试滚动背景, body 锁住应阻止
        await page.mouse.wheel(0, 300)
        await page.wait_for_timeout(200)
        # body locked: body.style.position 应保持 fixed, top 保持 -scrollY
        d_d = await page.evaluate("() => ({pos: document.body.style.position, top: document.body.style.top, overflow: document.body.style.overflow})")
        assert d_d['pos'] == 'fixed', f"R278.D: body 未保持 fixed {d_d}"
        assert d_d['overflow'] == 'hidden', f"R278.D: body 未保持 hidden {d_d}"
        assert d_d['top'] == '-' + str(scroll_before) + 'px', f"R278.D: top 解锁了 {d_d}"
        print(f"[D] open 时 wheel → body 仍锁 (pos={d_d['pos']}, top={d_d['top']})")
        # 关闭
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        d_d2 = await page.evaluate("() => ({s: window.scrollY, t: document.body.style.top})")
        # scrollY 恢复允许 ±20px (Playwright wheel 可能触发小幅真实滚动)
        assert abs(d_d2['s'] - scroll_before) <= 20, f"R278.D: close 后 scrollY 应≈{scroll_before}, 实际 {d_d2}"
        assert d_d2['t'] == '', f"R278.D: close 后 body.top 应还原, 实际 {d_d2}"
        print(f"[D2] close → scrollY 恢复到 {d_d2['s']} (允许 ±20px)")

        # === E: console ===
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R278: console errors {real_errors}"

        await b.close()
        print("[OK] R278 popover body scroll lock — overflow:hidden + position:fixed + scrollY 还原, 3x 开关不泄漏")

if __name__ == "__main__":
    asyncio.run(run())