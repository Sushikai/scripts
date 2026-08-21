"""R189 反复 fetch /api/yeren/ai/related — 计数 progress tick re-render 期间的请求数.

拦截 fetch: 对 related 接口计数. 注入 5 条 assistant 历史 + 重渲染 4 次.
BEFORE: 4×5=20 次请求 (每 tick 全重拉)
AFTER : 1×5=5 次请求 (首次拉, 后续走 _relatedFetched)
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
        # 拦截 fetch, 计数 related 接口
        page.evaluate("""() => {
            window.__relatedCalls = 0;
            const orig = window.fetch;
            window.fetch = function(url, ...args) {
                if (typeof url === 'string' && url.includes('/api/yeren/ai/related/')) {
                    window.__relatedCalls++;
                    // mock: 立即返回空
                    return Promise.resolve(new Response(JSON.stringify({ok:true,data:{items:[]}}), {status:200, headers:{'Content-Type':'application/json'}}));
                }
                return orig.call(this, url, ...args);
            };
        }""")
        msgs = [{"id": f"r189_{i}", "role": "assistant", "content": f"## 第{i}节\n\n**结论**: 强买 +{i}%, 止损 {100+i}.5 元。", "ts": int(time.time()*1000)-100, "related_code": "600519"} for i in range(5)]
        page.evaluate(f"() => {{ window.yerenAiHistory = {json.dumps(msgs)}; }}")
        page.evaluate("() => window._renderYerenAiMessages()")
        page.wait_for_timeout(200)
        c1 = page.evaluate("() => window.__relatedCalls")
        # 4 次 re-render
        for _ in range(4):
            page.evaluate("() => window._renderYerenAiMessages()")
            page.wait_for_timeout(50)
        c_final = page.evaluate("() => window.__relatedCalls")
        b.close()
    return {"after_1st_render": c1, "after_4_more_renders": c_final, "saved_calls": c_final - c1}

if __name__ == "__main__":
    print(json.dumps(run("http://localhost:7799/"), ensure_ascii=False, indent=1))