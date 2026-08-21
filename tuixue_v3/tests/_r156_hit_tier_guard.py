"""R156 mobile hit-tier stripe 被 R15 红环覆盖 — 修复: 提高权 guard 重绘左条.

根因 (Playwright 实测):
  .bv-row.is-first-board (R15, 18931) 的 box-shadow: 0 0 0 1px hsla(0,75%,55%,0.15)
  与 .bv-row.bv-hit-weak (R113, 18184) 的 inset 3px 左条 同为 (0,4,1) 特异性, 源序在后者胜。
  实测首板行 computed box-shadow = 红环 (rgba(226,54,54,0.15) 0px 0px 0px 1px), R113 左条被吞。
  首板行 = 涨停最密集 → 恰好是"命中强度分档"最该可见的行, 却一个信号都不显示。

R156 修复: 在 R15 块后追加 guard — `.bv-hit-* > td:first-child` (+td 提升 (0,5,1) 打破源序),
  只重绘 inset 左条, 不动 R15 红环 (外) 与 R113 原 palette (内), 两通道并存:
    外 1px 红环   = "首板" (R15)
    内 3px 左条   = "命中强度" (R113)
  首板行两者都可见。强=绿 / 中=黄 / 弱=灰 (R113 原色), 首板背景渐变仍由 R15 提供。

断言 (mock 数据, 390px):
  - row0 (is-first-board + bv-hit-weak) computed box-shadow 含 inset 3px 0 (左条回归)
    且含 0px 0px 0px 1px (红环保留)
  - row0 background 仍含首板红色渐变 (R15 不回归)
  - guard 用 inset 3px 0, 无 currentColor 污染 code-link 文本色
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'...', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'分歧低吸', category:'弱转强', description:'...', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01','BV02'], score:75,
      change_pct:5.5, amount_yi:22.3, volume_ratio:1.8, turnover_pct:3.2, seal_ratio:0.4,
      sector:'银行', first_time:'09:42', phase:'close', burst_count:1,
      top_rule: { id:'BV02', title:'分歧低吸', quote:'分歧低吸要看承接', timestamp:'00:42', score_weight:8, weight:8, value:20 } },
    { code:'002594', name:'比亚迪', streak:1, matched_rules:['BV01','BV02'], score:70,
      change_pct:6.8, amount_yi:30.1, volume_ratio:1.9, turnover_pct:4.1, seal_ratio:0.5,
      sector:'新能源车', first_time:'09:38', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强要卡位', timestamp:'00:38', score_weight:10, weight:10, value:15 } }
  ], phase:'close', ts: Date.now()/1000 } },
  '/api/bv/backtest': { ok:true, data: { trades: 120, win_rate_pct: 62, avg_return_pct: 1.8, max_drawdown_pct: -12 } }
};
window._mockFetch = window.fetch;
window.fetch = function(url, opts){
  var u = String(url);
  for (var k in MOCK_RESPONSES) {
    if (u.indexOf(k) === 0) {
      return Promise.resolve({ ok:true, json: function(){ return Promise.resolve(MOCK_RESPONSES[k]); } });
    }
  }
  return window._mockFetch(url, opts);
};
"""

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCK)
        page = await ctx.new_page()
        for attempt in range(5):
            try:
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
                break
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          if (!rows.length) return {note:'no rows'};
          var out = { rows: rows.length };
          function probe(r) {
            var cs = getComputedStyle(r);
            var ft = r.querySelector('td:first-child');
            return {
              classes: r.className,
              rowShadow: cs.boxShadow,
              background: cs.backgroundImage,
              firstTdShadow: getComputedStyle(ft).boxShadow,
              linkColor: ft.querySelector('a.code-link') ? getComputedStyle(ft.querySelector('a.code-link')).color : null
            };
          }
          // row0 = top-1 茅台 (首板+强命中, 但 R22 accent 顶条 (0,5,2) 同权且源序更后 → 顶条胜出, 左条让位 — 设计如此)
          // row1 = 平安银行 (streak=2, mid — 非首板, R113 原 rule 应原样)
          // row2 = 比亚迪 (streak=1 首板 + mid — 关键修复用例: R15 红环曾吞掉 R113 左条)
          out.row0 = probe(rows[0]);
          out.row1 = probe(rows[1]);
          out.row2 = probe(rows[2]);
          return out;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("note") is None, f"rows missing: {m}"

        # --- row2 (首板非 top): R113 左条回归 + R15 红环保留 (双通道并存, R156 修复核心) ---
        r2 = m["row2"]
        assert "is-first-board" in r2["classes"], f"R156: row2 should be first-board, got {r2['classes']}"
        r2s = r2["firstTdShadow"]
        assert "3px 0px 0px 0px inset" in r2s, f"R156 FAIL: first-board row lost R113 inset 3px left stripe, got {r2s}"
        # mid 命中左条 = 黄 (hsl(45,90%,60%)) → rgba(245,199,61,0.85)
        assert "245, 199, 61" in r2s or "245,199,61" in r2s, f"R156 FAIL: stripe should be mid-yellow, got {r2s}"
        assert "0px 0px 0px 1px" in r2["rowShadow"], f"R156 FAIL: R15 red ring wiped on first-board row, got {r2['rowShadow']}"
        assert "rgba(226" in r2["background"] or "0, 75%" in r2["background"], f"R156 FAIL: first-board red bg lost, got {r2['background']}"

        # --- row1 (非首板): R113 原 rule 不被 guard 干扰 (左条仍在, 无红环) ---
        r1 = m["row1"]
        assert "is-first-board" not in r1["classes"], f"R156: row1 should NOT be first-board, got {r1['classes']}"
        r1s = r1["firstTdShadow"]
        assert "3px 0px 0px 0px inset" in r1s, f"R156 FAIL: non-first-board row lost R113 stripe, got {r1s}"
        assert "0px 0px 0px 1px" not in r1s, f"R156 FAIL: guard leaked red ring onto non-first-board row: {r1s}"

        # --- guard 未污染 code-link 文本色 (无 currentColor) ---
        if m["row2"].get("linkColor"):
            assert "24, 24, 27" not in m["row2"]["linkColor"], f"R156 FAIL: link color polluted: {m['row2']['linkColor']}"

        await browser.close()
        print(f"[OK] R156 hit-tier guard — 首板行 左条(黄 3px) + 红环(1px) 双通道回归, 非首板 R113 原样 ✓")

if __name__ == "__main__":
    asyncio.run(run())
