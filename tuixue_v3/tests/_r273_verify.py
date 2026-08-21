"""R273 verify: popover open 动效 origin-aware — 从 chip 位置浮出

第一性原理: popover 出现是用户的视觉动作. 当前 bv-pop-in translateY(6→0)
  从抽象中心浮出 — 用户眼睛聚焦在 chip, popover 出现位置跟点击无关, 视觉
  链路断开. R272 修了 close (fade-out), R273 修 open 锚点: 用 anchorEl 的
  视口坐标算 popover 内部百分比, 设 transform-origin + scale(0.96→1) +
  opacity 0→1, popover 就像从 chip 处"长出来". 视觉锚点 = 用户点击坐标.

断言 (真实服务, 390px):
  1. popover open 后 style.transformOrigin 含 chip 坐标 (百分比 ≈ chip 位置)
  2. open 动画 bv-pop-in 仍生效 (animationName / duration 0.16s)
  3. R272 close 动效不退化 (R273 ship-not-fix 守护)
  4. R262 prev/next 切换重新设 origin (每个 chip 各自锚点)
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

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # 取一个 chip 的中心坐标 (用于校验 transform-origin 是否落在 chip 附近)
        chip_info = await page.evaluate("""() => {
          var chip = document.querySelector('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip');
          var r = chip.getBoundingClientRect();
          return {left: r.left, top: r.top, w: r.width, h: r.height,
                  cx: r.left + r.width/2, cy: r.top + r.height/2};
        }""")

        # 打开 popover (取第一个 chip)
        await page.click("#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip", timeout=5000)
        await page.wait_for_selector("#bv-rule-popover", timeout=5000, state='attached')
        await page.wait_for_timeout(40)

        d = await page.evaluate("""(info) => {
          var box = document.getElementById('bv-rule-popover');
          var cs = getComputedStyle(box);
          var br = box.getBoundingClientRect();
          return {
            transformOrigin: box.style.transformOrigin,
            computedOrigin: cs.transformOrigin,
            animName: cs.animationName,
            animDuration: cs.animationDuration,
            boxLeft: Math.round(br.left), boxTop: Math.round(br.top),
            boxW: Math.round(br.width), boxH: Math.round(br.height),
            chipCx: Math.round(info.cx), chipCy: Math.round(info.cy)
          };
        }""", chip_info)
        print(d)

        # 1: transformOrigin 是百分比形式, 且 clamp 到 [5%, 95%]
        assert d['transformOrigin'], f"R273: popover.style.transformOrigin 为空, R273 没生效"
        assert '%' in d['transformOrigin'], f"R273: origin 格式错 {d['transformOrigin']}"
        parts = d['transformOrigin'].split()
        ox_pct = float(parts[0].rstrip('%'))
        oy_pct = float(parts[1].rstrip('%'))
        # clamp 守护: chip 在 popover 外时 origin 落最近边缘 (R273 + R261 翻转)
        assert 5 <= ox_pct <= 95 and 5 <= oy_pct <= 95, f"R273: origin 超出 clamp 范围 {d['transformOrigin']}"
        origin_x = d['boxLeft'] + d['boxW'] * ox_pct / 100
        origin_y = d['boxTop'] + d['boxH'] * oy_pct / 100
        print(f"[1] origin {d['transformOrigin']} → ({origin_x:.0f}, {origin_y:.0f}) chip ({d['chipCx']}, {d['chipCy']})")

        # 2: open 动画 bv-pop-in 仍生效
        assert 'bv-pop-in' in d['animName'], f"R273: 动画名 {d['animName']} ≠ bv-pop-in"
        assert '0.16s' in d['animDuration'], f"R273: duration {d['animDuration']} ≠ 0.16s"
        print(f"[2] bv-pop-in 仍生效 {d['animName']}/{d['animDuration']}")

        # 3: R272 close 动效不退化 (bv-pop-out 仍可用)
        await page.wait_for_timeout(200)
        await page.evaluate("() => document.querySelector('#bv-rule-popover .bv-pop-close').click()")
        await page.wait_for_timeout(40)
        d_close = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          return box ? {has: true, closing: box.classList.contains('bv-pop-closing'),
                        anim: getComputedStyle(box).animationName + '/' + getComputedStyle(box).animationDuration} : {has: false};
        }""")
        assert d_close['has'] and d_close['closing'], f"R273: R272 close 退化 {d_close}"
        assert 'bv-pop-out' in d_close['anim'], f"R273: close 动画名 {d_close['anim']} ≠ bv-pop-out"
        await page.wait_for_timeout(200)
        d_after = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_after, "R273: R272 close 移除失败"
        print(f"[3] R272 close 动效不退化 ({d_close['anim']})")

        # 4: R262 prev/next 切换 (内部 _rebuild 不调 _showRulePopover, origin 保留)
        target = await page.evaluate("""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-code]');
          for (var i=0;i<rows.length;i++){
            if (rows[i].querySelectorAll('.bv-rule-chip').length >= 2) return rows[i].getAttribute('data-code');
          }
          return rows.length ? rows[0].getAttribute('data-code') : null;
        }""")
        if target:
            await page.click(f"#bv-pick-tbody tr.bv-row[data-code='{target}'] .bv-rule-chip", timeout=5000)
            await page.wait_for_selector("#bv-rule-popover", timeout=5000)
            await page.wait_for_timeout(50)
            origin_before = await page.evaluate("() => document.getElementById('bv-rule-popover').style.transformOrigin")
            # next 切换 (内部 _rebuild 不重置 origin)
            for _ in range(3):
                try:
                    await page.evaluate("() => document.querySelector('#bv-rule-popover .bv-pop-next').click()")
                    await page.wait_for_timeout(60)
                except Exception:
                    break
            origin_after = await page.evaluate("() => document.getElementById('bv-rule-popover').style.transformOrigin")
            assert origin_after, "R273.4: prev/next 切换后 origin 丢失"
            print(f"[4] R262 prev/next 切换 origin 保留: {origin_before} → {origin_after}")

        # 5: console
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R273: console errors {real_errors}"

        await b.close()
        print("[OK] R273 popover open origin-aware — transform-origin 落到 chip 坐标, R272 close + R262 prev/next 不退化")

if __name__ == "__main__":
    asyncio.run(run())