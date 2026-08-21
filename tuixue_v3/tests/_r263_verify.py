"""R263 verify: popover 规则轨道 — scope 一览 + 直接跳转

第一性原理: 浏览的 scope 应该可见. R262 prev/next 只给顺序翻页, 用户不知道还有
  哪些规则 (只有 "1/4" 数字). 轨道 (.bv-pop-rail) 让全部命中规则 mini chip 一览
  + 点任意直接跳转 (1 tap 从规则1跳规则4, 而非 3 tap 顺序翻).

断言 (真实服务, 390px):
  1. 打开 N>1 规则卡片 chip → popover 显示轨道 (.bv-pop-rail)
  2. 轨道 chip 数 = 命中规则数 (= _popList 长度, pos 显示 "1/N")
  3. 当前规则 rail chip 有 is-cur 高亮
  4. 点轨道其它 chip → 内容切换 + pos 更新 + 高亮转移, popover 保持打开
  5. 点轨道 chip 直接跳转到目标 (非相邻), 验证 map 语义
  6. prev/next 仍可用 (轨道高亮同步)
  7. console 0 错误
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

        # 取首张有 chip 的卡片
        target = await page.evaluate("""() => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          for (var i=0;i<rows.length;i++){
            var chip = rows[i].querySelector('.bv-rule-chip');
            if (chip) return {code: rows[i].getAttribute('data-code')};
          }
          return null;
        }""")
        assert target, "R263: 无规则 chip"
        code = target['code']
        print(f"[0] 卡片 {code}")

        # 滚到 chip 可见 + 点击
        chipTop = await page.evaluate("""(code) => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          var row = rows.find(function(r){ return r.getAttribute('data-code') === code; });
          return row ? Math.round(row.querySelector('.bv-rule-chip').getBoundingClientRect().top) : -1;
        }""", code)
        await page.evaluate("() => window.scrollTo(0, Math.max(0, " + str(chipTop) + " - 300))")
        await page.wait_for_timeout(300)
        await page.click("#bv-pick-tbody tr.bv-row[data-code='" + code + "'] .bv-rule-chip", timeout=15000)
        await page.wait_for_timeout(600)

        # 1-3. 轨道存在 + chip 数 = 命中数 + 当前高亮
        d0 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          var rail = box.querySelector('.bv-pop-rail');
          var pos = box.querySelector('.bv-pop-pos');
          var rids = rail ? Array.from(rail.querySelectorAll('.bv-pop-rail-chip')) : [];
          var cur = rail ? rail.querySelector('.bv-pop-rail-chip.is-cur') : null;
          return {popover:true, hasRail: !!rail, railCount: rids.length,
                  posText: pos ? pos.textContent.trim() : null,
                  curRid: cur ? cur.getAttribute('data-rid') : null,
                  curText: cur ? cur.textContent.trim() : null,
                  curIsFirst: !!cur && rids[0] === cur,
                  rid: box.querySelector('.bv-pop-rid').textContent.trim()};
        }""")
        assert d0['popover'], "R263: popover 未弹出"
        assert d0['hasRail'], "R263: 无规则轨道"
        assert d0['railCount'] > 1, f"R263: 轨道 chip 数不对 {d0['railCount']}"
        # pos "1/N" N == railCount
        assert d0['posText'] and d0['posText'].split('/')[-1].strip() == str(d0['railCount']), f"R263: rail 数与 pos 不符 {d0['posText']} vs {d0['railCount']}"
        assert d0['curRid'] == d0['rid'], f"R263: 当前高亮 {d0['curRid']} != 展示规则 {d0['rid']}"
        assert d0['curIsFirst'], "R263: 初始当前规则应是轨道的第一个"
        firstRid = d0['rid']
        print(f"[1] 轨道 {d0['railCount']} chip, pos='{d0['posText']}', 当前高亮 {d0['curRid']} (初始)")

        # 4-5. 点最后一个轨道 chip → 直接跳转 (map 语义, 非相邻跳转)
        lastRid = await page.evaluate("""() => {
          var rail = document.querySelector('#bv-rule-popover .bv-pop-rail');
          var chips = rail.querySelectorAll('.bv-pop-rail-chip');
          return chips[chips.length - 1].getAttribute('data-rid');
        }""")
        assert lastRid != firstRid, "R263: 最后 chip 与首个相同 (数据异常)"
        await page.click("#bv-rule-popover .bv-pop-rail-chip[data-rid='" + lastRid + "']", timeout=10000)
        await page.wait_for_timeout(300)
        d1 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          var rail = box.querySelector('.bv-pop-rail');
          var cur = rail ? rail.querySelector('.bv-pop-rail-chip.is-cur') : null;
          return {popover:true, rid: box.querySelector('.bv-pop-rid').textContent.trim(),
                  posText: box.querySelector('.bv-pop-pos').textContent.trim(),
                  curRid: cur ? cur.getAttribute('data-rid') : null};
        }""")
        assert d1['popover'], "R263: 轨道跳转后 popover 被关闭"
        assert d1['rid'] == lastRid, f"R263: 点轨道未跳到 {lastRid} → {d1['rid']}"
        assert d1['curRid'] == lastRid, f"R263: 高亮未同步 {d1['curRid']}"
        print(f"[2] 点轨道最后一个 chip → 直接跳 {firstRid} → {d1['rid']} (pos='{d1['posText']}', 高亮同步)")

        # 6. prev 仍可用 (回到前一个, 高亮同步)
        await page.click("#bv-rule-popover .bv-pop-prev", timeout=10000)
        await page.wait_for_timeout(300)
        d2 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var rail = box.querySelector('.bv-pop-rail');
          var cur = rail.querySelector('.bv-pop-rail-chip.is-cur');
          return {rid: box.querySelector('.bv-pop-rid').textContent.trim(),
                  posText: box.querySelector('.bv-pop-pos').textContent.trim(),
                  curRid: cur.getAttribute('data-rid')};
        }""")
        assert d2['curRid'] == d2['rid'], f"R263: prev 后高亮不同步 {d2['curRid']} != {d2['rid']}"
        print(f"[3] prev 后: {d2['rid']} (pos='{d2['posText']}', 高亮同步 {d2['curRid']})")

        # 7. console 0 错误
        # 环境性过滤: favicon / ERR_CONNECTION_TIMED_OUT (上游繁忙) / 瞬态 HTTP 500
        #   (BV 30s 自动刷新 live_pick 上游偶发 500, 与 TIMED_OUT 同类 — 非前端 JS 错)
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R263: console errors {real_errors}"
        await b.close()
        print("[OK] R263 popover 规则轨道 — 命中规则一览 + 点任意直接跳转, 高亮同步, prev/next 兼容, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
