"""R109 creed-card eyebrow 字号 — 10→11px (R108 一致性).

原: R95 把 creed-card eyebrow 压到 10px (单行省略), 但 UP主/BV号/时间
    是"我抄录给谁看"的关键身份, 10px 又走 R108 同个错。
R109: 10→11px, 沿用 R95 单行省略 + flex 1 1 100% 保持折叠紧凑 (row 高不增长)。
断言 (mock 数据, 390px):
  - bv-creed-card eyebrow 字号 ≥ 11px
  - 单行省略保持 (white-space nowrap 仍在生效)
  - row 高度不显著增长 (R95 折叠紧凑保持)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 }
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
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") > 0:
                break
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          var eb = document.querySelector('.view-bv .bv-creed-card .card-eyebrow');
          if (!eb) return {none:true};
          var r = eb.getBoundingClientRect();
          var cs = getComputedStyle(eb);
          return {
            eb: { w: Math.round(r.width), h: Math.round(r.height), fontSize: cs.fontSize, whiteSpace: cs.whiteSpace, text: eb.textContent.trim().slice(0,40) }
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert "eb" in m, f"creed eyebrow not found"
        fs = float(m["eb"]["fontSize"].rstrip("px"))
        assert fs >= 11, f"font size should be ≥ 11px, got {m['eb']['fontSize']}"
        # R95 single-line ellipsis preserved
        assert m["eb"]["whiteSpace"] == "nowrap", f"R95 nowrap preserved, got {m['eb']['whiteSpace']}"
        # row height not significantly larger
        assert m["eb"]["h"] <= 22, f"eyebrow row too tall, got {m['eb']['h']}"

        await browser.close()
        print(f"[OK] R109 creed eyebrow — {m['eb']['fontSize']}, single-line preserved")

if __name__ == "__main__":
    asyncio.run(run())