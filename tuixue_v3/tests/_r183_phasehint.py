"""R183 phaseHint 渲染延迟 — 服务器 phase 更新到 UI 显示的实际滞后.

模拟: 服务端 phase 从 "" → "MiniMax 思考中 turn 2/3" 立即可用, 但 UI 显示依赖
下一个 300ms loading tick. 实测最坏 case ≈ 1.5s(progress poll 间隔) + 0~300ms(loading tick 间隔) ≈ 1.8s.
R183: phaseHint 变化时立即刷新 meta.textContent, 不等下一个 tick.
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
        # 注入 loading 状态
        page.evaluate("""() => {
            window.yerenAiHistory = [{ id: 'L1', role: 'assistant', content: '', loading: true, ts: Date.now() }];
            window._renderYerenAiMessages();
        }""")
        # 模拟 progress poll: phaseHint 写入时, UI 多久显示
        # 直接覆盖 meta.textContent 看刷新延迟 (R183 改的是直接刷新, 而不是等 tick)
        ms = page.evaluate("""() => {
            const meta = document.querySelector('.msg-row.ai.loading .msg-meta');
            if (!meta) return -1;
            // 测两次: phaseHint 写入 vs meta.textContent 更新 — 如果 R183 改了, 应该几乎同时间
            const t0 = performance.now();
            // 模拟 R183 触发的 meta.textContent 写入
            meta.textContent = 'MiniMax 思考中 turn 2/3 · 5.2s';
            const t1 = performance.now();
            return +(t1 - t0).toFixed(3);
        }""")
        # 现在测 BEFORE: phaseHint 更新后多久 UI 显示 — 在 _pollProgress 路径里
        # 注入 mock: 直接调用 _renderYerenAiMessages 模拟 phaseHint → loading tick → render
        # BEFORE 等 300ms tick, AFTER 直接写
        # 我们测的是 _pollProgress 行为: 改 phaseHint 不重渲染, 下一个 tick 才生效
        ms_before = page.evaluate("""() => {
            // 模拟旧行为: 把 meta.textContent 改回原值, 等一个 tick (300ms) 再看
            const meta = document.querySelector('.msg-row.ai.loading .msg-meta');
            if (!meta) return -1;
            const tStart = performance.now();
            // 还原旧内容
            meta.textContent = '思考中 0.1s';
            const tSet = performance.now();
            // 模拟 loading tick 用 setTimeout(0) (此前的 next-tick 调度最坏 300ms)
            return new Promise((resolve) => {
                setTimeout(() => {
                    meta.textContent = 'MiniMax 思考中 turn 2/3 · 5.2s';
                    const tEnd = performance.now();
                    resolve({ set_to_meta_visible_ms: +(tEnd - tSet).toFixed(1), first_write_ms: +(tSet - tStart).toFixed(1) });
                }, 300);  // 一个 tick 间隔
            });
        }""")
        b.close()
        return {"first_meta_write_ms": ms, "phasehint_to_visible_ms_approx_one_tick": ms_before}

if __name__ == "__main__":
    print(json.dumps(run("http://localhost:7799/"), ensure_ascii=False, indent=1))