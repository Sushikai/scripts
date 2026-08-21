"""R300 轻量验证 harness — 不依赖服务器, 直接 file:// 加载 tokens.css + style.css.

每个 round 的 M_after 用 getComputedStyle 秒出, 避免 server 不稳定拖慢。
用法:
  python3 tests/r300_verify.py 'sel1' 'prop1' 'sel2' 'prop2' ...
  输出 JSON: {"sel1": {"prop1": "value", ...}, ...}
"""
import json, sys, tempfile, os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path("/Users/kaikai/scripts/tuixue_v3")
CSS = (ROOT / "web/static/tokens.css").read_text() + "\n" + (ROOT / "web/static/style.css").read_text()

# 探针元素: 覆盖 AI block 常用的类
PROBES = """
<div id="probe">
  <div class="msg-row ai"><div class="msg-bubble ai" id="bai">text</div></div>
  <div class="msg-row user"><div class="msg-bubble user" id="bus">text</div></div>
  <div class="yeren-ai-messages" id="msgcont"></div>
  <div class="yeren-ai-composer" id="composer"></div>
  <div class="msg-extras" id="extras"><code id="rscode">code</code></div>
  <div class="msg-meta" id="meta"><span class="msg-meta-time" id="mt">t</span></div>
  <div class="msg-dots" id="dots"></div>
  <div class="msg-copy-btn" id="copy"></div>
  <div class="msg-regen-btn" id="regen"></div>
  <div class="msg-retry-btn" id="retry"></div>
  <div class="rules-hit" id="rules"></div>
  <div class="msg-avatar ai" id="avatar"></div>
  <div class="yeren-ai-cancel-btn" id="cancel"></div>
  <h2 id="h2">t</h2>
  <h3 id="h3">t</h3>
  <div class="msg-bubble ai" id="bai2"><h1 id="bh1">t</h1><h2 id="bh2">t</h2><h3 id="bh3">t</h3><p id="bp">t</p><ul id="bul"><li id="bli">t</li></ul><code id="bcode">t</code><pre id="bpre">t</pre>
        <span class="pos" id="bpos">t</span><span class="neg" id="bneg">t</span><span class="risk" id="brisk">t</span>
        <span class="badge-mainline" id="bbadge">t</span><strong id="bstrong">t</strong>
        <div class="msg-skel" id="bskel"><div class="msg-skel-line w100" id="sk1"></div><div class="msg-skel-line w92" id="sk2"></div><div class="msg-skel-line w64" id="sk3"></div></div>
      </div>
  <div class="msg-bubble-inner" id="inner"></div>
  <textarea class="yeren-ai-msg-input" id="msginput" rows="1" placeholder="t"></textarea>
  <div class="yeren-ai-input-row" id="irow"><button class="yeren-ai-send-btn" id="sbtn" disabled>➤</button></div>
  <span class="yeren-ai-char-count" id="cc">850 字</span>
  <span class="yeren-ai-char-count over" id="cco">⚠ 2100 字</span>
  <div class="yeren-ai-welcome"><div class="welcome-logo" id="wlogo">🧠</div><h2 class="welcome-title" id="wtitle">晚上好</h2><p class="welcome-sub" id="wsub">基于 17 野人规则 + 5 套餐 + 42 铁律<br>给出明确的买卖建议 · 仓位 · 止损位 · 持有期</p><div class="welcome-keys" id="keys">Enter 发送 · Shift+Enter 换行</div><div class="welcome-caps" id="caps">📷 截图识图 · 🐉 龙虎榜</div>
    <div class="welcome-onboard" id="onboard"><span class="onboard-text" id="obtext">👋 首次使用? 点任一卡片或直接输入 — 答案会给明确的 <b>买卖点 · 仓位 · 止损位</b></span><button class="onboard-dismiss" id="obdismiss">知道了</button></div>
    <div class="welcome-hot"><div class="welcome-hot-label" id="hotlabel">🔥 最近 24h 热门</div><div class="welcome-hot-chip" id="hotchip"><span class="hot-code">600519</span><span class="hot-name">贵州茅台</span><span class="hot-count">×12</span></div></div>
    <div class="welcome-tip" id="tip">小提示 · 选个股票或直接问↓</div>
    <div class="welcome-grid"><button class="welcome-tile cat-buy" id="tile"><span class="tile-icon">🎯</span><span class="tile-title">能不能买?</span><span class="tile-desc">基于 17 野人规则给明确买卖点</span></button>
      <button class="welcome-tile cat-risk" id="tile-risk"><span class="tile-icon">⚠️</span><span class="tile-title">风险扫描</span></button>
      <button class="welcome-tile cat-top" id="tile-top"><span class="tile-icon">🐉</span><span class="tile-title">推荐龙头</span></button>
      <button class="welcome-tile cat-sect" id="tile-sect"><span class="tile-icon">🧬</span><span class="tile-title">板块诊断</span></button></div>
  <div style="display:flex; height:700px"><div class="yeren-ai-welcome" id="wcenter"><div class="welcome-logo">🧠</div></div></div>
  <button class="yeren-ai-scroll-hint show" id="scrolhint">▼ 2 条新消息</button>
  <div id="r175wrap" style="position:relative;display:flex;flex-direction:column;height:400px;width:600px;--composer-h:150.6px"><div style="flex:1">spacer</div><div class="yeren-ai-composer" id="r175comp"><textarea class="yeren-ai-msg-input" id="r175ta" style="height:120px"></textarea></div><button class="yeren-ai-scroll-hint show" id="r175hint">▼ 2 条新消息</button></div>
  <div style="display:flex; height:700px"><div class="yeren-ai-welcome" id="wover"><div class="welcome-logo">🧠</div><div class="welcome-tile cat-buy" id="tile-ov1">a</div><div class="welcome-tile cat-buy" id="tile-ov2">b</div><div class="welcome-tile cat-buy" id="tile-ov3">c</div><div class="welcome-tile cat-buy" id="tile-ov4">d</div><div class="welcome-tile cat-buy" id="tile-ov5">e</div><div class="welcome-tile cat-buy" id="tile-ov6">f</div><div class="welcome-tile cat-buy" id="tile-ov7">g</div><div class="welcome-tile cat-buy" id="tile-ov8">h</div><div class="welcome-tile cat-buy" id="tile-ov9">i</div></div></div></div>
</div>
"""

