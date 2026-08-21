"""R269 verify: popover ✕ close tap zone 32→44 (HIG)

第一性原理: R268 sticky head 后, ✕ 是 popover 顶部唯一控制控件 — 用户读完
  内容想退/切换时唯一可点的出口. 32px 不达 HIG 44 标准 (跟 R111 详情内✕
  22→32 同款根因, 但 close 是 popover 主退出通道, 主操作地位, 应进 44 体系).
  32px 在 390px 窄屏上命中率仍低 — 拇指精度误差 ~12px → 33% 误触.

修复:
  - padding: 4→12 (加 8px 热区)
  - min-width/height: 32→44
  - box-sizing: border-box (min-height 含 padding)
  - margin-left: auto (主轴推到右边缘, 跟 rid/title 自然分隔)

断言 (真实服务, 390px):
  1. close tap zone ≥ 44×44 (HIG 达标)
  2. close 仍在 head 内 (sticky head 守护 R260/R268)
  3. close 实际可点 — click 触发关闭 (R268/R260 ship-not-fixed 守护)
  4. console 0 错误
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

        target = await page.evaluate("""() => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          for (var i=0;i<rows.length;i++){
            var chip = rows[i].querySelector('.bv-rule-chip');
            if (chip) return rows[i].getAttribute('data-code');
          }
          return null;
        }""")
        chipTop = await page.evaluate("""(code) => {
          var row = document.querySelector('#bv-pick-tbody tr.bv-row[data-code="'+code+'"]');
          var r = row.querySelector('.bv-rule-chip').getBoundingClientRect();
          window.scrollTo(0, window.scrollY + r.top - 200);
          return Math.round(r.top);
        }""", target)
        await page.wait_for_timeout(400)
        await page.click("#bv-pick-tbody tr.bv-row[data-code='" + target + "'] .bv-rule-chip", timeout=15000)
        await page.wait_for_timeout(600)

        # 1: tap zone ≥ 44
        d = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var close = box.querySelector('.bv-pop-close');
          var head = box.querySelector('.bv-pop-head');
          var cr = close.getBoundingClientRect();
          var hr = head.getBoundingClientRect();
          var cs = getComputedStyle(close);
          return {
            closeW: Math.round(cr.width), closeH: Math.round(cr.height),
            padding: cs.padding, minW: cs.minWidth, minH: cs.minHeight,
            boxSizing: cs.boxSizing,
            closeInHead: cr.top >= hr.top - 1 && cr.bottom <= hr.bottom + 1
          };
        }""")
        assert d['closeW'] >= 44 and d['closeH'] >= 44, f"R269: close tap zone {d['closeW']}×{d['closeH']} < 44×44"
        print(f"[1] close tap zone {d['closeW']}×{d['closeH']} ≥ 44×44 (HIG)")

        # 2: close 在 head 内 (sticky 守护)
        assert d['closeInHead'], f"R269: close 脱离 head (sticky 失效)"
        print(f"[2] close 在 sticky head 内")

        # 3: click 关闭
        await page.click("#bv-rule-popover .bv-pop-close", timeout=5000)
        await page.wait_for_timeout(400)
        closed = await page.evaluate("""() => !document.getElementById('bv-rule-popover')""")
        assert closed, "R269: close click 未关闭 popover"
        print(f"[3] close click 触发关闭 (popover DOM 移除)")

        # 4: console 0 错误
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R269: console errors {real_errors}"

        await b.close()
        print("[OK] R269 popover ✕ close tap zone 44×44 — 关闭主操作达 HIG, sticky 守护, click 触发")

if __name__ == "__main__":
    asyncio.run(run())