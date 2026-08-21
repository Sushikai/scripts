"""R184 progress fill 宽度反映真实耗时 — 注入 loading + 10s elapsed 测 fill width.

BEFORE: fill width 固定 50% (shimmer 动画, 不反映实际进度).
AFTER : fill width = min(95, 10/100*100)% = 10% (单调递增).
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
        # 注入 loading 消息 + cancelBar (模拟 send 流程)
        page.evaluate("""() => {
            window.yerenAiHistory = [{ id: 'L1', role: 'assistant', content: '', loading: true, ts: Date.now() - 10000 }];
            window._renderYerenAiMessages();
            const row = document.querySelector('.msg-row.ai.loading');
            if (row) {
                const stack = row.querySelector('.msg-bubble-stack') || row;
                const cb = document.createElement('div');
                cb.className = 'yeren-ai-progress';
                cb.innerHTML = '<div class="yeren-ai-progress-fill"></div>';
                stack.appendChild(cb);
            }
        }""")
        page.wait_for_timeout(100)
        out = page.evaluate("""() => {
            const f = document.querySelector('.yeren-ai-progress-fill');
            const rect = f.getBoundingClientRect();
            const parent = f.parentElement;
            const pr = parent.getBoundingClientRect();
            const w = rect.width / pr.width * 100;
            const cs = getComputedStyle(f);
            return { width_pct_actual: +w.toFixed(1), computed_width: cs.width, animation: cs.animationName };
        }""")
        b.close()
        return out

if __name__ == "__main__":
    print(json.dumps(run("http://localhost:7799/"), ensure_ascii=False, indent=1))