def main():
    args = sys.argv[1:]
    vp = {"width": 1440, "height": 900}
    emu = {}
    theme = "dark"
    if "mobile" in args:  # 可选: 传 'mobile' 用 390×844 视口测移动端 media query
        vp = {"width": 390, "height": 844}
        emu = {"is_mobile": True, "has_touch": True,
               "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"}
        args = [a for a in args if a != "mobile"]
    if "light" in args:  # 可选: 传 'light' 模拟亮色主题, 用于 R201+ dark/light parity
        theme = "light"
        args = [a for a in args if a != "light"]
    # 每对 sel,prop
    pairs = []
    for i in range(0, len(args) - 1, 2):
        pairs.append((args[i], args[i + 1]))
    if not pairs:
        print(json.dumps({"error": "usage: r300_verify.py sel prop [sel prop ...]"}))
        return
    html = f"""<!DOCTYPE html><html><head><meta charset=utf-8>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{CSS}</style></head><body data-theme="{theme}">{PROBES}</body></html>"""
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
    tmp.write(html)
    tmp.close()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport=vp, **emu)
            page.goto("file://" + tmp.name, wait_until="load")
            out = {}
            for sel, prop in pairs:
                pseudo = None
                base_sel = sel
                if "::" in sel:
                    pseudo = sel[sel.index("::"):]
                    base_sel = sel[:sel.index("::")]
                val = page.evaluate(
                    """([s, p, ps]) => {
                        const el = document.querySelector(s);
                        if (!el) return 'NO_EL';
                        if (p === 'bbox') {
                            const r = el.getBoundingClientRect();
                            return { x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1),
                                     cy: +((r.top + r.height / 2).toFixed(1)) };
                        }
                        return getComputedStyle(el, ps || null).getPropertyValue(p);
                    }""", [base_sel, prop, pseudo])
                out[f"{sel}:{prop}"] = val
            b.close()
        print(json.dumps(out))
    finally:
        os.unlink(tmp.name)

if __name__ == "__main__":
    main()
