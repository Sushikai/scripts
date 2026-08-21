"""R179 嵌套滚动链 — textarea (at-max) 滚到边界, wheel 不得链到背后的消息流.

布局: #msg 可滚动容器 (历史) + 下方 #ta 高 126px 可滚动 textarea.
把 #msg.scrollTop 置中, #ta.scrollTop=0 (滚动起点), 在 #ta 上 wheel 向上滚,
测 #msg.scrollTop 是否被链动. 默认 overscroll-behavior:auto → 链动; contain → 隔离.
"""
import json, tempfile, os
from playwright.sync_api import sync_playwright

def html(contain):
    ob = "#ta { overscroll-behavior: contain; }" if contain else ""
    return f"""<!DOCTYPE html><html><head><meta charset=utf-8><style>
body {{ display:flex; flex-direction:column; }}
#msg {{ height:260px; overflow-y:auto; }}
.m {{ height:22px; border-bottom:1px solid #ddd; }}
#ta {{ height:126px; overflow-y:auto; width:400px; }}
.t {{ height:24px; }}
{ob}
</style></head><body>
<div id="msg"></div><textarea id="ta"></textarea>
</body></html>"""

def run(contain):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
    tmp.write(html(contain)); tmp.close()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport={"width": 500, "height": 500})
            page.goto("file://" + tmp.name)
            page.evaluate("""() => {
                const msg = document.getElementById('msg');
                for (let i=0;i<30;i++){const c=document.createElement('div');c.className='m';c.textContent='hist '+i;msg.appendChild(c);}
                const ta = document.getElementById('ta');
                let s='';
                for (let i=0;i<30;i++) s += 'line '+i+'\\n';
                ta.value = s;
                ta.scrollTop = 0;
                msg.scrollTop = 150;  // 中位, 非顶非底
            }""")
            before = page.evaluate("() => document.getElementById('msg').scrollTop")
            # 把指针悬停在 textarea 上, 向上滚 3 次 (deltaY<0 = 向上)
            ta_box = page.locator('#ta').bounding_box()
            page.mouse.move(ta_box['x'] + ta_box['width'] / 2, ta_box['y'] + ta_box['height'] / 2)
            for _ in range(3):
                page.mouse.wheel(0, -120)
            page.wait_for_timeout(100)
            after = page.evaluate("() => document.getElementById('msg').scrollTop")
            ta_top = page.evaluate("() => document.getElementById('ta').scrollTop")
            b.close()
        return {"contain": contain, "msg_before": before, "msg_after": after,
                "msg_chained": round(after - before, 1), "ta_scrollTop": ta_top}
    finally:
        os.unlink(tmp.name)

if __name__ == "__main__":
    print(json.dumps([run(False), run(True)], ensure_ascii=False, indent=1))
