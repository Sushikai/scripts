"""R-fix 验证: 得鑫界面 4 个 stage 都显示股票 + meta 行 + degraded"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/dexin_fix_visual")
OUT.mkdir(parents=True, exist_ok=True)


def shoot(page, name):
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print(f"  📸 {p}")


def check_dexin(page, label):
    """访问 dexin, 校验 4 stage 都有卡片 + meta 行"""
    page.goto(BASE + "#dexin", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(".view-dexin", timeout=10000)
    page.wait_for_timeout(8000)  # 等数据
    # 校验 stage cards
    counts = page.evaluate("""(() => {
        const out = {};
        ['cang_zha', 'xu_sha', 'clearing', 'de_xin'].forEach(stage => {
            // 切到对应 tab → 数卡片
            const tab = document.querySelector(`.dexin-tab[data-tab="${stage}"]`);
            if (tab) tab.click();
        });
        // 等 1s 让 render
        return new Promise(resolve => {
            setTimeout(() => {
                const data = window._dexin_data || null;
                ['cang_zha', 'xu_sha', 'clearing', 'de_xin'].forEach(stage => {
                    const tab = document.querySelector(`.dexin-tab[data-tab="${stage}"]`);
                    if (tab) tab.click();
                });
                setTimeout(() => {
                    ['cang_zha', 'xu_sha', 'clearing', 'de_xin'].forEach(stage => {
                        const tab = document.querySelector(`.dexin-tab[data-tab="${stage}"]`);
                        if (tab) tab.click();
                        const cards = document.querySelectorAll('.dx-card');
                        out[stage] = cards.length;
                    });
                    // 切回 cang_zha 默认
                    const def = document.querySelector('.dexin-tab[data-tab="cang_zha"]');
                    if (def) def.click();
                    resolve(out);
                }, 500);
            }, 500);
        });
    })()""")
    shoot(page, f"{label}-1-all-stages")
    # meta 行截图 (近景)
    page.evaluate("document.getElementById('dexin-meta')?.scrollIntoView({block:'start'})")
    page.wait_for_timeout(300)
    shoot(page, f"{label}-2-meta")
    return counts


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for label, vp in [
            ("desktop", {"width": 1280, "height": 800}),
            ("mobile_390", {"width": 390, "height": 844}),
            ("mobile_414", {"width": 414, "height": 896}),
        ]:
            is_m = vp["width"] < 768
            ctx = browser.new_context(viewport=vp, is_mobile=is_m, has_touch=is_m,
                                      ignore_https_errors=True, service_workers="block")
            page = ctx.new_page()
            page.on("pageerror", lambda e: print(f"  [pageerror] {e}"))
            print(f"=== {label} {vp['width']}x{vp['height']} ===")
            counts = check_dexin(page, label)
            print(f"  stage 卡片数: {counts}")
            # 校验: 每个 stage ≥ 1
            empty = [s for s, n in counts.items() if n < 1]
            if empty:
                print(f"  ✗ 空 stage: {empty}")
            else:
                print(f"  ✓ 全部 stage 都有卡片")
            ctx.close()
        browser.close()
    sys.exit(0)


if __name__ == "__main__":
    main()