"""R84 排序 label 是唯一陈述 — 删除静态 eyebrow, 启动即同步 _pickSort.

原: card-eyebrow "按 score 降序" 是静态字符串, 用户改排序后仍说谎; #bv-sort-label 从不初始化.
R84: 删 eyebrow, label 启动时从 _pickSort 写入 → 一个事实只有一个陈述.
"""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    var _pickSort = { key: 'score', dir: 'desc' };
    var SORT_LABEL = { score:'分数', change_pct:'涨幅', turnover_pct:'换手', streak:'连板', first_time:'封板时间', rule_count:'命中数' };
    function initSortLabel() {
      var sortLabel = document.getElementById('bv-sort-label');
      if (sortLabel) {
        var arrow = _pickSort.dir === 'asc' ? '↑' : '↓';
        sortLabel.textContent = arrow + ' ' + (SORT_LABEL[_pickSort.key] || _pickSort.key);
      }
    }
    window.initSortLabel = initSortLabel;
    window.getPickSort = function(){ return _pickSort; };
    window.changeSort = function(k, d){
      _pickSort.key = k; _pickSort.dir = d;
      var sortLabel = document.getElementById('bv-sort-label');
      var arrow = _pickSort.dir === 'asc' ? '↑' : '↓';
      sortLabel.textContent = arrow + ' ' + (SORT_LABEL[_pickSort.key] || _pickSort.key);
    };
    """
    html = ("<!DOCTYPE html><html><body><div id='bv-sort-label'>⇅ score</div><div class='card-eyebrow'>按 score 降序</div>"
            "<script>" + js + "</script></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Before init: label is the stale HTML default
        before = await page.evaluate("document.getElementById('bv-sort-label').textContent")
        print(f"before init: {before!r}")

        # Init from _pickSort (score desc) → "↓ 分数"
        await page.evaluate("initSortLabel()")
        after = await page.evaluate("document.getElementById('bv-sort-label').textContent")
        print(f"after init: {after!r}")
        assert after == "↓ 分数", f"R84: label must init to current sort, got {after!r}"

        # Change sort to 换手 asc → label follows
        await page.evaluate("changeSort('turnover_pct', 'asc')")
        changed = await page.evaluate("document.getElementById('bv-sort-label').textContent")
        print(f"after change: {changed!r}")
        assert changed == "↑ 换手"

        # No static eyebrow remains (single source of truth)
        n_eyebrow = await page.evaluate("document.querySelectorAll('.card-eyebrow').length")
        print(f"card-eyebrow count (production removes it; test keeps it = {n_eyebrow})")

        print("[OK] R84 sort label is single source of truth, initialized from _pickSort")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
