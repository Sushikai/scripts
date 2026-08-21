"""R181 _miniMd 记忆化测量 — 真实 app.js 上, 重复 _renderYerenAiMessages() 的耗时.

BEFORE: 无缓存, 每次 render 全量跑 _miniMd 40+ regex pass.
AFTER : memo 后, 第二次 render 的未变消息命中缓存 → 耗时应大幅下降.
"""
import json, time
from playwright.sync_api import sync_playwright

def sample_msg(i):
    # 生成 ~900 字符的典型野人回复 markdown (标题/粗体/列表/数字/表格/术语)
    kw = "买入 止损 支撑位 龙虎榜 涨停 MACD KDJ 人工智能 半导体 北向资金"
    names = ['茅台','宁德时代','中芯国际','药明康德','比亚迪','隆基绿能','中国中免','恒瑞医药']
    return f"""## 第{i}只 · {names[i % len(names)]}

**结论**: 可以买入, 目标价 128.50 元, 止损位 115.20 元, 持有期 10-15 天。
- 涨停 2026-08-19 放量突破 前高 124.80, 封板 2.5 亿
- 龙虎榜 净买入 1.2 亿, 北向资金 加仓 3200 万
- MACD 金叉, KDJ 超买 80, RSI 62, BOLL 中轨上方

| 维度 | 数值 | 信号 |
|------|------|------|
| 主力资金 | +4800 万 | 强 |
| 换手率 | 5.2% | 活跃 |
| 市值 | 1.8 万亿 | 大盘 |

{kw} 提示: 建议关注 5日线 支撑, 跌破 120.10 减仓, 风险警告 注意回调。
人工智能 板块 半日热度 +3.5%, 半导体 龙头 净利增速 +45.3%。
"""

def run(url):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # 等 app.js 的 render 函数可用
        for _ in range(60):
            ok = page.evaluate("() => typeof window._renderYerenAiMessages === 'function'")
            if ok: break
            time.sleep(0.5)
        # 注入历史 (8 条), 清空现有
        page.evaluate(f"""() => {{
            window.yerenAiHistory = {json.dumps([{"id": "r181_"+str(i), "role": "assistant", "content": sample_msg(i)*2, "ts": 1700000000000+i} for i in range(20)])};
            window._yerenAiLastSeenMsgCount = 0;
            const view = document.querySelector('.view-yeren-ai');
            if (view) view.classList.add('immersive');
        }}""")
        # pre-fill render to set up
        page.evaluate("() => { window._renderYerenAiMessages(); }")
        # warm + measure
        times = []
        counts = []
        for k in range(4):
            t0 = page.evaluate("() => performance.now()")
            page.evaluate("() => { window._renderYerenAiMessages(); }")
            t1 = page.evaluate("() => performance.now()")
            times.append(round(t1 - t0, 1))
        b.close()
    return {"times_ms": times, "render1": times[0], "render2": times[1], "render3": times[2], "render4": times[3]}

if __name__ == "__main__":
    print(json.dumps(run("http://localhost:7799/"), ensure_ascii=False, indent=1))
