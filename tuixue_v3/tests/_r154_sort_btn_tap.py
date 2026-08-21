"""R154 mobile .bv-sort-btn 28→44px — 排序主触发按钮进 HIG 44 体系.

原: R17 排序 sheet 的触发按钮 (.bv-sort-btn, 卡片头右侧"⇅ 分数"按钮) R102 设 28px,
    注释写"排序按钮是次级操作"。但它是**主触发** — 点它打开排序 sheet (R17),
    全页排序的单一入口。R102 "次级"标签错误。
R154: 28→44px (显式 Apple HIG 44), 跟 R139 sort-opt / R146 prev-next / R147 cat-summary
    工具类按钮体系一致。
第一性原理: 排序是用户看推票列表前的第一操作 (R6 "3 秒定位目标"), 触发按钮是
    tool-class 主操作, 28px 拇指难点。R102 把它当次级快捷 (卡片内 wl/jump 28px R98)
    是错的 — 它不是卡内快捷, 是列表级工具。
R17/R84 守护: font 12 + border/bg/accent + flex-shrink 0 + nowrap 不动。label 逻辑不动。
  注意: min-height 44 会顶高 card-head — R94 flex-basis:0 防止换行仍生效, 验证 h3 不换行。
断言 (mock 数据, 390px, 注入/已有 sort-btn):
  - sort-btn height ≥ 44px (HIG)
  - font-size 12px 保留
  - color accent 保留
  - flex-shrink 0 保留 (不压缩)
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
      top_rule: { id:'BV02', title:'分歧低吸', quote:'分歧低吸要看承接', timestamp:'00:42', score_weight:8, weight:8, value:20 } }
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
          var btn = document.querySelector('.bv-sort-btn');
          if (!btn) return {note:'no sort-btn'};
          var cs = getComputedStyle(btn);
          var rect = btn.getBoundingClientRect();
          var head = document.querySelector('.bv-pick-card .card-head');
          return {
            height: Math.round(rect.height),
            minHeight: cs.minHeight,
            fontSize: cs.fontSize,
            color: cs.color,
            flexShrink: cs.flexShrink,
            padding: cs.padding,
            label: (btn.textContent || '').trim()
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("note") is None, f"sort-btn missing: {m}"
        assert m.get("height") >= 44, f"sort-btn height must be ≥44 (HIG), got {m.get('height')}"
        assert m.get("minHeight") == "44px", f"sort-btn min-height must be 44, got {m.get('minHeight')}"
        assert m.get("fontSize") == "12px", f"sort-btn font regression: {m.get('fontSize')}"
        assert m.get("flexShrink") == "0", f"sort-btn flex-shrink regression: {m.get('flexShrink')}"

        await browser.close()
        print(f"[OK] R154 sort-btn — height {m['height']}px (was 28) | font 12 + accent + flexShrink 0 ✓")

if __name__ == "__main__":
    asyncio.run(run())
