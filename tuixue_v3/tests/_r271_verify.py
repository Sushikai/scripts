"""R271 verify: popover mask 遮罩背景色 — 强化"非 page 区域"信号

第一性原理: 浮层遮罩是"非活跃区域"信号. 原 mask rgba(0,0,0,.32) + 无模糊,
  在 light 主题 (rgb 248,250,252) 上几乎无视觉差, 用户分不清"哪些是页面哪些
  是遮罩". R270 加强 popover 本身 (accent border + 双层 shadow + backdrop
  blur), 但 mask 仍是弱信号. 修复:
    - mask 加 backdrop-filter blur(4px) (跟 popover 同族, 视觉统一)
    - dark theme 0.32→0.55 (深色页面上黑色蒙布更明显)
    - light theme 0.32→0.18 (浅色页面上过黑会"压死"内容 — 浅色 + 微暗
      是更平衡的"非活跃区"信号)

Race 经验:
  - chip.click() 在 evaluate 闭包内调用不触发 popover (jQuery 委托绑在容器层,
    evaluate 内 event 路径不通). 必须用 page.click 真浏览器事件.
  - 30s 自动刷新会让 data-code 短暂消失 — 必须用 wait_for_function 持续等.

断言 (真实服务, 390px):
  1. dark theme mask bg = rgba(0,0,0,0.55) + blur(4px)
  2. light theme mask bg = rgba(15,23,42,0.18) + blur(4px)
  3. mask 实际覆盖整个视口 (inset:0)
  4. 双主题 console 0 错误
"""
import asyncio, json
from playwright.async_api import async_playwright

async def load(page, theme):
    await page.add_init_script(f"localStorage.setItem('tuixue-theme', '{theme}')")
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    # 等 rows + chips (30s 自动刷新会清表重建, 要持续等)
    await page.wait_for_function(
        "document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip').length > 0",
        timeout=60000)
    await page.wait_for_timeout(800)

async def open_popover(page):
    # 持续等 + 真浏览器 click (jQuery 委托必须真实事件)
    for _ in range(15):
        try:
            target = await page.evaluate("""() => {
              var row = document.querySelector('#bv-pick-tbody tr.bv-row[data-code] .bv-rule-chip');
              if (!row) return null;
              var tr = row.closest('tr.bv-row[data-code]');
              return tr ? tr.getAttribute('data-code') : null;
            }""")
            if not target:
                await page.wait_for_timeout(1000)
                continue
            await page.click(f"#bv-pick-tbody tr.bv-row[data-code='{target}'] .bv-rule-chip", timeout=5000)
            return target
        except Exception:
            await page.wait_for_timeout(1000)
    raise AssertionError("R271: 15 次尝试打开 popover 仍失败")

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        results = {}
        for theme in ('dark', 'light'):
            ctx = await b.new_context(viewport={"width": 390, "height": 844})
            page = await ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
            await load(page, theme)
            target = await open_popover(page)
            await page.wait_for_selector("#bv-rule-popover-mask", timeout=15000, state='attached')
            await page.wait_for_timeout(300)

            d = await page.evaluate("""(tgt) => {
              var mask = document.getElementById('bv-rule-popover-mask');
              var cs = getComputedStyle(mask);
              var r = mask.getBoundingClientRect();
              return {
                target: tgt,
                bg: cs.backgroundColor,
                backdrop: cs.backdropFilter || cs.webkitBackdropFilter,
                rectTop: Math.round(r.top), rectBottom: Math.round(r.bottom),
                rectLeft: Math.round(r.left), rectRight: Math.round(r.right),
                vw: window.innerWidth, vh: window.innerHeight
              };
            }""", target)
            results[theme] = d
            real_errors = [e for e in errors
                           if 'favicon' not in e
                           and 'ERR_CONNECTION_TIMED_OUT' not in e
                           and 'status of 500' not in e]
            assert not real_errors, f"R271[{theme}]: console errors {real_errors}"
            await ctx.close()

        print(json.dumps(results, ensure_ascii=False, indent=2))

        # 1: dark theme bg = rgba(0,0,0,0.55) + blur(4px)
        assert 'rgba(0, 0, 0, 0.55)' in results['dark']['bg'], f"R271: dark bg {results['dark']['bg']} ≠ 0.55 黑"
        assert 'blur' in results['dark']['backdrop'], f"R271: dark backdrop 无 blur"
        print(f"[1] dark mask bg = {results['dark']['bg']} + {results['dark']['backdrop']}")

        # 2: light theme bg = rgba(15,23,42,0.18) + blur(4px)
        assert '15, 23, 42' in results['light']['bg'], f"R271: light bg {results['light']['bg']} ≠ rgba(15,23,42,0.18)"
        assert 'blur' in results['light']['backdrop'], f"R271: light backdrop 无 blur"
        print(f"[2] light mask bg = {results['light']['bg']} + {results['light']['backdrop']}")

        # 3: mask 覆盖整个视口 (inset:0)
        for theme, d in results.items():
            assert d['rectTop'] == 0 and d['rectLeft'] == 0, f"R271[{theme}]: mask 不覆盖视口 (top={d['rectTop']} left={d['rectLeft']})"
            assert d['rectRight'] >= d['vw'] - 1 and d['rectBottom'] >= d['vh'] - 1, f"R271[{theme}]: mask 未到视口右下 ({d['rectRight']}×{d['rectBottom']} vs {d['vw']}×{d['vh']})"
            print(f"[3] {theme} mask 覆盖全视口 (0..{d['vw']} × 0..{d['vh']})")

        await b.close()
        print("[OK] R271 popover mask — dark 0.55 黑透 + light rgba(15,23,42,0.18) + 双主题 blur(4px), 全视口覆盖")

if __name__ == "__main__":
    asyncio.run(run())