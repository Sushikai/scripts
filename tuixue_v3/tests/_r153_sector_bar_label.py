"""R153 mobile .bv-sector-bar-label 10px ink-3 → 10.5px ink-2 — 聚合条引导 label 双弱.

原: R18 加的"🔥 板块命中:"引导 label (聚合条前导), font 10px + color ink-3 (#B8C2D6 弱灰)。
    双弱组合 (10px + 最弱灰) — 用户扫聚合条时引导词几乎不可见, 但它是"这是板块聚合条"
    的唯一标识 (R86 pill 可视化之前, 用户不知道这行彩色 pill 是什么)。
R153: 10→10.5px (跟 chip 地板同档) + ink-3→ink-2 (中灰, label 不是被弱化的元数据,
    是聚合条的 affordance 引导词 — 弱化它=弱化整条的语义)。
第一性原理: 聚合条的意义是"一眼扫完最强板块", 引导 label 是"这行是板块"的语义锚点。
    10px + ink-3 双弱让它不可见 → 用户把彩色 pill 当噪音跳过。
    label ≠ 元数据: 元数据 (数据戳/计数) 才该弱化, 引导词是功能的一部分该保持可读。
R18/R86 守护: flex-shrink 0 / align-self center / weight 600 / padding-right 2 不动。
断言 (mock 数据 4 板块, 390px):
  - sector-bar-label font = 10.5px (was 10)
  - font-weight 600 保留
  - color = ink-2 (rgb(51,65,85) dark theme / 亮色对应) — 不再是最弱 ink-3
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
    { code:'300750', name:'宁德时代', streak:1, matched_rules:['BV01'], score:65,
      change_pct:3.2, amount_yi:66.1, volume_ratio:1.2, turnover_pct:1.8, seal_ratio:0.2,
      sector:'电池', first_time:'09:50', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'...', timestamp:'00:50', score_weight:10, weight:10, value:15 } },
    { code:'002594', name:'比亚迪', streak:1, matched_rules:['BV02'], score:60,
      change_pct:2.1, amount_yi:44.8, volume_ratio:1.5, turnover_pct:2.2, seal_ratio:0.15,
      sector:'新能源车', first_time:'09:55', phase:'close', burst_count:0,
      top_rule: { id:'BV02', title:'分歧低吸', quote:'...', timestamp:'00:55', score_weight:8, weight:8, value:12 } }
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
          var bar = document.querySelector('.bv-sector-bar');
          if (!bar) return {note:'no sector-bar'};
          var lbl = bar.querySelector('.bv-sector-bar-label');
          if (!lbl) return {note:'no label'};
          var cs = getComputedStyle(lbl);
          return {
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            color: cs.color,
            flexShrink: cs.flexShrink,
            alignSelf: cs.alignSelf,
            paddingRight: cs.paddingRight,
            text: lbl.textContent.trim()
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("note") is None, f"sector-bar-label missing: {m}"
        assert m.get("fontSize") == "10.5px", f"sector-bar-label should be 10.5px (was 10), got {m.get('fontSize')}"
        assert m.get("fontWeight") == "600", f"sector-bar-label weight regression: {m.get('fontWeight')}"
        # ink-2 (rgb(51,65,85) 是 ink-2 色值; 若解析不同, 只要不是最弱 ink-3 #B8C2D6 即可)
        assert m.get("color") != "rgb(184, 194, 214)", f"sector-bar-label should no longer be ink-3 (#B8C2D6), got {m.get('color')}"
        assert m.get("flexShrink") == "0", f"flex-shrink regression (R18): {m.get('flexShrink')}"

        await browser.close()
        print(f"[OK] R153 sector-bar-label — 10.5px + ink-2 (was 10 + ink-3) | weight 600 + flexShrink 0 (R18 ✓)")

if __name__ == "__main__":
    asyncio.run(run())
