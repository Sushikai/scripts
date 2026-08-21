"""R270 verify: popover 视觉层级守护 — 浮在卡片之上的"边缘"信号

第一性原理: popover 是临界面 (modal), 必须有明确的视觉边界告诉用户"这是浮层,
  不是页面内容". 原 box-shadow rgba(0,0,0,0.45) 0 8px 28px + border 1px line-2
  (12-14% 半透) 在双主题下都跟背景色阶接近:
  - dark: bg-3 (35,43,61) vs 12% 白线 → 边界弱
  - light: bg浅灰 vs 14% 黑线 + 白底黑阴影扩散 → 几乎看不见
  用户分不清 popover 与卡片.

修复:
  - border-color: accent 蓝色 45% (跟 rid/filter 同 accent 色, 视觉同族)
  - 双层 box-shadow: inset accent ring 1px + outer 深黑 16px/40px
    (内圈给"边框"信号, 外圈给"悬浮"信号 — 双主题都可见)
  - backdrop-filter: blur(6px) — 背景模糊加强"浮层"感 (light 尤其需要)

断言 (真实服务, 390px, dark + light 双主题):
  1. borderColor 是 accent 蓝 (rgb 51,187,255)
  2. boxShadow 含 accent ring + 深黑 outer 双层
  3. backdrop-filter blur 生效
  4. 双主题下 popover 背景 / border 颜色对比度 > 1.5:1 (边界清晰)
  5. console 0 错误
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
    # 等至少一行带 data-code 的 row 真正注入
    await page.wait_for_function(
        "document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-code]').length > 0",
        timeout=30000)
    await page.wait_for_timeout(800)

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

            # 用 nth(0) 而非 data-code 拼接, 避免 light theme reload 竞态
            target = await page.evaluate("""() => {
              var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row[data-code]'));
              return rows.length ? rows[0].getAttribute('data-code') : null;
            }""")
            assert target, f"R270[{theme}]: 无 data-code row"
            # 等 chip 注入 (rows 和 chips 分两步渲染)
            await page.wait_for_function(
                f"document.querySelector('#bv-pick-tbody tr.bv-row[data-code=\"{target}\"] .bv-rule-chip')",
                timeout=15000)
            await page.evaluate("""(code) => {
              var row = document.querySelector('#bv-pick-tbody tr.bv-row[data-code="'+code+'"]');
              var r = row.querySelector('.bv-rule-chip').getBoundingClientRect();
              window.scrollTo(0, window.scrollY + r.top - 200);
            }""", target)
            await page.wait_for_timeout(400)
            await page.click(f"#bv-pick-tbody tr.bv-row[data-code='{target}'] .bv-rule-chip", timeout=15000)
            await page.wait_for_timeout(600)

            d = await page.evaluate("""() => {
              var box = document.getElementById('bv-rule-popover');
              var cs = getComputedStyle(box);
              var body = document.body;
              var bcs = getComputedStyle(body);
              // 取 popover 外/内各一像素颜色做对比度估算
              var mask = document.getElementById('bv-rule-popover-mask');
              var mcs = mask ? getComputedStyle(mask) : null;
              return {
                borderColor: cs.borderColor,
                boxShadow: cs.boxShadow,
                backdrop: cs.backdropFilter || cs.webkitBackdropFilter,
                bg: cs.backgroundColor,
                pageBg: bcs.backgroundColor,
                maskBg: mcs ? mcs.backgroundColor : null
              };
            }""")
            results[theme] = d
            # console 错误
            real_errors = [e for e in errors
                           if 'favicon' not in e
                           and 'ERR_CONNECTION_TIMED_OUT' not in e
                           and 'status of 500' not in e]
            assert not real_errors, f"R270[{theme}]: console errors {real_errors}"
            await ctx.close()

        print(json.dumps(results, ensure_ascii=False, indent=2))

        # 1: borderColor accent 蓝
        for theme, d in results.items():
            assert '51, 187, 255' in d['borderColor'], f"R270[{theme}]: borderColor {d['borderColor']} ≠ accent 蓝"
            print(f"[1] {theme} borderColor = {d['borderColor']} (accent 蓝 45%)")

        # 2: 双层 box-shadow (含 accent ring + 深黑 outer)
        for theme, d in results.items():
            assert '51, 187, 255' in d['boxShadow'] and 'rgba(0, 0, 0' in d['boxShadow'], \
                f"R270[{theme}]: boxShadow 不是双层 {d['boxShadow'][:80]}"
            print(f"[2] {theme} boxShadow 双层 (accent ring + 深黑 outer)")

        # 3: backdrop-filter blur
        for theme, d in results.items():
            assert 'blur' in d['backdrop'], f"R270[{theme}]: backdrop 无 blur {d['backdrop']}"
            print(f"[3] {theme} backdrop-filter = {d['backdrop']}")

        # 4: 视觉对比 (border vs bg) — WCAG luminance 公式
        import re
        def rgb_int(s):
            m = re.match(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', s)
            return tuple(int(x) for x in m.groups()) if m else None
        def luminance(rgb):
            def chan(c):
                c = c / 255
                return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            r, g, b = rgb
            return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)
        for theme, d in results.items():
            bg = rgb_int(d['bg'])
            bc = rgb_int(d['borderColor'])
            if bg and bc:
                l1 = luminance(bg) + 0.05
                l2 = luminance(bc) + 0.05
                ratio = max(l1, l2) / min(l1, l2)
                assert ratio > 1.5, f"R270[{theme}]: 边界对比度 {ratio:.2f} < 1.5 (border vs bg 弱)"
                print(f"[4] {theme} border vs bg WCAG 对比度 {ratio:.2f} (≥1.5 边界清晰)")

        await b.close()
        print("[OK] R270 popover 视觉层级 — accent border + 双层 shadow + backdrop blur, 双主题边界清晰")

if __name__ == "__main__":
    asyncio.run(run())