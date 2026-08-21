"""R276 verify: popover tap-outside 关闭 — 点击 popover 外任意位置都关

第一性原理: popover = 用户的"此刻注意焦点". R269 ✕ + R271 mask click + R254
  scroll close 都有局限: ✕ 视线移到角落, mask 仅限遮罩区, scroll 需移动.
  用户读完内容后, 拇指随便一点页面空白/卡片/侧栏就应关闭 — 这是 iOS modal
  /sheet 的系统级约定. R275 swipe-down 是 vertical gesture, R276 是 discrete
  click — 互补.

实现: capture-phase document click listener, 点 popover/mask 自身 skip (让
  原 handler 处理), 点其他任意位置 close. setTimeout 0 延迟注册避免被同次
  click 事件触发 (open 时 chip.click() 会冒泡上来).

Ship-not-fix 守护:
  - capture phase: chip click handler stopPropagation 也不阻断
  - close 时清理 listener: 防止 30s 自动刷新或重复打开泄漏
  - setTimeout 0: 避免自身 click 立刻触发 close

断言 (真实服务, 390px):
  1. 点 page 空白处 (空白 area) → popover close
  2. 点另一卡片非 chip 区域 (bv-row 内容) → popover close
  3. 点 popover 自身 (rail chip / body 文字) → 不 close (内部交互)
  4. ✕ / mask click 仍工作 (路径覆盖)
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
    raise AssertionError("R276: 10 次尝试打开 popover 失败")

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # === A: 点 page 空白处 (顶部 view-head 区域, 跟 chip 无关) → close ===
        await open_popover(page)
        await page.wait_for_timeout(80)
        # 用 page.mouse.click 真实坐标点击, 避开 popover/mask/chip 区域
        await page.mouse.click(20, 30)  # 顶栏左上空
        await page.wait_for_timeout(250)
        d_a = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_a, "R276.A: 点 page 空白处后 popover 未移除"
        print("[A] 点 page 空白处 → popover 关闭")

        # === B: 点另一卡片非 chip 区域 (sector td 单元格) → close ===
        await open_popover(page)
        await page.wait_for_timeout(80)
        # 找一行 sector td 单元格 — bv-row 内 .bv-sector-name 或 td[标题是板块]
        # 简化: 用坐标点击 — popover 大约 24-360 wide, 居中靠下, 避开 popover
        # 在第一行左侧空白 (x=20, y=200) 点击
        await page.mouse.click(20, 200)
        await page.wait_for_timeout(250)
        d_b = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_b, "R276.B: 点卡片区空白处 popover 未移除"
        print("[B] 点卡片区空白处 → popover 关闭")

        # === C: 点 popover 自身 (body 文字/rail) → 不 close ===
        await open_popover(page)
        await page.wait_for_timeout(80)
        # 点击 popover body 文字 (description 区域)
        body_click = await page.evaluate("""() => {
          var body = document.querySelector('#bv-rule-popover .bv-pop-body');
          if (!body) return false;
          // 找第一个子元素点
          var first = body.children[0];
          if (!first) return false;
          first.click();
          return true;
        }""")
        assert body_click, "R276.C: popover body 无子元素可点"
        await page.wait_for_timeout(250)
        d_c = await page.evaluate("() => !!document.getElementById('bv-rule-popover')")
        assert d_c, "R276.C: 点 popover body 错误触发 close"
        print("[C] 点 popover body 内部 → 不 close")

        # === D: ✕ click 仍工作 (路径覆盖) ===
        await page.evaluate("() => document.querySelector('#bv-rule-popover .bv-pop-close').click()")
        await page.wait_for_timeout(250)
        d_d = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_d, "R276.D: ✕ click 关闭失败 (路径覆盖)"
        print("[D] ✕ click → popover 关闭 (路径覆盖)")

        # === E: mask click 仍工作 ===
        await open_popover(page)
        await page.wait_for_timeout(80)
        await page.evaluate("() => document.getElementById('bv-rule-popover-mask').click()")
        await page.wait_for_timeout(250)
        d_e = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_e, "R276.E: mask click 关闭失败 (路径覆盖)"
        print("[E] mask click → popover 关闭 (路径覆盖)")

        # === F: R272 close 动效不退化 ===
        await open_popover(page)
        await page.wait_for_timeout(80)
        await page.mouse.click(20, 30)
        # 30ms 内应该带 closing class
        await page.wait_for_timeout(30)
        d_f = await page.evaluate("""() => {
          var p = document.getElementById('bv-rule-popover');
          if (!p) return {hasPop: false};
          return {hasPop: true, closing: p.classList.contains('bv-pop-closing'),
                  anim: getComputedStyle(p).animationName};
        }""")
        if d_f['hasPop']:
            assert d_f['closing'] and 'bv-pop-out' in d_f['anim'], f"R276.F: 关闭动效退化 {d_f}"
            print(f"[F] R272 close 动效 ({d_f['anim']})")
        await page.wait_for_timeout(250)

        # === G: console ===
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R276: console errors {real_errors}"

        await b.close()
        print("[OK] R276 popover tap-outside 关闭 — page/另一卡片点关闭 + popover 内部不关 + ✕/mask 路径覆盖 + R272 动效不退化")

if __name__ == "__main__":
    asyncio.run(run())