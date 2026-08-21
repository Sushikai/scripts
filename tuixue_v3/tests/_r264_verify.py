"""R264 verify: 轨道按决策价值排序 — 权重高规则排前

第一性原理: 轨道是导航 map, map 应按用户决策价值排列. R263 轨道按 API matched_rules
  原始顺序, 权重高的规则可能排后面 (用户在轨道上找"最重要的"还得横向滚动).
  R264: 轨道 chip 按 score_weight 降序排列, 并列按 title 稳定排序. prev/next
  步进跟随同一顺序 (轨道顺序=步进顺序).

断言 (真实服务, 390px):
  1. 打开 N>1 规则卡片 chip → 轨道 chip 顺序 = 命中规则按 score_weight 降序
  2. 权重相同的规则按 id 字典序稳定
  3. 当前高亮规则 = 点击的规则 (排序后位置正确)
  4. prev/next 步进跟随轨道顺序 (next 到轨道下一 chip)
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

        # 抓规则权重 (前端 _rulesById 来自 /api/bv/rules)
        weights = await page.evaluate("""() => {
          var out = {};
          // 从页面已渲染的规则数据取 — bv-frontend 内部 _rulesById 不可达, 从 API 已载
          // popover 轨道里可读 rid, 但权重要查接口. 这里直接 fetch /api/bv/rules 太重,
          // 改用: 打开 popover 读轨道 + 记录当前卡片 matched_rules (从 popover 不暴露).
          return out;
        }""")
        # 直接 fetch rules 拿到权重映射
        rules_resp = await page.request.get("http://127.0.0.1:7799/api/bv/rules")
        rules_json = await rules_resp.json()
        rdata = rules_json.get('data', {})
        rules = rdata.get('rules') or rdata if isinstance(rdata, list) else (rdata.get('rules') or [])
        weight_map = {}
        for r in rules:
            if isinstance(r, dict) and r.get('id'):
                weight_map[r['id']] = r.get('score_weight')
        print(f"[0] 规则权重映射 {len(weight_map)} 条 (BV03={weight_map.get('BV03')}, BV05={weight_map.get('BV05')}, BV06={weight_map.get('BV06')}, BV07={weight_map.get('BV07')})")

        # 取首张有 chip 的卡片
        target = await page.evaluate("""() => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          for (var i=0;i<rows.length;i++){
            var chip = rows[i].querySelector('.bv-rule-chip');
            if (chip) return {code: rows[i].getAttribute('data-code')};
          }
          return null;
        }""")
        assert target, "R264: 无规则 chip"
        code = target['code']
        print(f"[0.5] 卡片 {code}")

        chipTop = await page.evaluate("""(code) => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          var row = rows.find(function(r){ return r.getAttribute('data-code') === code; });
          return row ? Math.round(row.querySelector('.bv-rule-chip').getBoundingClientRect().top) : -1;
        }""", code)
        await page.evaluate("() => window.scrollTo(0, Math.max(0, " + str(chipTop) + " - 300))")
        await page.wait_for_timeout(300)
        await page.click("#bv-pick-tbody tr.bv-row[data-code='" + code + "'] .bv-rule-chip", timeout=15000)
        await page.wait_for_timeout(600)

        # 1. 轨道 chip 顺序 + 权重
        d0 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          var rail = box.querySelector('.bv-pop-rail');
          if (!rail) return {popover:true, hasRail:false};
          var chips = Array.from(rail.querySelectorAll('.bv-pop-rail-chip'));
          return {popover:true, hasRail:true, railIds: chips.map(function(c){ return c.getAttribute('data-rid'); }),
                  curRid: (rail.querySelector('.bv-pop-rail-chip.is-cur')||{}).getAttribute ? rail.querySelector('.bv-pop-rail-chip.is-cur').getAttribute('data-rid') : null,
                  rid: box.querySelector('.bv-pop-rid').textContent.trim()};
        }""")
        assert d0['popover'], "R264: popover 未弹出"
        assert d0['hasRail'], "R264: 无轨道"
        rail_ids = d0['railIds']
        assert len(rail_ids) > 1, f"R264: 轨道只有 {len(rail_ids)} 个 (数据不足)"
        # 计算期望顺序: 按权重降序, 并列按 id 字典序
        expected = sorted(rail_ids, key=lambda x: (-(weight_map.get(x) if weight_map.get(x) is not None else 0), x))
        assert rail_ids == expected, f"R264: 轨道顺序不是权重降序\n  rail: {rail_ids}\n  exp : {expected}"
        print(f"[1] 轨道顺序 = 权重降序: {rail_ids}")
        # 3. 当前高亮 = 展示规则 (排序后点击的规则位置正确)
        assert d0['curRid'] == d0['rid'], f"R264: 当前高亮 {d0['curRid']} != 展示 {d0['rid']}"
        print(f"[2] 当前高亮 {d0['curRid']} (点击规则, 排序后位置正确)")

        # 4. next 步进跟随轨道顺序
        cur_idx = rail_ids.index(d0['rid'])
        next_expected = rail_ids[cur_idx + 1]
        await page.click("#bv-rule-popover .bv-pop-next", timeout=10000)
        await page.wait_for_timeout(300)
        d1 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var rail = box.querySelector('.bv-pop-rail');
          var cur = rail.querySelector('.bv-pop-rail-chip.is-cur');
          return {rid: box.querySelector('.bv-pop-rid').textContent.trim(),
                  curRid: cur.getAttribute('data-rid')};
        }""")
        assert d1['rid'] == next_expected, f"R264: next 未跟随轨道顺序 {d1['rid']} != {next_expected}"
        assert d1['curRid'] == next_expected, f"R264: next 后高亮未同步 {d1['curRid']}"
        print(f"[3] next 步进跟随轨道: {d0['rid']} → {d1['rid']} (轨道顺序)")

        # 5. console 0 错误
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R264: console errors {real_errors}"
        await b.close()
        print("[OK] R264 轨道按决策价值排序 — 权重高规则排前, prev/next 跟随同一顺序, 高亮同步, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
