"""R258 verify: popover filter 按钮单行触控 — 操作按钮不折行膨胀

第一性原理: popover 是"此刻注意焦点"的就地答案 (R254), 其操作闭环 (✕ 关闭 /
  🔍 过滤 R252) 必须真正可达. R111-R160 的 tap-zone 轮次全在 popover 引入
  (R252) 之前 — popover 内部控件从未被扫描. probe 抓到 filter 按钮 390px 折行
  → 高 44→64px: 操作按钮不能换行膨胀, 触控目标必须单行.

断言 (真实服务, 390px):
  1. popover 弹出
  2. filter 按钮单行 (nowrap 生效, 高度 44±2px, 无折行)
  3. ✕ close 按钮 tap zone ≥ 32×32
  4. popover 无横向溢出 (box 内 scrollWidth 不超 clientWidth)
  5. filter 按钮可见 (在 popover 可视区内, 可点)
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
    # BV view 数据异步加载 (自动刷新 30s 持续请求, networkidle 永不触发 → 用 domcontentloaded)
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

        # 点规则 chip 弹 popover (守护 chip 已渲染)
        await page.wait_for_selector("#bv-pick-tbody .bv-rule-chip", timeout=15000)
        await page.evaluate("""() => {
          var chip = document.querySelector('#bv-pick-tbody .bv-rule-chip');
          chip.click();
        }""")
        await page.wait_for_timeout(400)

        d = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          var br = box.getBoundingClientRect();
          function m(sel){
            var el = box.querySelector(sel); if (!el) return null;
            var r = el.getBoundingClientRect();
            return {w: Math.round(r.width), h: Math.round(r.height),
                    top: Math.round(r.top - br.top), left: Math.round(r.left - br.left),
                    inBox: r.left >= br.left - 1 && r.right <= br.right + 1,
                    ws: getComputedStyle(el).whiteSpace};
          }
          var close = m('.bv-pop-close');
          var filter = m('.bv-pop-filter');
          // filter 单行: nowrap 生效 + 高度 ≈ 44 (padding 10 + 内容 24)
          var filterSingleLine = filter && filter.ws === 'nowrap' && Math.abs(filter.h - 44) <= 2;
          // popover 无横向溢出
          var bodyOverflowX = box.scrollWidth > box.clientWidth + 1;
          // filter 在可视区内 (可点): box 内部位置 + box 高度内
          var filterVisible = filter && filter.top + filter.h <= box.clientHeight + 1 &&
                              filter.top >= 0;
          // ops sticky bottom 吸附 (R258)
          var ops = box.querySelector('.bv-pop-ops');
          var opsSticky = ops ? getComputedStyle(ops).position === 'sticky' : false;
          var opsPos = ops ? getComputedStyle(ops).position + ' bottom:' + getComputedStyle(ops).bottom : null;
          return {popover:true, boxW: Math.round(br.width),
                  close: close, filter: filter,
                  filterSingleLine: filterSingleLine,
                  bodyOverflowX: bodyOverflowX,
                  filterVisible: filterVisible,
                  opsSticky: opsSticky, opsPos: opsPos,
                  boxClientH: box.clientHeight, boxScrollH: box.scrollHeight,
                  filterText: filter ? box.querySelector('.bv-pop-filter').textContent.trim() : null};
        }""")
        assert d['popover'], "R258: popover 未弹出"
        assert d['close'], "R258: close 不存在"
        assert d['filter'], "R258: filter 不存在"
        print(f"[1] popover 弹出, boxW={d['boxW']}px (390px 内全宽布局)")
        # 2. filter 单行 (核心断言)
        assert d['filterSingleLine'], f"R258: filter 折行 {d['filter']}"
        print(f"[2] filter 按钮单行 nowrap h={d['filter']['h']}px (44px 触控目标, 不折行膨胀)")
        # 3. close tap zone ≥ 32
        assert d['close']['w'] >= 32 and d['close']['h'] >= 32, f"R258: close tap zone 不足 {d['close']}"
        print(f"[3] close ✕ tap zone {d['close']['w']}×{d['close']['h']}px ≥ 32×32")
        # 4. 无横向溢出
        assert not d['bodyOverflowX'], f"R258: popover 横向溢出 scrollW={d['boxScrollH']}"
        print("[4] popover 无横向溢出")
        # 5. filter 可见: ops sticky bottom 吸附 → 无论内容多长 filter 恒在可视区底部
        assert d['filterVisible'], f"R258: filter 不可见 top={d['filter']['top']} h={d['filter']['h']} boxH={d['boxClientH']}"
        assert d['opsSticky'], f"R258: ops 未 sticky {d['opsPos']}"
        print(f"[5] filter 按钮 sticky 吸附底部可见可点 (boxH={d['boxClientH']}px, ops {d['opsPos']}) '{d['filterText']}'")

        # 6. console 0 错误 (过滤 favicon + 环境性网络超时)
        real_errors = [e for e in errors if 'favicon' not in e and 'ERR_CONNECTION_TIMED_OUT' not in e]
        assert not real_errors, f"R258: console errors {real_errors}"
        await b.close()
        print("[OK] R258 popover filter 按钮单行触控 + ops sticky 吸附 — 操作按钮不折行膨胀恒可达, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
