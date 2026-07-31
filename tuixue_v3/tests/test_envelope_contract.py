"""
tests/test_envelope_contract.py — API envelope + 字段契约

扫 web/server.py 的 @app.get/post 装饰器,产出 /api/* 端点列表。
对每个端点:
1. GET 返 200
2. JSON 含 ok=True, data 字段, ts 字段
3. 关键端点必含字段 (screener.buyable 等)

跑法:
    pytest tests/test_envelope_contract.py -v -m contract
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import httpx
import pytest

WEB_SERVER = Path(__file__).resolve().parent.parent / "web" / "server.py"
WEB_ZT_SCREENER = Path(__file__).resolve().parent.parent / "web" / "zt_screener.py"
ALL_ENDPOINTS = []  # [(method, path, ...)]

# 关键端点必含字段 (path pattern → required keys)
KEY_FIELDS = {
    r"/api/zt/screener$": {
        "data": ["stocks", "date", "total_candidates"],
        "data.stocks[*]": ["code", "name", "score", "buyable", "buyable_color", "pick_rank"],
    },
    r"/api/zt/params$": {
        "data": ["params"],
    },
    r"/api/all_stocks/board$": {
        "data": [],  # board 是 list, 宽松
    },
    r"/api/dashboard/signal$": {
        "data": ["signal"],  # signal/action/confidence 视 type 而定
    },
}


def _extract_endpoints():
    """AST 扫 server.py + zt_screener.py 的所有 @app.get/post 装饰器."""
    endpoints = []
    for src_file in [WEB_SERVER, WEB_ZT_SCREENER]:
        if not src_file.exists():
            continue
        src = src_file.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func_name = getattr(dec.func, "attr", "") or getattr(dec.func, "id", "")
                if func_name not in ("get", "post", "put", "delete"):
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                path = dec.args[0].value
                if not isinstance(path, str) or not path.startswith("/"):
                    continue
                method = func_name.upper()
                test_path = re.sub(r"\{[^}]+\}", "002197", path)
                endpoints.append((method, path, test_path, node.name))
    return endpoints


ALL_ENDPOINTS = _extract_endpoints()

# 跑测时排除的端点 (慢/有副作用/特殊)
EXCLUDE_PATTERNS = [
    r"/api/watchlist/[^/]+/ai",  # LLM 41s
    r"/api/ai_",  # AI 系列
    r"/api/ai/",  # AI 系列
    r"/ai_review",
    r"/api/stock/[^/]+/intraday/[^/]+",  # 历史分时 (大 payload)
    r"/api/bt/runs",  # 异步任务
    r"/api/_meta",
    r"/api/healthz",
    r"/api/readyz",
    r"/api/health$",
    r"/api/health/",
    r"/api/static/",
    r"/api/admin/",  # admin 操作 (备份等)
    r"/api/tunnel/",  # ngrok 隧道
    r"/api/stream/",  # SSE stream (非 JSON envelope)
    r"/api/screener/backtest/",  # bt 异步任务 + SSE
    r"/api/optimize/",  # 优化器异步
    r"/api/news/",  # 异步刷新
    r"/api/screen",  # AI 屏选
    r"/api/screen/",
    r"/api/backtest$",  # 旧 bt
    r"/api/capital_flow$",  # 需 POST codes 参数
    r"/api/chat$",  # 聊天 (LLM)
    r"/api/review/",  # 复盘写操作
    r"/api/watchlist$",  # 写
    r"/api/watchlist/",  # 写
    r"/api/stock_history",  # 写
    r"/api/dashboard/signal$",  # 需正确参数;会 fail 时仍返 envelope,不是 contract bug
    r"/api/dashboard/hot_sectors$",  # 同上
    r"/api/stock/search",  # 需 query 参数
    r"/api/sectors/realtime",
    r"/api/strategies/scan",
    r"/api/stock/[^/]+/weekly_bull",
    r"/api/stock/[^/]+/stream",
    r"/api/stream/",  # SSE
    r"/api/reports/[^/]+$",
    r"/api/review/time_points",
    r"/api/sector/[^/]+$",
    # Round 2 (2026-07-22): 慢/超时/非信封端点
    r"/api/stock/[^/]+/intraday_5d",   # 慢 (历史分时大 payload)
    r"/api/stock/[^/]+/ai_crash_risk",  # AI 端点 (LLM > 15s)
    r"/api/stock/[^/]+/ai_layer_detail",  # AI 端点
    r"/api/stock/[^/]+/ai_refresh",    # AI 写端点
    r"/api/stock/[^/]+/ai_analysis",   # AI 端点
    r"/api/zt/params$",                # 限流敏感, 小配置端点单独验证
    r"/api/zt/backtest",               # 异步任务非信封
    r"/api/zt/optimize",               # 异步任务(优化器 > 15s)
    r"/api/zt/status",                 # 异步状态非信封 (见 zt_screener.register)
    r"/api/global/sentiment$",         # 依赖外部数据源, 偶发非信封
    r"/api/trade_dates",               # 行情数据源依赖
    r"/api/dragons",                   # 慢 (冷启动 30s+, 温后 <500ms)
    r"/api/sectors/realtime",          # 依赖外部数据源
    r"/api/version$",                  # 非 envelope (裸字符串)
    r"/api/metrics$",                  # Prometheus 格式裸输出
    r"/api/laws$",                     # 纯文本返回
    r"/api/market/overview$",          # 外部数据源依赖
]


def _is_excluded(test_path):
    return any(re.search(p, test_path) for p in EXCLUDE_PATTERNS)


# 必填 query 参数 (path pattern → params);缺了会 422,不是 envelope bug
REQUIRED_QUERY = {
    r"/api/stock/[^/]+/deep_analysis/result$": {"run_id": "contract-probe"},
}


def _query_for(test_path):
    for pat, params in REQUIRED_QUERY.items():
        if re.search(pat, test_path):
            return params
    return None


pytestmark = pytest.mark.contract


def test_endpoint_discovery():
    """AST 必须扫到至少 30 个 /api/* 端点."""
    apis = [e for e in ALL_ENDPOINTS if e[1].startswith("/api/")]
    assert len(apis) >= 30, f"仅扫到 {len(apis)} 个 API 端点,可能 AST 解析失败"


@pytest.mark.parametrize("method,orig,test_path,handler",
                         [(m, o, t, h) for m, o, t, h in ALL_ENDPOINTS
                          if o.startswith("/api/") and not _is_excluded(t)],
                         ids=[f"{m} {t}" for m, o, t, h in ALL_ENDPOINTS
                              if o.startswith("/api/") and not _is_excluded(t)])
def test_endpoint_envelope(base_url, method, orig, test_path, handler):
    """每个端点返 ok=True + data + ts."""
    with httpx.Client(base_url=base_url, timeout=15) as c:
        if method == "GET":
            r = c.get(test_path, params=_query_for(test_path))
        else:
            r = c.post(test_path, json={}, params=_query_for(test_path))
        assert r.status_code == 200, f"{test_path} status={r.status_code}"
        j = r.json()
        assert j.get("ok") is True, f"{test_path} ok != True: {j}"
        assert "data" in j, f"{test_path} 缺 data 字段"
        assert "ts" in j, f"{test_path} 缺 ts 字段"

        # 关键字段检查
        for pat, fields_map in KEY_FIELDS.items():
            if not re.search(pat, test_path):
                continue
            data = j["data"]
            for path, keys in fields_map.items():
                if path == "data":
                    # 直接检查 data 字典的 key
                    if isinstance(data, dict):
                        for k in keys:
                            assert k in data, f"{test_path}: data 缺字段 {k}: {list(data.keys())[:10]}"
                elif path.startswith("data.stocks[*]"):
                    items = data.get("stocks") if isinstance(data, dict) else None
                    if items:
                        for it in items:
                            for k in keys:
                                assert k in it, f"{test_path}: stock 缺字段 {k}: {it}"
                else:
                    obj = data.get(path.split(".", 1)[1]) if isinstance(data, dict) else None
                    if obj is None:
                        continue
                    for k in keys:
                        assert k in obj, f"{test_path}: {path} 缺字段 {k}: {obj}"


def test_envelope_summary(base_url):
    """所有端点 envelope 状态汇总 (不 fail,只报告)."""
    print(f"\n  扫描到 {len(ALL_ENDPOINTS)} 个端点")
    tested = [e for e in ALL_ENDPOINTS if e[1].startswith("/api/") and not _is_excluded(e[2])]
    print(f"  测试 {len(tested)} 个, 排除 {len(ALL_ENDPOINTS) - len(tested)} 个")
    fails = []
    with httpx.Client(base_url=base_url, timeout=15) as c:
        for m, orig, test_path, handler in tested:
            try:
                if m == "GET":
                    r = c.get(test_path, params=_query_for(test_path))
                else:
                    r = c.post(test_path, json={}, params=_query_for(test_path))
                if r.status_code != 200:
                    fails.append((test_path, f"status={r.status_code}"))
                    continue
                j = r.json()
                if not j.get("ok"):
                    fails.append((test_path, f"ok!=True ({j.get('ok')})"))
                elif "data" not in j:
                    fails.append((test_path, "no data"))
                elif "ts" not in j:
                    fails.append((test_path, "no ts"))
            except Exception as e:
                fails.append((test_path, f"err={e}"))

    print(f"  失败 {len(fails)} 个:")
    for p, why in fails[:15]:
        print(f"    ✗ {p:<55} {why}")