"""R187 消息入场动画 re-render 重放 — 每次 _renderYerenAiMessages() 都重置 DOM,
每条 .msg-row-fresh 行 (无论新旧) 的 CSS animation 重新播放.

BEFORE: progress tick 每 300ms 一次 re-render → 整页消息 fade-in 重播, 视觉闪烁.
AFTER : 跳过未变消息的 animation (只在 id 第一次出现时打 fresh 类).
"""
import json, time
from playwright.sync_api import sync_playwright

def sample_msg(i):
    return f"## 第{i}节\n\n**结论**: 强买 +{i}%, 止损 {100+i}.5 元。"

def run(url):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        for _ in range(60):
            if page.evaluate("() => typeof window._renderYerenAiMessages === 'function'"): break
            time.sleep(0.5)
        msgs = [{"id": f"r187_{i}", "role": "assistant" if i%2 else "user", "content": sample_msg(i), "ts": int(time.time()*1000) - 100} for i in range(10)]
        page.evaluate(f"() => {{ window.yerenAiHistory = {json.dumps(msgs)}; }}")
        page.evaluate("() => window._renderYerenAiMessages()")
        n_fresh = page.evaluate("() => document.querySelectorAll('.msg-row-fresh').length")
        # 立即 re-render (无新消息)
        page.evaluate("() => window._renderYerenAiMessages()")
        n_fresh_2 = page.evaluate("() => document.querySelectorAll('.msg-row-fresh').length")
        b.close()
    return {"fresh_after_1st_render": n_fresh, "fresh_after_2nd_render": n_fresh_2,
            "replay_bug": n_fresh_2 == n_fresh and n_fresh > 0}

if __name__ == "__main__":
    print(json.dumps(run("http://localhost:7799/"), ensure_ascii=False, indent=1))