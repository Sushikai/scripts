"""R183 re-render DOM 节点数 — 一次 re-render 的 node 创建量 + 同 history 第二次 render 是否同结构.
"""
import json, time
from playwright.sync_api import sync_playwright

def sample_msg(i):
    names = ['茅台','宁德','中芯','药明','比亚迪']
    return f"## {names[i%5]}\n\n**结论**: 买入 +{i}%, 止损 {100+i}.5 元。\n\n- 涨停 2026-08-19 放量, 封板 2.5 亿\n- 龙虎榜 净买入 1.2 亿, 北向 +3200 万\n- MACD 金叉, KDJ 超买 80\n\n| 维度 | 数值 |\n|---|---|\n| 主力 | +4800 万 |\n| 换手 | 5.2% |\n\n建议关注 5日线 支撑, 人工智能 板块 +3.5%。"

def run(url):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        for _ in range(60):
            if page.evaluate("() => typeof window._renderYerenAiMessages === 'function'"): break
            time.sleep(0.5)
        msgs = [{"id": f"r183_{i}", "role": "assistant" if i%2 else "user", "content": sample_msg(i), "ts": 1700000000000+i} for i in range(30)]
        page.evaluate(f"() => {{ window.yerenAiHistory = {json.dumps(msgs)}; }}")
        page.evaluate("() => window._renderYerenAiMessages()")
        n1 = page.evaluate("() => document.querySelectorAll('#yeren-ai-history *').length")
        page.evaluate("() => window._renderYerenAiMessages()")
        n2 = page.evaluate("() => document.querySelectorAll('#yeren-ai-history *').length")
        b.close()
    return {"nodes_after_render_1": n1, "nodes_after_render_2": n2, "stable": n1 == n2}

if __name__ == "__main__":
    print(json.dumps(run("http://localhost:7799/"), ensure_ascii=False, indent=1))