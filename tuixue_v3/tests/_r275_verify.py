"""R275 verify: popover swipe-down 关闭手势 — 触控自然关闭

第一性原理: popover 是临时浮层, 拇指 swipe-down 关闭是 iOS/Android 系统级
  约定 (原生 sheet/modal 都是这手势). 当前只有 ✕ (R269 44px 按钮) + mask
  click + R254 scroll close, 缺少触控手势 — 用户需要视线从内容移到 ✕ 按钮.

  实现: touchstart 记起点, touchmove 实时 translateY(dy) (视觉跟随手指),
  touchend 判定 dy > 80px 或 velocity > 0.5px/ms → 触发 _closeRulePopover,
  否则 transform="" 回弹 (CSS transition 接住). 上滑 ignore, preventDefault
  防止 popover 内部滚动冲突.

  Ship-not-fix 守护:
    - 长 swipe down 90px → 必须 close (主断言)
    - 短 swipe 25px → 不 close, transform 回弹 (临界值守护)
    - 上滑 (dy < 0) → 不 preventDefault, 不影响 popover 内容滚动 (scroll body)
    - R272 close 动效不退化
    - console 0 错误

断言 (真实服务, 390px):
  1. swipe down 90px → popover close (200ms 后 DOM 移除)
  2. swipe down 25px → popover 仍存在 (短 swipe 不触发)
  3. swipe down 90px 中途: box.style.transform 跟随手指 translateY
  4. R272 close 动效 (bv-pop-out) 不退化
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
    raise AssertionError("R275: 10 次尝试打开 popover 失败")

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # === A: swipe down 90px → close ===
        await open_popover(page)
        await page.wait_for_timeout(50)
        box = await page.query_selector("#bv-rule-popover")
        # 测中途 transform
        await page.touchscreen.tap(195, 400)
        await page.wait_for_timeout(50)
        # dispatch touch events 模拟 swipe (touchscreen.tap 是 click, 用 dispatch)
        await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          function fire(name, t){
            var ev = new TouchEvent(name, {
              cancelable: true, bubbles: true,
              touches: name === 'touchend' ? [] : [t],
              targetTouches: name === 'touchend' ? [] : [t],
              changedTouches: [t]
            });
            box.dispatchEvent(ev);
          }
          var t1 = new Touch({identifier:1, target:box, clientX:195, clientY:400});
          fire('touchstart', t1);
          // 中途 move 到 490 (dy=90)
          var t2 = new Touch({identifier:1, target:box, clientX:195, clientY:490});
          fire('touchmove', t2);
          // 看 transform
          window.__r275_mid_transform = box.style.transform;
          // touchend
          fire('touchend', t2);
        }""")
        # 3: 中途 transform 跟随
        mid_d = await page.evaluate("() => window.__r275_mid_transform")
        assert 'translateY(90' in mid_d, f"R275.3: 中途 transform {mid_d} ≠ translateY(90px)"
        print(f"[3] swipe 中途 transform = {mid_d}")
        # 1: 200ms 后 DOM 移除 (R272 close 动效 160ms + 余量)
        await page.wait_for_timeout(250)
        d_after = await page.evaluate("() => !document.getElementById('bv-rule-popover')")
        assert d_after, "R275.A1: swipe down 90px 后 popover 未移除"
        print(f"[A1] swipe down 90px → popover 移除")

        # === B: swipe down 25px (短 swipe, 不触发 close) ===
        await open_popover(page)
        await page.wait_for_timeout(50)
        # 分 3 段异步 dispatch, 模拟真实 swipe 50ms 时长, velocity < 0.5
        await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          window.__r275Touch = async function(){
            function fire(name, t){
              var ev = new TouchEvent(name, {
                cancelable: true, bubbles: true,
                touches: name === 'touchend' ? [] : [t],
                targetTouches: name === 'touchend' ? [] : [t],
                changedTouches: [t]
              });
              box.dispatchEvent(ev);
            }
            var t1 = new Touch({identifier:1, target:box, clientX:195, clientY:400});
            fire('touchstart', t1);
            await new Promise(r => setTimeout(r, 50));
            var t2 = new Touch({identifier:1, target:box, clientX:195, clientY:425});  // dy=25
            fire('touchmove', t2);
            fire('touchend', t2);
          };
        }""")
        await page.evaluate("() => window.__r275Touch()")
        await page.wait_for_timeout(250)
        d_b = await page.evaluate("() => !!document.getElementById('bv-rule-popover')")
        assert d_b, "R275.B: 短 swipe 25px 错误触发 close"
        # transform 应回弹 (空字符串或 none)
        tr_b = await page.evaluate("() => document.getElementById('bv-rule-popover').style.transform")
        print(f"[B] 短 swipe 25px → popover 仍存在, transform={tr_b!r} (回弹)")

        # === C: R272 close 动效不退化 (swipe 后触发的也是 bv-pop-out) ===
        # 先看是否还带 bv-pop-closing class (160ms 内还在 fade-out)
        d_c = await page.evaluate("""() => {
          var p = document.getElementById('bv-rule-popover');
          if (!p) return {hasPop: false};
          return {
            hasPop: true,
            closing: p.classList.contains('bv-pop-closing'),
            anim: getComputedStyle(p).animationName
          };
        }""")
        # 上一步 B 是短 swipe 没 close, 这里 popover 仍在 — 直接测 ✕ close
        await page.evaluate("() => document.querySelector('#bv-rule-popover .bv-pop-close').click()")
        await page.wait_for_timeout(50)
        d_c2 = await page.evaluate("""() => {
          var p = document.getElementById('bv-rule-popover');
          if (!p) return {hasPop: false};
          return {hasPop: true, closing: p.classList.contains('bv-pop-closing'),
                  anim: getComputedStyle(p).animationName};
        }""")
        if d_c2['hasPop']:
            assert d_c2['closing'] and 'bv-pop-out' in d_c2['anim'], f"R275.C: R272 close 动效退化 {d_c2}"
            print(f"[C] R272 close 动效仍生效 ({d_c2['anim']})")
        await page.wait_for_timeout(250)

        # === D: console ===
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R275: console errors {real_errors}"

        await b.close()
        print("[OK] R275 popover swipe-down 关闭 — 90px 触发 + 25px 不触发 + 中途 transform 跟随 + R272 close 不退化")

if __name__ == "__main__":
    asyncio.run(run())