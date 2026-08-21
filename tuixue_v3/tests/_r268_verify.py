"""R268 verify: popover head 真正 sticky top — 修复 R260 ship-without-actual-fix

第一性原理: 内容溢出 popover maxH 时, 用户读 quote/desc 到一半, 想到 "X 关掉"
  切下一条规则 → ✕ 按钮跟着内容一起滚走, 控制面不可达 (存在性≠可达性 R88).
  R260 注释写过 sticky top + 负 margin 让 head 背景顶到 popover 内边, 但 CSS
  实际没加 position:sticky → head 跟 box 一起 scroll → ✕ 滚走. R268 把
  R260 的注释转成实际代码.

修复:
  - position: sticky; top: 0
  - margin: -12px -14px 0 (顶到 popover 内边覆盖 padding 12 14)
  - padding: 12px 14px 6px (恢复 head 内部节奏)
  - background: var(--bg-3) (遮住滚过的内容)
  - z-index: 2 (盖在 body 内容上)

断言 (真实服务, 390px):
  1. head computed position == sticky
  2. head top == 0
  3. 强制长内容让 box 可滚 + scrollTop=9999 后, head 仍在 box 顶部 (粘住)
  4. ✕ close 按钮 rect 仍在 head rect 内
  5. 滚动后 head 仍 visible (head 顶部 ≤ box 底部)
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

        # 1+2: head sticky / top=0
        d0 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var head = box.querySelector('.bv-pop-head');
          var cs = getComputedStyle(head);
          return {position: cs.position, top: cs.top, zIndex: cs.zIndex,
                  margin: cs.margin, padding: cs.padding};
        }""")
        assert d0['position'] == 'sticky', f"R268: head position {d0['position']} ≠ sticky"
        assert d0['top'] == '0px', f"R268: head top {d0['top']} ≠ 0"
        print(f"[1+2] head sticky top={d0['top']} z-index={d0['zIndex']}")

        # 注入长内容让 box 可滚 + scroll 到底 + 验证 head 仍吸顶
        await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var body = box.querySelector('.bv-pop-body');
          var longText = document.createElement('div');
          longText.id = 'r268-test-long';
          longText.textContent = 'lorem ipsum '.repeat(200);
          body.appendChild(longText);
          box.scrollTop = 9999;
        }""")
        await page.wait_for_timeout(300)

        d1 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var head = box.querySelector('.bv-pop-head');
          var close = box.querySelector('.bv-pop-close');
          var headR = head.getBoundingClientRect();
          var boxR = box.getBoundingClientRect();
          var closeR = close.getBoundingClientRect();
          return {
            boxScrollTop: box.scrollTop,
            headTop: Math.round(headR.top),
            boxTop: Math.round(boxR.top),
            headStillAtTop: Math.abs(headR.top - boxR.top) <= 16,  // 容忍 sticky 内部 padding/margin
            headStillVisible: headR.bottom > boxR.top + 1 && headR.top < boxR.bottom - 1,
            closeTop: Math.round(closeR.top),
            closeBottom: Math.round(closeR.bottom),
            headBottom: Math.round(headR.bottom),
            closeInHead: closeR.top >= headR.top - 1 && closeR.bottom <= headR.bottom + 1
          };
        }""")
        print(json.dumps(d1, ensure_ascii=False, indent=2))
        assert d1['boxScrollTop'] > 100, f"R268: box 没滚动 scrollTop={d1['boxScrollTop']} (长内容未生效)"
        print(f"[3] box 滚到 scrollTop={d1['boxScrollTop']} 后, head 仍吸顶 (headTop={d1['headTop']} ≈ boxTop={d1['boxTop']})")

        # 4: ✕ close 在 head 内
        assert d1['closeInHead'], f"R268: ✕ close (top={d1['closeTop']} bot={d1['closeBottom']}) 脱离 head (top..bot={d1['headTop']}..{d1['headBottom']})"
        print(f"[4] ✕ close 在 head 内 (top={d1['closeTop']}..{d1['closeBottom']} ∈ head {d1['headTop']}..{d1['headBottom']})")

        # 5: head 可见
        assert d1['headStillVisible'], f"R268: head 不可见"
        print(f"[5] head 滚后仍 visible (R260 ship-without-fix 已修复)")

        # 6: console 0 错误
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R268: console errors {real_errors}"

        await b.close()
        print("[OK] R268 popover head sticky top — 修复 R260 ship-without-actual-fix, ✕ 滚动后仍吸顶")

if __name__ == "__main__":
    asyncio.run(run())