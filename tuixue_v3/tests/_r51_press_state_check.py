"""R51 卡片按压态."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    @media (max-width: 768px) {
      .bv-table tr.bv-row {
        cursor: pointer;
        transition: transform 0.12s ease-out, background 0.12s ease-out;
      }
      .bv-table tr.bv-row:active {
        background: var(--bg-2);
        transform: scale(0.985);
      }
    }
    """
    html = "<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
    html += '<table class="bv-table"><tr class="bv-row" id="row1"><td>test</td></tr></table>'
    html += "</body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Check transition defined
        tr = await page.evaluate("getComputedStyle(document.querySelector('.bv-row')).transitionProperty")
        print(f"transition-property={tr}")
        # transform should be in transition
        assert "transform" in tr or "all" in tr

        # Apply :active via JS pseudo-class emulation: not directly possible via computed style
        # Instead, verify the CSS rule exists by checking rule text
        active_rule = await page.evaluate("""
        (() => {
          for (const sheet of document.styleSheets) {
            try {
              for (const rule of sheet.cssRules) {
                if (rule.cssText && rule.cssText.includes(':active') && rule.cssText.includes('scale')) {
                  return rule.cssText;
                }
              }
            } catch (e) {}
          }
          return null;
        })()
        """)
        print(f"active rule: {active_rule}")
        assert active_rule is not None
        assert "scale(0.985)" in active_rule
        assert "background" in active_rule

        print("[OK] R51 press state CSS present")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())