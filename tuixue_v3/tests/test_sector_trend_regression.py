"""Regression tests for the all-sector trend tiles on the dashboard.

These tests lock the data contract between the sector snapshot and the
sparkline response, plus the two presentation details that are easy to lose:
metrics must not depend on a fallback chart, and day labels need room on
mobile.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tuixue_v3.web.server import _merge_sector_snapshot


ROOT = Path(__file__).resolve().parents[1]
VIEW_DASH = ROOT / "web" / "static" / "view-dash.js"
STYLE = ROOT / "web" / "static" / "style.css"


def test_sector_snapshot_metrics_override_chart_period_and_normalize_flow():
    """Today's snapshot wins over a 30/120-day chart-derived percentage."""
    merged = _merge_sector_snapshot(
        {
            "name": "半导体",
            "change_pct": 40.235,  # multi-day chart range, not today's move
            "ok": True,
            "klines": [{"date": "20260805", "close": 15871.224}],
        },
        {
            "name": "半导体",
            "change_pct": 1.25,
            "net_inflow": 123_000_000,  # Eastmoney source: yuan
            "rank_flow": 2,
            "rank_pct": 4,
            "rank_kind": "flow+pct",
        },
    )

    assert merged["change_pct"] == pytest.approx(1.25)
    assert merged["net_inflow_yi"] == pytest.approx(1.23)
    assert merged["rank_flow"] == 2
    assert merged["rank_pct"] == 4
    assert merged["rank_kind"] == "flow+pct"


def test_trend_tile_metrics_are_rendered_for_every_chart_result():
    """Treemap 色块 label 必须带涨幅/资金 (按色块尺寸分级, 不再是 sparkline + pulse_only 兜底)。"""
    source = VIEW_DASH.read_text(encoding="utf-8")

    assert "function _treemapLabel" in source
    assert "pct.toFixed(2)}%" in source  # 涨幅文案
    assert "亿" in source                # 资金流入文案
    assert "if (it.pulse_only)" not in source


def test_day_date_labels_are_normalized_and_have_mobile_height():
    """day 模式移动端高度 + treemap label 尺寸分级 (小色块不溢出) 必须保留。"""
    js = VIEW_DASH.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")

    assert "document.body.classList.toggle('day-mode-on'" in js
    assert "body.day-mode-on .trend-tile-chart { height: 64px !important; }" in css
    assert "function _treemapLabel" in js
    assert "_treemapSizes" in js        # 尺寸分级依据
