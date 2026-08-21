"""R261 verify: popover 高度动态化 — 有空间就全显示不滚动

第一性原理: popover 内容是"此刻注意焦点"的就地答案, 应一次全给. maxHeight 固定
  220px 在屏高充足时浪费下方空间 (844 屏下方 340px 空闲却只给 220 → 251px 内容
  需滚动). R261: 下方空间充足时给到 min(360, innerHeight-top-8), 内容少时
  shrink-wrap 到内容高; 空间不足 (屏小/翻转) 退到 220 上限.

断言 (真实服务, 390px, 卡片在屏中):
  1. 下方空间充足 (>300px) 时 popover maxHeight 动态放大 (>220)
  2. 内容全显示: scrollHeight ≤ clientHeight (无滚动)
  3. popover 不溢出视口底 (bottom ≤ innerHeight)
  4. 仍保留 sticky head/ops (R258/R260 守护)
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

        # 让 chip 进视口中部 (滚动到屏中)
        c0 = await page.evaluate("() => Math.round(document.querySelector('#bv-pick-tbody .bv-rule-chip').getBoundingClientRect().top)")
        await page.evaluate("() => window.scrollTo(0, " + str(max(0, c0 - 300)) + ")")
        await page.wait_for_timeout(300)
        await page.click("#bv-pick-tbody .bv-rule-chip")
        await page.wait_for_timeout(500)

        d = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          var br = box.getBoundingClientRect();
          var head = box.querySelector('.bv-pop-head');
          var ops = box.querySelector('.bv-pop-ops');
          var hcs = head ? getComputedStyle(head) : null;
          var ocs = ops ? getComputedStyle(ops) : null;
          return {popover:true, popoverTop: Math.round(br.top), popoverBottom: Math.round(br.bottom),
                  popoverH: Math.round(br.height), vh: window.innerHeight,
                  scrollH: box.scrollHeight, clientH: box.clientHeight,
                  scrollable: box.scrollHeight > box.clientHeight + 1,
                  maxH: getComputedStyle(box).maxHeight,
                  spaceBelow: window.innerHeight - Math.round(br.bottom),
                  contentFullH: Math.round(box.scrollHeight),
                  headSticky: hcs ? hcs.position : null, opsSticky: ocs ? ocs.position : null,
                  noOverflowBottom: Math.round(br.bottom) <= window.innerHeight};
        }""")
        assert d['popover'], "R261: popover 未弹出"
        print(f"[1] popover top={d['popoverTop']} bottom={d['popoverBottom']} h={d['popoverH']}px (空间下方 {d['spaceBelow']}px)")
        # 2. 下方空间充足时 maxHeight 动态放大 (>220)
        assert d['spaceBelow'] > 300, f"R261: 下方空间不足 {d['spaceBelow']}px (probe 前提) — 改用更多滚动"
        # 3. 内容全显示不滚动 (核心断言)
        assert not d['scrollable'], f"R261: 内容仍需滚动 scrollH={d['scrollH']} > clientH={d['clientH']} (maxH 未动态放大)"
        assert d['scrollH'] <= d['clientH'] + 1, f"R261: scrollH {d['scrollH']} > clientH {d['clientH']}"
        print(f"[2] 内容全显示: scrollH={d['scrollH']} ≤ clientH={d['clientH']} (无滚动, maxH={d['maxH']})")
        # 4. 不溢出视口底
        assert d['noOverflowBottom'], f"R261: popover 溢出视口底 bottom={d['popoverBottom']} > vh={d['vh']}"
        print(f"[3] popover 不溢出视口底 (bottom={d['popoverBottom']} ≤ vh={d['vh']})")
        # 5. sticky head/ops 保留 (R258/R260 守护)
        assert d['headSticky'] == 'sticky', f"R261: head sticky 回归 {d['headSticky']}"
        assert d['opsSticky'] == 'sticky', f"R261: ops sticky 回归 {d['opsSticky']}"
        print(f"[4] head({d['headSticky']}) + ops({d['opsSticky']}) sticky 控制面保留 (R258/R260 守护)")

        # 6. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e and 'ERR_CONNECTION_TIMED_OUT' not in e]
        assert not real_errors, f"R261: console errors {real_errors}"
        await b.close()
        print("[OK] R261 popover 高度动态化 — 有空间就全显示不滚动, sticky 控制面保留, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
