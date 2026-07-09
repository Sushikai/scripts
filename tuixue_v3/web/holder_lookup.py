"""
散户/主力持股占比 - 数据源:
1) Eastmoney web f10: emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax
   → 股东户数 / 户均持股 / 持仓集中度 (前十大流通股东)
2) 派生: 散户比例 ≈ 1 - 机构 + 大股东 持仓比例

输出:
  holder_total:   股东户数 (季末)
  avg_shares:     户均持股 (股)
  change_pct:     环比变化 (%)
  focus_label:    集中度标签 ("非常集中" / "集中" / "一般" / "分散")
  top10_pct:      前十大流通股东合计持股比例 (%)
  retail_proxy_pct: 散户估算占比 (%)
  main_proxy_pct:  主力(前十大+大户)估算占比 (%)
  history:        最近 4 季序列

TTL: 24h (季报更新慢,缓存可长)
"""
from __future__ import annotations
import json
import logging
import time as systime
from typing import Any

import requests

log = logging.getLogger("tuixue_v3.web.holders")

_TTL = 24 * 3600
_cache: dict[str, tuple[float, dict]] = {}
_TIMEOUT = 8


def _to_emcode(code: str) -> str:
    """A股代码 → 东财 SH603137 / SZ000001 形式。"""
    code = code.strip().zfill(6)
    if code.startswith(("60", "68", "9", "5")):
        return f"SH{code}"
    return f"SZ{code}"


def _safe_float(v: Any, default=None) -> float | None:
    try:
        if v is None or v == "" or v == "-":
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def _parse_row(row: dict) -> dict:
    """单季记录 → 标准化字段(无 history 字段,避免递归)。"""
    return {
        "report_date": row.get("END_DATE", "")[:10] if row.get("END_DATE") else "",
        "holder_total":   int(row.get("HOLDER_TOTAL_NUM") or 0) or None,
        "change_pct":     _safe_float(row.get("TOTAL_NUM_RATIO")),
        "avg_shares":     _safe_float(row.get("AVG_FREE_SHARES")),
        "focus_label":    row.get("HOLD_FOCUS") or "",
        "avg_hold_amt":   _safe_float(row.get("AVG_HOLD_AMT")),
        "top10_pct":      _safe_float(row.get("HOLD_RATIO_TOTAL")),
        "top10_free_pct": _safe_float(row.get("FREEHOLD_RATIO_TOTAL")),
        "retail_proxy_pct": None,  # 仅 latest 计算
        "main_proxy_pct":  None,
    }


def fetch_holder_info(code: str) -> dict | None:
    """返回最新一期 + 近 4 季历史。失败 None。"""
    code = code.strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return None
    cached = _cache.get(code)
    if cached and (systime.time() - cached[0]) < _TTL:
        return cached[1]
    emcode = _to_emcode(code)
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax"
    try:
        r = requests.get(url, params={"code": emcode}, timeout=_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://emweb.securities.eastmoney.com/"})
    except Exception as e:
        log.warning(f"东财 holder {code} 网络失败: {e}")
        return None
    if r.status_code != 200:
        log.warning(f"东财 holder {code} HTTP {r.status_code}")
        return None
    try:
        data = r.json()
    except Exception:
        log.warning(f"东财 holder {code} JSON 解析失败")
        return None
    rows = data.get("gdrs") or []
    if not rows:
        return None
    parsed: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        parsed.append(_parse_row(r))
    if not parsed:
        return None
    latest = dict(parsed[0])  # copy,避免污染 parsed[0]
    # 散户/主力估算:
    #   top10_pct = 前十大流通股东持股比例
    #   散户 ≈ 1 - top10 - 主力估算(超大单/大单 proxy)
    #   注: 散户比例无直接公开数据,这里用"非前十大流通股东"做粗算
    top10 = latest.get("top10_pct") or 0
    latest["retail_proxy_pct"] = round(max(0, 100 - top10 - 25), 2)  # 25% 估算主力
    latest["main_proxy_pct"]  = round(top10 + 25, 2)
    latest["history"]         = parsed[:4]
    _cache[code] = (systime.time(), latest)
    return latest


if __name__ == "__main__":
    import sys
    print(json.dumps(fetch_holder_info(sys.argv[1] if len(sys.argv) > 1 else "603137"),
                     ensure_ascii=False, indent=2))