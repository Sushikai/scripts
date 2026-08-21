"""R88 footer 在列表尾部 + 可达 — 存在性≠可达性.

原: #bv-loadmore + #bv-loadmore-end 在卡片顶部 (card-head 下, table 上方),
    R47 rewrite 又误删了 R41-R45 的 .bv-loadmore* CSS → 加载完成 footer
    "已加载全部 N 只 / ↓ 加载更多 / ↑ 返回顶部" 以无样式文本显示在页面顶部,
    用户看列表时永远看不到 (存在但不可达).
R88: DOM 移到 .bv-table-wrap 内 table 之后 (列表尾部) + 恢复 sticky footer 样式.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
body { background:#0e1116; margin:0; font-family:-apple-system,'PingFang SC',sans-serif; }
.bv-table-wrap { overflow-x:auto; }
.bv-table { width:100%; border-collapse:collapse; }
.bv-table td { padding:8px; font-size:13px; color:#ddd; }
.bv-loadmore { display:flex; align-items:center; justify-content:center; gap:8px; padding:14px 0 16px; font-size:12px; color:#888; }
.bv-loadmore[hidden] { display:none !important; }
.bv-loadmore-spin { width:14px; height:14px; border-radius:50%; border:2px solid #333; border-top-color:#00e0ff; animation:bv-spin .8s linear infinite; }
@keyframes bv-spin { to { transform:rotate(360deg); } }
.bv-loadmore-end { display:block; padding:10px 0; margin-top:6px; text-align:center; font-size:12px; color:#888; border-top:1px dashed #333; position:sticky; bottom:0; background:rgba(15,15,18,.92); backdrop-filter:blur(8px); z-index:4; }
.bv-loadmore-end[hidden] { display:none !important; }
.bv-loadmore-btn { margin-left:6px; padding:8px 14px; border-radius:8px; background:#222; color:#eee; border:1px solid #444; font-size:13px; font-weight:600; cursor:pointer; }
.bv-retry-btn { color:#ffcc00; border-color:rgba(255,204,0,.35); }
.bv-end-err { color:#ffcc00; }
.bv-end-top { margin-left:8px; color:#00e0ff; cursor:pointer; text-decoration:none; font-weight:600; }
"""

HTML = """
<!DOCTYPE html><html><head><style>__CSS__</style></head><body>
<div class="bv-table-wrap">
  <table class="bv-table" id="tbl">
    <tbody id="tbody">
      <tr><td>600000</td></tr><tr><td>600001</td></tr>
    </tbody>
  </table>
  <div class="bv-loadmore" id="bv-loadmore" hidden>
    <span class="bv-loadmore-spin"></span><span>加载中…</span>
  </div>
  <div class="bv-loadmore-end" id="bv-loadmore-end" hidden></div>
</div>
</body></html>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(HTML.replace("__CSS__", CSS))

        # 1) footer 在 table 之后 (列表尾部, 非卡片顶部) — 先 unhide 才有 rect
        await page.evaluate("""() => {
          document.getElementById('bv-loadmore').hidden = false;
          document.getElementById('bv-loadmore-end').hidden = false;
        }""")
        pos = await page.evaluate("""() => {
          var tbl = document.getElementById('tbl');
          var lm = document.getElementById('bv-loadmore');
          var end = document.getElementById('bv-loadmore-end');
          var tbox = tbl.getBoundingClientRect();
          var lbox = lm.getBoundingClientRect();
          var ebox = end.getBoundingClientRect();
          return { lmAfterTable: lbox.top >= tbox.bottom - 1,
                   endAfterTable: ebox.top >= tbox.bottom - 1,
                   endBelowLm: ebox.top >= lbox.top - 1 };
        }""")
        print(f"position: {pos}")
        assert pos["lmAfterTable"], "R88: 加载提示必须在表格之后"
        assert pos["endAfterTable"], "R88: footer 必须在表格之后"
        assert pos["endBelowLm"], "R88: footer 在加载提示之下"

        # 2) 渲染 loaded footer → 可见 + sticky
        await page.evaluate("""() => {
          var e = document.getElementById('bv-loadmore-end');
          e.hidden = false;
          e.innerHTML = '已加载全部 50 只 · <a class="bv-end-top" href="javascript:void(0)">↑ 返回顶部</a>';
        }""")
        state = await page.evaluate("""() => {
          var e = document.getElementById('bv-loadmore-end');
          var cs = getComputedStyle(e);
          return { text: e.textContent, sticky: cs.position,
                   bg: cs.backgroundColor, z: cs.zIndex,
                   height: e.getBoundingClientRect().height };
        }""")
        print(f"loaded footer: {state}")
        assert "已加载全部 50 只" in state["text"]
        assert "返回顶部" in state["text"]
        assert state["sticky"] == "sticky", "R88: footer 必须 sticky"
        assert state["bg"] != "rgba(0, 0, 0, 0)", "R88: sticky footer 需底色"
        assert state["height"] > 20, "R88: footer 必须可见高度"

        # 3) hasmore 态 → ↓ 加载更多 按钮 + 可点
        await page.evaluate("""() => {
          var e = document.getElementById('bv-loadmore-end');
          e.hidden = false;
          e.innerHTML = '<button class="bv-loadmore-btn">↓ 加载更多</button>';
        }""")
        btn = await page.evaluate("""() => {
          var b = document.querySelector('.bv-loadmore-btn');
          if (!b) return null;
          var cs = getComputedStyle(b);
          return { text: b.textContent, w: b.getBoundingClientRect().width,
                   h: b.getBoundingClientRect().height, bg: cs.backgroundColor };
        }""")
        print(f"loadmore btn: {btn}")
        assert btn and "加载更多" in btn["text"]
        assert btn["h"] >= 30, "R88: 按钮需可点高度"

        # 4) spinner 样式 (加载中)
        await page.evaluate("document.getElementById('bv-loadmore').hidden = false")
        spin = await page.evaluate("""() => {
          var s = document.querySelector('.bv-loadmore-spin');
          return s ? { w: s.getBoundingClientRect().width, h: s.getBoundingClientRect().height } : null;
        }""")
        print(f"spin: {spin}")
        assert spin and spin["w"] > 0 and spin["h"] > 0, "R88: spinner 必须可见"

        print("[OK] R88 footer at list tail + reachable + sticky")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
