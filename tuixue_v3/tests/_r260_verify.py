"""R260 verify: popover head sticky — ✕ 关闭恒可达

第一性原理: popover 是"此刻注意焦点"的就地答案 (R254), 其操作闭环 (✕ 关闭 /
  🔍 过滤) 必须真正可达. 内容 scrollable 时 head (static) 随内容滚走 → 用户读到
  一半想关 popover 却找不到 ✕ (控制面不可达). R260: head sticky top 吸附, ✕ 恒
  在顶部; 与 ops sticky bottom (R258) 形成控制面夹层.

断言 (真实服务, 390px):
  1. popover 弹出且内容可滚动 (scrollable, 触发 sticky 需求)
  2. head sticky top (computed position: sticky)
  3. 滚动到 popover 内容深处后, ✕ close 仍可见 (在 popover 可视区内)
  4. head 背景不透明 (遮住滚过内容)
  5. 打开 popover 时 ✕ 正常 (head 未被负 margin 裁掉)
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

        await page.click("#bv-pick-tbody .bv-rule-chip")
        await page.wait_for_timeout(500)

        # 1. 打开状态: head sticky + scrollable + close 初始可见
        d0 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var head = box.querySelector('.bv-pop-head');
          var close = box.querySelector('.bv-pop-close');
          var hcs = getComputedStyle(head);
          var br = box.getBoundingClientRect();
          var cr = close.getBoundingClientRect();
          return {headPos: hcs.position, headBg: hcs.backgroundColor,
                  scrollable: box.scrollHeight > box.clientHeight + 1,
                  closeVisible: cr.top >= br.top - 1 && cr.bottom <= br.bottom + 1,
                  closeInHead: !!head.querySelector('.bv-pop-close'),
                  headNotClipped: cr.top >= br.top - 1};
        }""")
        assert d0['scrollable'], "R260: 内容不可滚动, sticky 无需求 (数据不足)"
        print(f"[1] 内容可滚动 (scrollH > clientH), head sticky 有需求")
        assert d0['headPos'] == 'sticky', f"R260: head 未 sticky {d0['headPos']}"
        assert d0['closeVisible'], "R260: 初始 ✕ 不可见"
        assert d0['closeInHead'], "R260: ✕ 不在 head 内"
        assert d0['headNotClipped'], "R260: head 负 margin 裁掉了 ✕"
        print(f"[2] head sticky ({d0['headPos']}), ✕ 在 head 内初始可见, 未被负 margin 裁剪")

        # 2. 滚动到内容深处, ✕ 仍可见 (sticky 生效)
        await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          box.scrollTop = box.scrollHeight;  // 滚到底
        }""")
        await page.wait_for_timeout(300)
        d1 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var head = box.querySelector('.bv-pop-head');
          var close = box.querySelector('.bv-pop-close');
          var br = box.getBoundingClientRect();
          var cr = close.getBoundingClientRect();
          var hr = head.getBoundingClientRect();
          return {scrollTop: Math.round(box.scrollTop),
                  closeVisible: cr.top >= br.top - 1 && cr.bottom <= br.bottom + 1,
                  closeTop: Math.round(cr.top - br.top),
                  headVisible: hr.top >= br.top - 1,
                  headBg: getComputedStyle(head).backgroundColor};
        }""")
        assert d1['scrollTop'] > 0, f"R260: 没滚到底 {d1['scrollTop']}"
        assert d1['closeVisible'], f"R260: 滚动后 ✕ 不可见 (sticky 未生效) closeTop={d1['closeTop']}"
        assert d1['headVisible'], "R260: 滚动后 head 不可见"
        # head 背景不透明 (遮住滚过内容)
        assert d1['headBg'] != 'rgba(0, 0, 0, 0)', f"R260: head 背景透明, 内容透过 {d1['headBg']}"
        print(f"[3] 滚动到 {d1['scrollTop']}px 后 ✕ 仍可见 (closeTop={d1['closeTop']}px, headBg={d1['headBg']})")

        # 3. ✕ 可关闭 (操作闭环)
        await page.click("#bv-rule-popover .bv-pop-close")
        await page.wait_for_timeout(300)
        closed = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert closed, "R260: ✕ 点击未关闭 popover"
        print("[4] ✕ 点击关闭 popover 正常")

        # 6. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e and 'ERR_CONNECTION_TIMED_OUT' not in e]
        assert not real_errors, f"R260: console errors {real_errors}"
        await b.close()
        print("[OK] R260 popover head sticky ✕ 恒可达 — 控制面夹层 (head top + ops bottom), console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
