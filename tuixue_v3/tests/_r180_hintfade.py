"""R180 提示淡出 vs 新消息增长 — 刷新 count 时必须重置 5s 淡出计时器.

真实计时器模拟: t=0 显示 hint (启动 5s fade timer), t=3s 刷新 count (2→5),
记录 fade-out class 首次出现的时间.
BEFORE: timer 不重置 → fade 于 t=5.0s (刷新后 2s 就 fade, 信号正变强却消失).
AFTER:  刷新重置 timer → fade 于 t=8.0s (刷新后满 5s).
"""
import json, tempfile, os
from playwright.sync_api import sync_playwright

HTML = """<!DOCTYPE html><html><head><meta charset=utf-8><style>
#hint { position:fixed; bottom:20px; opacity:1; pointer-events:auto; }
#hint.fade-out { opacity:0; }
</style></head><body><button id="hint">▼ 2 条新消息</button></body></html>"""

def run(reset):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
    tmp.write(HTML); tmp.close()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport={"width": 400, "height": 400})
            page.goto("file://" + tmp.name)
            js = f"""(() => {{
                const h = document.getElementById('hint');
                h._fadeTimer = setTimeout(() => h.classList.add('fade-out'), 5000);
                const t0 = Date.now();
                const fadeAt = [];
                return new Promise((resolve) => {{
                    // t=3s 刷新 count
                    setTimeout(() => {{
                        h.textContent = '▼ 5 条新消息';
                        if ({'true' if reset else 'false'}) {{
                            h.classList.remove('fade-out');
                            clearTimeout(h._fadeTimer);
                            h._fadeTimer = setTimeout(() => h.classList.add('fade-out'), 5000);
                        }}
                    }}, 3000);
                    // 每 100ms 轮询 fade-out 是否首次出现
                    const poll = setInterval(() => {{
                        if (h.classList.contains('fade-out')) {{
                            fadeAt.push(Math.round((Date.now() - t0) / 100) / 10);
                            clearInterval(poll);
                            resolve({{ fade_sec: fadeAt[0] }});
                        }}
                    }}, 100);
                }});
            }})()"""
            out = page.evaluate(js)
            b.close()
        return {"reset": reset, **out}
    finally:
        os.unlink(tmp.name)

if __name__ == "__main__":
    print(json.dumps([run(False), run(True)], ensure_ascii=False, indent=1))
