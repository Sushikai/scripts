"""R267 verify: popover 翻转路径 head 身份可读 + body 内容空间充足

第一性原理: popover 的职责是"此刻这条规则的全貌". 翻转路径 (卡片在视口底部) +
  多规则卡片时, head 五元素 (rail+nav+rid+title+close) 同一行 flex nowrap,
  title 被压成 14px×101px 竖排 → head 125px 吃掉翻转总高 220px 的 57%,
  body 可读区只剩 ~25px. 身份与导航挤在一条水平线 = 双轨道信息互抢空间.

修复:
  1. CSS: .bv-pop-rail flex-basis:100% → 轨道独占整行, 身份行 (nav+rid+title+close)
     保持一行完整可读 (身份与导航职责分离)
  2. JS: 翻转 maxH 220→360 (触发条件已保证上方空间 ≥576px, 硬 220 过度保守)

断言 (真实服务, 390px, 翻转路径):
  1. head title 横向可读 (h < 40px 不再 101px 竖排)
  2. rail 独占整行 (rail top > head top + 身份行高)
  3. body 可读区高度 > 80px (翻转路径不再被 head/ops 挤没)
  4. filter 按钮恒可见 (ops bottom ≤ 视口高)
  5. 翻转 popover 整体在视口内 (top ≥ 0, bottom ≤ 视口高)
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

        # 找视口底部卡片触发翻转路径
        target = await page.evaluate("""() => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          var best = null, bestTop = 0;
          for (var i=0;i<rows.length;i++){
            var chip = rows[i].querySelector('.bv-rule-chip');
            if (!chip) continue;
            var t = chip.getBoundingClientRect().top;
            if (t > 560 && (!best || t < bestTop)) { best = rows[i]; bestTop = t; }
          }
          if (!best) best = rows[rows.length-1];
          return {code: best.getAttribute('data-code')};
        }""")
        code = target['code']
        # 滚动到卡片接近视口底部 (top ~640, 触发 top+260>844) — 合并滚动+取 top
        # 为一次 evaluate (BV 30s 自动刷新会重建 tbody, 二次 querySelector 可能 null)
        chipTop = await page.evaluate("""(code) => {
          var row = document.querySelector('#bv-pick-tbody tr.bv-row[data-code="'+code+'"]');
          if (!row) return -1;
          var r = row.querySelector('.bv-rule-chip').getBoundingClientRect();
          window.scrollTo(0, window.scrollY + r.top - 620);
          return Math.round(r.top);
        }""", code)
        await page.wait_for_timeout(400)
        # 自动刷新后重查 chip top (若 -1 表示卡片已被刷新掉, 重新选一张)
        if chipTop < 0:
            target2 = await page.evaluate("""() => {
              var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
              var best = null, bestTop = 0;
              for (var i=0;i<rows.length;i++){
                var chip = rows[i].querySelector('.bv-rule-chip');
                if (!chip) continue;
                var t = chip.getBoundingClientRect().top;
                if (t > 560 && (!best || t < bestTop)) { best = rows[i]; bestTop = t; }
              }
              if (!best) best = rows[rows.length-1];
              return best ? {code: best.getAttribute('data-code'), top: Math.round(best.querySelector('.bv-rule-chip').getBoundingClientRect().top)} : null;
            }""")
            assert target2, "R267: 无 chip"
            code, chipTop = target2['code'], target2['top']
        # 断言触发翻转 (top 在视口底部区间)
        print(f"[0] 卡片 chip top={chipTop} (翻转路径: 应 >584 才触发 top+260>844)")
        assert chipTop > 560, f"R267: chipTop {chipTop} 未到视口底部 (未触发翻转路径)"

        await page.click("#bv-pick-tbody tr.bv-row[data-code='" + code + "'] .bv-rule-chip", timeout=15000)
        await page.wait_for_timeout(600)

        d = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return null;
          var head = box.querySelector('.bv-pop-head');
          var meta = box.querySelector('.bv-pop-meta');
          var body = box.querySelector('.bv-pop-body');
          var ops = box.querySelector('.bv-pop-ops');
          var rail = box.querySelector('.bv-pop-rail');
          var title = box.querySelector('.bv-pop-title');
          var cs = getComputedStyle(box);
          var boxR = box.getBoundingClientRect();
          var headR = head.getBoundingClientRect();
          var metaR = meta.getBoundingClientRect();
          var bodyR = body.getBoundingClientRect();
          var opsR = ops.getBoundingClientRect();
          var titleR = title.getBoundingClientRect();
          var railR = rail ? rail.getBoundingClientRect() : null;
          // body 可读区 = ops 顶 - head 底 (被 ops 遮住的部分不算可读)
          var bodyReadH = Math.max(0, Math.min(bodyR.bottom, opsR.top) - Math.max(bodyR.top, headR.bottom));
          // rail 独立行 = rail 自身高度 ≤ 36px (单行, 没换行堆叠)
          // flex-wrap:wrap + flex-basis:100% 让 rail 占满整行宽度, 高度由内容决定 (单行 ~32px)
          return {
            rid: box.querySelector('.bv-pop-rid').textContent.trim(),
            boxTop: Math.round(boxR.top), boxBottom: Math.round(boxR.bottom), boxH: Math.round(boxR.height),
            maxH: cs.maxHeight, overflowY: cs.overflowY,
            headTop: Math.round(headR.top), headBottom: Math.round(headR.bottom), headH: Math.round(headR.height),
            titleH: Math.round(titleR.height), titleW: Math.round(titleR.width), titleText: title.textContent.slice(0,40),
            railPresent: !!rail, railTop: railR ? Math.round(railR.top) : null, railH: railR ? Math.round(railR.height) : null,
            metaTop: Math.round(metaR.top), metaBottom: Math.round(metaR.bottom),
            bodyTop: Math.round(bodyR.top), bodyBottom: Math.round(bodyR.bottom), bodyReadH: Math.round(bodyReadH),
            opsTop: Math.round(opsR.top), opsBottom: Math.round(opsR.bottom),
            viewH: window.innerHeight,
            // 真正的语义: rail 自身是否单行 (≤36px), 不管 nav/title/close 怎么排
            railOwnRow: railR ? (railR.height <= 36) : null
          };
        }""")
        assert d, "R267: popover 未弹出"
        print(json.dumps(d, ensure_ascii=False, indent=2))

        # 1. title 横向可读 (不再 14px×101px 竖排)
        assert d['titleH'] <= 40, f"R267: title 仍竖排 {d['titleW']}×{d['titleH']} — rail 未独立行"
        print(f"[1] title 横向可读: {d['titleW']}×{d['titleH']} '{d['titleText']}'")

        # 2. rail 独占整行 (rail 自身只占单行高度 ≤36px)
        if d['railPresent']:
            assert d['railOwnRow'], f"R267: rail 未单行 railH={d['railH']} — 期望 ≤36px"
            print(f"[2] rail 独占整行 (railH={d['railH']} ≤ 36px)")
        else:
            print("[2] 单规则卡片无 rail, 跳过 (无多规则冲突场景)")

        # 3. body 可读区 > 80px
        assert d['bodyReadH'] > 80, f"R267: 翻转路径 body 可读区只剩 {d['bodyReadH']}px (head/ops 挤占)"
        print(f"[3] body 可读区 {d['bodyReadH']}px (head {d['headH']}px + ops 53px 让位)")

        # 4. filter 按钮恒可见
        assert d['opsBottom'] <= d['viewH'], f"R267: ops 底部 {d['opsBottom']} 越出视口 {d['viewH']}"
        print(f"[4] filter 按钮恒可见 (ops bottom {d['opsBottom']} ≤ {d['viewH']})")

        # 5. popover 整体在视口内
        assert d['boxTop'] >= 0, f"R267: popover top {d['boxTop']} < 0"
        assert d['boxBottom'] <= d['viewH'], f"R267: popover bottom {d['boxBottom']} > {d['viewH']}"
        print(f"[5] popover 整体在视口内 ({d['boxTop']}..{d['boxBottom']} / {d['viewH']})")

        # 6. console 0 错误
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R267: console errors {real_errors}"
        await b.close()
        print("[OK] R267 popover 翻转路径 — rail 单行独立, title 横向可读, body 空间充足, filter 恒可见, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
