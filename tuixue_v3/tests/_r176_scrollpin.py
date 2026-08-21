"""R176+R177 流式 pin scroll lag 量化 — 快速追加下追底 lag vs CSS scroll-behavior.

模拟流式: 每 16ms (~60fps) 追加一个 chunk + 调 pin(), 同帧采样
distFromBottom = scrollHeight - scrollTop - clientHeight.
- behavior smooth → dist 累积 >0
- behavior auto 且无 CSS scroll-behavior → dist 恒 0
- behavior auto 但有 CSS scroll-behavior:smooth → CSS 覆盖, 仍 lag (R177 要修这个)
"""
import json, tempfile, os
from playwright.sync_api import sync_playwright

def html(css_smooth):
    sb = "#box { scroll-behavior: smooth; }" if css_smooth else ""
    return f"""<!DOCTYPE html><html><head><meta charset=utf-8><style>
#box {{ height:300px; width:400px; overflow-y:auto; }}
{sb}
.c {{ height:24px; border-bottom:1px solid #eee; }}
</style></head><body><div id="box"></div></body></html>"""

def run(behavior, css_smooth, chunks=30):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
    tmp.write(html(css_smooth)); tmp.close()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport={"width": 500, "height": 500})
            page.goto("file://" + tmp.name)
            js = f"""(() => {{
                const box = document.getElementById('box');
                const samples = [];
                let i = 0;
                const pin = () => box.scrollTo({{ top: box.scrollHeight, behavior: '{behavior}' }});
                return new Promise((resolve) => {{
                    const iv = setInterval(() => {{
                        const c = document.createElement('div'); c.className='c'; c.textContent='chunk '+i;
                        box.appendChild(c);
                        pin();
                        samples.push(box.scrollHeight - box.scrollTop - box.clientHeight);
                        i++;
                        if (i >= {chunks}) {{ clearInterval(iv); resolve(samples); }}
                    }}, 16);
                }});
            }})()"""
            samples = page.evaluate(js)
            b.close()
        nz = sum(1 for s in samples if s > 0)
        return {"behavior": behavior, "css_smooth": css_smooth, "max_dist": max(samples),
                "avg_dist": round(sum(samples) / len(samples), 1),
                "pct_nonzero": round(100 * nz / len(samples), 1)}
    finally:
        os.unlink(tmp.name)

if __name__ == "__main__":
    cases = [
        run("auto", True, chunks=30),    # 当前状态: R176 auto 被 CSS scroll-behavior 覆盖
        run("auto", False, chunks=30),   # R177: 移除 CSS → auto 真正即时
        run("smooth", False, chunks=30), # 对照: 一直 smooth 的本体
    ]
    print(json.dumps(cases, ensure_ascii=False, indent=1))
