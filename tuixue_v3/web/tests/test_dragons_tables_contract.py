"""龙头页 两表维度 + 排序键契约回归

确保 index.html thead 与 view-other.js 排序键严格一致 — 改任一边都会被这个测试拦下。
"""
import re
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static/index.html").read_text()
JS = (ROOT / "static/view-other.js").read_text()


def _th_all(table_id: str) -> list[str]:
    """thead 里所有 <th> 的可读标签 — 锁对齐时用"""
    m = re.search(rf'<table id="{table_id}".*?</thead>', HTML, re.S)
    assert m, f"找不到 {table_id}"
    return re.findall(r'<th(?:\s[^>]*)?>(?:<span[^>]*>[^<]*</span>)?([^<]*)', m.group(0))


def _th_keys(table_id: str) -> list[str]:
    m = re.search(rf'<table id="{table_id}".*?</thead>', HTML, re.S)
    assert m, f"找不到 {table_id}"
    return re.findall(r'data-sort="([a-z_]+)"', m.group(0))


def _js_keys(name: str) -> list[str]:
    m = re.search(rf'var {name}\s*=\s*\{{(.*?)\}};', JS, re.S)
    assert m, f"找不到 {name}"
    return re.findall(r'^\s*([a-z_]+):\s*\w+\s*=>', m.group(0), re.M)


def test_today_table_contract():
    th = _th_keys("dragons-all-table")
    keys = _js_keys("_DRAGONS_SORT_KEYS")
    # 比较集合 (顺序无硬约束,因为 thead 顺序与 JS sort map 顺序可能不同;
    # 只要两边都覆盖且不漏即可)
    assert sorted(th) == sorted(keys), f"今日表 th 与排序键漂移: {th} vs {keys}"


def test_yesterday_table_contract():
    th = _th_keys("dragons-yesterday-table")
    keys = _js_keys("_YEST_SORT_KEYS")
    assert sorted(th) == sorted(keys), f"昨日表 th 与排序键漂移: {th} vs {keys}"


def test_two_tables_have_no_drift():
    """昨日表维度应覆盖今日表前 10 列(确保可拉齐)"""
    today = _th_keys("dragons-all-table")
    yest = _th_keys("dragons-yesterday-table")
    assert today == yest[:len(today)], (
        f"今日表前 {len(today)} 列与昨日表前 {len(today)} 列漂移:\n"
        f"今日={today}\n昨日前 N={yest[:len(today)]}"
    )


def test_yesterday_loading_row_colspan_matches():
    """loading 行 colspan 必须等于 th 数(14 — 2026-08-03 加 PE 列)"""
    m = re.search(
        r'<table id="dragons-yesterday-table".*?<tbody>(.*?)</tbody>',
        HTML, re.S)
    assert m
    colspan = int(re.search(r'colspan="(\d+)"', m.group(1)).group(1))
    th_count = len(_th_keys("dragons-yesterday-table"))
    assert colspan == th_count, f"loading colspan={colspan} ≠ th 数 {th_count}"


def test_renderDragons_default_breaks_columns():
    """renderDragons 中 yestEnriched 空态 colspan 应等于 14 (2026-08-03 加 PE)"""
    assert "'<tr><td colspan=\"14\" class=\"empty\">无昨日涨停数据</td></tr>'" in JS


def test_yesterday_row_td_order_matches_thead():
    """昨日表 row 模板里的 <td> 数必须 == thead <th> 数 — 防止 streak/concept 漂移这类内容跟表头对不上的 bug"""
    th_all = _th_all("dragons-yesterday-table")
    m = re.search(
        r"yBody\.innerHTML\s*=\s*sortedYest\.map\(.*?return\s+`<tr[^>]*>(.*?)</tr>`;",
        JS, re.S)
    assert m, "找不到昨日表 row 模板"
    row_html = m.group(1)
    td_count = row_html.count('<td>') + row_html.count('<td ')
    colspan_count = len(re.findall(r'<td[^>]*colspan', row_html))
    assert (td_count - colspan_count) == len(th_all), (
        f"昨日表 row 主行 td 数 ({td_count - colspan_count}) ≠ thead th 总数 ({len(th_all)}) — "
        f"内容与表头对不上\nrow 模板:\n{row_html[:400]}"
    )


def test_today_row_td_order_matches_thead():
    """今日表 row 主行 <td> 数 == thead <th> 数 (10 个可排 + 1 提示共 11)。
    今日表额外有个 ai-detail-row (colspan 11) 的展开行,不算主行 td,会过滤掉。"""
    th_all = _th_all("dragons-all-table")
    m = re.search(
        r"allBody\.innerHTML\s*=\s*sortedAll\.map\(.*?return\s+`<tr[^>]*>(.*?)</tr>`;",
        JS, re.S)
    assert m, "找不到今日表 row 模板"
    row_html = m.group(1)
    # 主行 td 数: 去掉 ai-detail-row 那段 (它不该在这里,但 regex 会一并捕到)
    main_td_count = row_html.count('<td>') + row_html.count('<td ')
    # 过滤掉 colspan td (只属于 ai-detail-row 那种 colspan="11")
    colspan_count = len(re.findall(r'<td[^>]*colspan', row_html))
    assert (main_td_count - colspan_count) == len(th_all), (
        f"今日表 row 主行 td 数 ({main_td_count - colspan_count}) ≠ thead th 总数 ({len(th_all)})"
    )


def test_yesterday_concept_after_streak_in_row():
    """防御回归:概念 td 必须出现在连板 td 之后 (the 新顺序是 streak/concept)"""
    m = re.search(
        r"yBody\.innerHTML\s*=\s*sortedYest\.map\(.*?return\s+`<tr[^>]*>(.*?)</tr>`;",
        JS, re.S)
    row_html = m.group(1)
    # 抓首个含 板 的 td (连板) 与首个含 escapeHtml(concept) 的 td 位置
    streak_pos = row_html.find("}板</b>")
    concept_pos = row_html.find("escapeHtml(concept)")
    assert streak_pos > 0 and concept_pos > 0
    assert streak_pos < concept_pos, (
        "昨日表 row 里 streak td 在 concept td 后面 — 概念/连板顺序颠倒"
    )