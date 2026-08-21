"""R182 re-render 破坏用户交互状态 — open <details>, 复盘 strip 展开, toc 展开.

用户展开 .msg-toc → 等 loading tick 触发 re-render → 检查 .msg-toc 是否仍 open.
BEFORE: re-render 全 innerHTML 替换 → open 状态丢失.
AFTER : 增量 diff → 用户交互状态保留.
"""
import json, time
from playwright.sync_api import sync_playwright

def run(url):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        for _ in range(60):
            if page.evaluate("() => typeof window._renderYerenAiMessages === 'function'"): break
            time.sleep(0.5)
        # 注入 1 条超长 markdown 触发折叠 / TOC 逻辑 (>600 字 + ≥3 ## 标题)
        long_md = "\n\n".join(f"## 第{i}节\n\n**结论**: 强买点 +{i}%, 止损位 {100+i}.50 元。" for i in range(8)) + "\n\n龙虎榜 北向资金 加仓 +4500 万, 5日线 支撑, MACD 金叉, KDJ 超买, 龙头 净利 +30%。" * 6
        page.evaluate(f"""() => {{
            window.yerenAiHistory = [{{ id: 't1', role: 'user', content: '推荐一只', ts: 1700000000000 }}, {{ id: 'a1', role: 'assistant', content: {json.dumps(long_md)}, ts: 1700000001000 }}];
        }}""") if False else page.evaluate(f"""() => {{
            window.yerenAiHistory = [{{ id: 't1', role: 'user', content: '推荐一只', ts: 1700000000000 }}, {{ id: 'a1', role: 'assistant', content: {json.dumps(long_md)}, ts: 1700000001000 }}];
            window._renderYerenAiMessages();
        }}""")
        # 展开 TOC + 展开折叠
        page.evaluate("""() => {
            const toc = document.querySelector('.msg-toc');
            if (toc) toc.open = true;
            const fold = document.querySelector('.msg-fold');
            const btn = document.querySelector('.msg-fold-btn');
            if (btn) btn.click();
        }""")
        before = page.evaluate("""() => ({
            toc_open: !!document.querySelector('.msg-toc[open]'),
            fold_hidden: !!(document.querySelector('.msg-fold[hidden]') || !document.querySelector('.msg-fold')),
            full_visible: !!document.querySelector('.msg-bubble-full:not([hidden])')
        })""")
        # 触发一次 re-render (模拟 progress tick)
        page.evaluate("() => window._renderYerenAiMessages()")
        after = page.evaluate("""() => ({
            toc_open: !!document.querySelector('.msg-toc[open]'),
            fold_hidden: !!(document.querySelector('.msg-fold[hidden]') || !document.querySelector('.msg-fold')),
            full_visible: !!document.querySelector('.msg-bubble-full:not([hidden])')
        })""")
        b.close()
        return {"before": before, "after": after, "lost": before != after}

if __name__ == "__main__":
    print(json.dumps(run("http://localhost:7799/"), ensure_ascii=False, indent=1))