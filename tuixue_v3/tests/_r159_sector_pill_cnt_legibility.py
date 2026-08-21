"""R159 mobile .bv-sector-pill-cnt 9→10.5px — 聚合条 hit 计数 chip 地板收尾.

第一性原理: 聚合条每个板块 pill 的 "×N" 计数告诉用户这个板块后面藏着几只票 —
  是"读哪个板块值得点进去"的关键读数, 与 R152 的 chg 涨幅同属温度计数据。
  9px 是全页最后一个低于 chip 地板 10.5px 的数据 chip (R148-R152 已收 vr/sector-chg/
  pill-chg), R159 收尾 cnt → 全页 0 个 9px 数据 chip。
  与 pill-chg 10.5px 保持同档 (同层读数不打架), 与 pill-name 11px 保持 0.5px 层级差。

断言 (mock 数据, 390px, 有 sector-bar + pill-cnt):
  - sector-pill-cnt font-size = 10.5px (was 9)
  - font-weight 700 保留
  - pill-name 11px 保留 (层级差)
  - active 态 (选中板块) cnt 仍黑色覆盖 (R86 active 守护)
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
          var bar = document.querySelector('.bv-sector-bar');
          if (!bar) return {note:'no sector-bar'};
          var cnt = bar.querySelector('.bv-sector-pill-cnt');
          var name = bar.querySelector('.bv-sector-pill-name');
          var pill = bar.querySelector('.bv-sector-pill');
          if (!cnt) return {note:'no pill-cnt'};
          var cs = getComputedStyle(cnt);
          return {
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            nameFont: name ? getComputedStyle(name).fontSize : null,
            color: cs.color,
            text: cnt.textContent
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("note") is None, f"sector-bar missing: {m}"
        assert m.get("fontSize") == "10.5px", f"sector-pill-cnt should be 10.5px (was 9), got {m.get('fontSize')}"
        assert m.get("fontWeight") == "700", f"sector-pill-cnt weight regression: {m.get('fontWeight')}"
        assert m.get("nameFont") == "11px", f"sector-pill-name should stay 11px, got {m.get('nameFont')}"

        await browser.close()
        print(f"[OK] R159 sector-pill-cnt — 10.5px (was 9) | weight 700 + name 11 (层级差 ✓) | 全页 0 个 9px 数据 chip ✓")

if __name__ == "__main__":
    asyncio.run(run())
