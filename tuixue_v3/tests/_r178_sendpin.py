"""R178 发送贴底 — 用户显式发送必须无条件贴底 (区别于被动流式仅近底贴).

模拟: 容器已有大量历史, 用户滚到上方 (dist≈300px), 触发 send (soft pin vs soft+force pin),
settle 后测最终 distFromBottom = scrollHeight - scrollTop - clientHeight.
"""
import json, tempfile, os
from playwright.sync_api import sync_playwright

HTML = """<!DOCTYPE html><html><head><meta charset=utf-8><style>
#box { height:300px; width:400px; overflow-y:auto; }
.c { height:24px; border-bottom:1px solid #eee; }
</style></head><body><div id="box"></div></body></html>"""

def run(force_pin):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
    tmp.write(HTML); tmp.close()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport={"width": 500, "height": 500})
            page.goto("file://" + tmp.name)
            js = f"""(() => {{
                const box = document.getElementById('box');
                const softPin = () => {{
                    const STICKY_PX = 80;
                    const dist = box.scrollHeight - box.scrollTop - box.clientHeight;
                    if (dist < STICKY_PX) box.scrollTop = box.scrollHeight;
                }};
                // 历史: 60 chunks → 溢出; 用户滚到上方, dist≈300
                for (let i = 0; i < 60; i++) {{ const c = document.createElement('div'); c.className='c'; c.textContent='hist '+i; box.appendChild(c); }}
                box.scrollTop = box.scrollHeight - 300 - box.clientHeight;
                const distBefore = box.scrollHeight - box.scrollTop - box.clientHeight;
                // send 流程: push user msg + loading → soft pin (渲染内) → 可选 force pin
                const a = document.createElement('div'); a.className='c'; a.textContent='USER'; box.appendChild(a);
                const l = document.createElement('div'); l.className='c'; l.textContent='LOADING'; box.appendChild(l);
                softPin();
                if ({'true' if force_pin else 'false'}) {{
                    box.scrollTo({{ top: box.scrollHeight, behavior: 'smooth' }});
                }}
                return new Promise((resolve) => {{
                    setTimeout(() => {{
                        resolve({{ dist_before_send: distBefore,
                                   dist_after: box.scrollHeight - box.scrollTop - box.clientHeight }});
                    }}, 400);  // 等 smooth 动画 settle
                }});
            }})()"""
            out = page.evaluate(js)
            b.close()
        return {"force_pin": force_pin, **out}
    finally:
        os.unlink(tmp.name)

if __name__ == "__main__":
    print(json.dumps([run(False), run(True)], ensure_ascii=False, indent=1))
