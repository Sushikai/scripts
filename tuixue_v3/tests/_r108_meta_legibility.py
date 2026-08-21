"""R108 bv-meta 副标题可读性 — 10.5→11.5px (Apple caption 最低).

原: audit 实测 .view-bv .bv-meta 254×14px @ 10.5px font,
    副标题是战法名/版本/UP主 (身份识别关键), 但字号细到要凑近看。
    第一性原理: 身份信息要么消失要么可读, 半残最差。
R108: 10.5→11.5px (Apple iOS caption 最小可读), line-height 1.35→1.4 防截断。
    视觉高度 14→16px, 增 2px, 让"知道在哪个战法"零思考成本。
断言 (mock 数据, 390px):
  - bv-meta 字号 ≥ 11.5px (苹果 caption 最低)
  - bv-meta 行高 ≥ 1.4 (不裁切)
  - view-head 总高度不显著增长 (R99 保持紧凑, R108 只 +2px)
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
          var meta = document.querySelector('.view-bv .bv-meta');
          var head = document.querySelector('.view-bv .view-head');
          if (!meta) return {none:true};
          var r = meta.getBoundingClientRect();
          var cs = getComputedStyle(meta);
          return {
            meta: { w: Math.round(r.width), h: Math.round(r.height), fontSize: cs.fontSize, lineHeight: cs.lineHeight, text: meta.textContent.trim().slice(0,30) },
            headH: head ? Math.round(head.getBoundingClientRect().height) : null
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert "meta" in m, f"meta not found"
        # font size >= 11.5px (Apple caption minimum readable)
        fs = float(m["meta"]["fontSize"].rstrip("px"))
        assert fs >= 11.5, f"font size should be ≥ 11.5px, got {m['meta']['fontSize']}"
        # line height >= 1.4 (returned as '16.1px')
        lh_str = m["meta"]["lineHeight"]
        lh_px = float(lh_str.rstrip("px"))
        assert lh_px >= 16, f"line height should be ≥ 16px (1.4×11.5), got {lh_str}"
        # view-head total height shouldn't grow significantly (R108 only adds 2px to meta line)
        assert m["headH"] is not None and m["headH"] <= 100, f"view-head shouldn't grow much, got {m['headH']}"

        await browser.close()
        print(f"[OK] R108 meta legibility — {m['meta']['fontSize']}, lh {m['meta']['lineHeight']}")

if __name__ == "__main__":
    asyncio.run(run())