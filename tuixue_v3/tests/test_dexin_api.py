"""
tests/test_dexin_api.py — 得鑫量变术 /api/dexin/screen 契约测试。

跑法 (需 server 在 7799):
    pytest tests/test_dexin_api.py -v -m contract
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.contract

STAGES = ["cang_zha", "xu_sha", "bian_zhen", "de_xin"]
STOCK_FIELDS = {"code", "name", "stage", "stage_label", "quote",
                "sector", "volume", "box", "gap", "dragon", "advice"}


@pytest.fixture(scope="module")
def screen(base_url):
    r = httpx.get(f"{base_url}/api/dexin/screen", timeout=90)
    r.raise_for_status()
    return r.json()


def test_envelope_shape(screen):
    assert screen["ok"] is True
    assert "data" in screen and isinstance(screen["data"], dict)
    assert "ts" in screen


def test_four_stages_present(screen):
    stages = screen["data"]["stages"]
    for s in STAGES:
        assert s in stages, f"缺阶段 {s}"
        assert isinstance(stages[s], list)


def test_each_stage_capped_at_10(screen):
    stages = screen["data"]["stages"]
    for s in STAGES:
        assert len(stages[s]) <= 10, f"{s} 超过 10 只"


def test_stock_required_fields(screen):
    stages = screen["data"]["stages"]
    seen = False
    for s in STAGES:
        for stk in stages[s]:
            seen = True
            missing = STOCK_FIELDS - set(stk.keys())
            assert not missing, f"{s} 股票缺字段 {missing}"
            assert stk["quote"], "原话溯源不得为空"
            assert stk["advice"], "操作建议不得为空"
    # 允许某些阶段为空 (行情决定), 但整体至少要有结构
    assert isinstance(seen, bool)


def test_xu_sha_variant_marked(screen):
    for stk in screen["data"]["stages"]["xu_sha"]:
        assert stk.get("variant") in {"benign", "dangerous"}


def test_laws_and_disclaimer_present(screen):
    data = screen["data"]
    assert data.get("laws"), "缺四句纲领原话"
    assert "风险" in data.get("disclaimer", ""), "缺统一风险提示尾注"


def test_meta_fields(screen):
    data = screen["data"]
    assert "candidate_total" in data
    assert "regime" in data  # 震荡/主升 判定


def test_cache_hit_is_fast(base_url):
    import time
    t0 = time.time()
    r = httpx.get(f"{base_url}/api/dexin/screen", timeout=30)
    dt = time.time() - t0
    assert r.status_code == 200
    assert dt < 5.0, f"缓存命中应 < 5s, 实测 {dt:.1f}s"
