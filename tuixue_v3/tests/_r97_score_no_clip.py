"""R97 决策信号必须有专属工位 — 分数条不裁剪、不与操作按钮/连板 chip 重叠.

原: 全局 .data-table td { padding:9px 8px !important } 泄漏进卡片格 → 绝对定位
    td.bv-score (width:72px border-box) 内容盒只有 56px, 分数条(56)+数值(14) 溢出,
    数值右缘(r:364) 越过 📈 跳转按钮左缘(l:324) 叠在按钮上; top:26px 又与
    📈(top:4, 全局 button min-height:40px 撑到 42px) + 连板 chip 垂直重叠.
    avgline + ×N均 标签存在时更糟 (r:392 溢出卡片).

R97: 决策信号必须有专属工位 —
  1. 卡片格一律 padding:0 !important (grid 布局供位, 信息不该带表格 padding)
  2. 分数格从右上角浮动移入 burst 格 (grid-area:burst, 右下角, 无炸板时空闲)
  3. 无炸板: 空格让位给分数; 有炸板(少/弱): 分数让位, 详情行仍展示分数组成
断言 (mock 数据, score=90 → 有 rel 标签 ×1.2均):
  - scoreOverflows == false (sw ≤ cw)
  - score 不与 jump 按钮重叠
  - score 不与 streak(连板 chip) 重叠
  - rel 标签在卡片内
  - 有炸板数据时 score 让位 (bv-score-yield 隐藏, burst 显示)
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'昨日分歧今日修复', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'低位首板', category:'首板', description:'低位首板试错', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' },
    { id:'BV03', title:'回封', category:'回封', description:'炸板回封', score_weight:6, conditions:[], quote:'...', timestamp:'00:03' },
    { id:'BV04', title:'卡位', category:'卡位', description:'板块卡位', score_weight:7, conditions:[], quote:'...', timestamp:'00:04' },
    { id:'BV05', title:'二板分歧', category:'分歧', description:'二板分歧转一致', score_weight:9, conditions:[], quote:'...', timestamp:'00:05' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01','BV02'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0 },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV03','BV04','BV05'], score:76,
      change_pct:3.2, amount_yi:45.2, volume_ratio:1.8, turnover_pct:5.2, seal_ratio:20,
      sector:'银行', first_time:'10:05', phase:'close', burst_count:2 },
    { code:'300750', name:'宁德时代', streak:1, matched_rules:['BV02'], score:68,
      change_pct:1.5, amount_yi:32.1, volume_ratio:1.2, turnover_pct:3.1, seal_ratio:10,
      sector:'电池', first_time:'14:20', phase:'close', burst_count:0 }
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

        def R(el):
            r = el.getBoundingClientRect()
            return {"l": round(r.left), "r": round(r.right), "t": round(r.top), "b": round(r.bottom), "w": round(r.width)}

        def ol(a, b):
            return not (a["r"] < b["l"] or b["r"] < a["l"] or a["b"] < b["t"] or b["b"] < a["t"])

        m = await page.evaluate("""() => {
          var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
          if (!rows.length) return { picks: 0 };
          var c1 = rows[0];          // score=90 no burst → score should own burst slot
          var c2 = rows[1];          // burst_count=2 → score should yield
          var R = function(el){ var r = el.getBoundingClientRect();
            return {l:Math.round(r.left), r:Math.round(r.right), t:Math.round(r.top), b:Math.round(r.bottom), w:Math.round(r.width)} };
          var ol = function(a,b){ return !(a.r < b.l || b.r < a.l || a.b < b.t || b.b < a.t); };
          var score1 = c1.querySelector('td.bv-score');
          var jump1 = c1.querySelector('.bv-jump-btn');
          var streak1 = c1.querySelector('td:nth-child(6)');
          var burst1 = c1.querySelector('td:nth-child(9)');
          var rel1 = score1.querySelector('.bv-score-rel');
          var rowR1 = c1.getBoundingClientRect();
          var out = { picks: rows.length };
          out.score1 = R(score1);
          out.score1SW = score1.scrollWidth; out.score1CW = score1.clientWidth;
          out.score1Overflows = score1.scrollWidth > score1.clientWidth + 1;
          out.score1Jump = ol(R(score1), R(jump1));
          out.score1Streak = ol(R(score1), R(streak1));
          out.burst1 = R(burst1);
          out.burst1EmptyCls = burst1.className;
          out.burst1Display = getComputedStyle(burst1).display;
          if (rel1) out.rel1Inside = Math.round(rel1.getBoundingClientRect().right) <= Math.round(rowR1.right);
          out.rel1Right = rel1 ? Math.round(rel1.getBoundingClientRect().right) : null;
          out.row1Right = Math.round(rowR1.right);
          // card 2: burst=2 → score yields
          var score2 = c2.querySelector('td.bv-score');
          var burst2 = c2.querySelector('td:nth-child(9)');
          out.score2Cls = score2.className;
          out.score2Display = getComputedStyle(score2).display;
          out.burst2Text = burst2.textContent;
          out.burst2Cls = burst2.className;
          out.burst2Display = getComputedStyle(burst2).display;
          return out;
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        # ── assertions ──
        assert m["picks"] > 0, "no picks rendered"
        assert m["score1Overflows"] == False, f"score must not overflow, sw={m['score1SW']} cw={m['score1CW']}"
        assert m["score1Jump"] == False, "score must not overlap jump btn"
        assert m["score1Streak"] == False, "score must not overlap streak chip"
        assert m["burst1Display"] == "none", "empty burst cell must yield to score (display:none)"
        assert m["rel1Inside"] == True, f"rel label must stay inside card (rel right {m['rel1Right']} > row right {m['row1Right']})"
        # 炸板>0: score yields, burst shows count
        assert m["score2Display"] == "none", "score must yield when burst_count>0"
        assert m["burst2Text"].strip() == "2", "burst count must show when present"

        await browser.close()
        print("[OK] R97 score cell: no clip, owns burst slot, yields to 炸板 count")

if __name__ == "__main__":
    asyncio.run(run())
