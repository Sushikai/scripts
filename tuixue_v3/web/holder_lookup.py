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


# R34: 股东类型画像 — 东财 HOLDER_TYPE / 股东名 → 专业分类
_TYPE_NAME_KW = [
    ("北向/外资",  "香港中央结算|汇丰银行|渣打银行|摩根大通|花旗银行"),
    # 公募基金/ETF — 名称常带托管银行前缀(如"中国工商银行股份有限公司-华泰柏瑞"),须先于"银行"命中
    ("公募基金",   "证券投资基金|交易型开放式|指数证券投资基金|混合型证券投资基金|股票型证券投资基金|联接基金"),
    ("QFII",       "MORGAN STANLEY|UBS|GOLDMAN|JPMORGAN|MERRILL|CITIGROUP|BARCLAYS|香港上海汇丰"),
    ("一般法人",   "投资控股|投资公司|企业管理|控股集团|集团有限|有限合伙|资产管理公司|银行股份|银行股份有限公司|通用机械|科技有限|实业|发展"),
]
_TYPE_ORDER = [
    ("公募基金",   "证券投资基金"),
    ("社保基金",   "社保"),
    ("险资",       "保险"),
    ("私募基金",   "私募"),
    ("QFII",       "QFII"),
    ("券商",       "券商"),
    ("信托",       "信托"),
    ("个人",       "个人"),
    ("一般法人",   "一般企业|法人|银行|集团|资管|投资"),
]


def _classify_type(t: str, name: str = "") -> str:
    """东财 HOLDER_TYPE + 股东名 → 专业分类标签。"""
    t = (t or "").strip()
    n = (name or "").strip()
    # 1) 股东名强特征 (香港中央结算 = 北向)
    for label, kw in _TYPE_NAME_KW:
        if any(k in n for k in kw.split("|")):
            return label
    # 2) HOLDER_TYPE 关键词
    for label, kw in _TYPE_ORDER:
        if any(k in t for k in kw.split("|")):
            return label
    # 3) 兜底: 名含"基金"且非"私募"
    if "基金" in n and "私募" not in n:
        return "公募基金"
    if "私募" in t or "私募" in n:
        return "私募基金"
    if t:
        return t
    return "其他"


def _parse_top10(sdltgd: list[dict] | None) -> tuple[list[dict], dict, float]:
    """
    十大流通股东 → (top10_holders, type_breakdown, inst_free_pct)。
    inst_free_pct = 机构类(北向/公募/社保/险资/私募/QFII/券商/信托/一般法人)合计占流通%。
    """
    top10: list[dict] = []
    if sdltgd:
        for r in sdltgd:
            name = (r.get("HOLDER_NAME") or "").strip()
            if not name:
                continue
            typ = _classify_type(r.get("HOLDER_TYPE"), name)
            hold_num = _safe_float(r.get("HOLD_NUM")) or 0
            chg = r.get("HOLD_NUM_CHANGE")
            if chg in ("新进", "不变", "退出"):
                change = chg
            else:
                cv = _safe_float(chg)
                change = "新进" if cv is None else ("增持" if cv > 0 else "减持")
            top10.append({
                "rank": int(r.get("HOLDER_RANK") or 0),
                "name": name,
                "type": typ,
                "type_raw": r.get("HOLDER_TYPE") or "",
                "shares_wan": round(hold_num / 1e4, 2),
                "pct_free": _safe_float(r.get("FREE_HOLDNUM_RATIO")),
                "change": change,
                "change_pct": _safe_float(r.get("CHANGE_RATIO")),
            })
    # 类型聚合
    breakdown: dict[str, dict] = {}
    inst_pct = 0.0
    inst_kinds = {"北向/外资", "公募基金", "社保基金", "险资", "私募基金", "QFII", "券商", "信托", "一般法人"}
    for h in top10:
        t = h["type"]
        b = breakdown.setdefault(t, {"count": 0, "pct": 0.0})
        b["count"] += 1
        p = h["pct_free"] or 0
        b["pct"] += p
        if t in inst_kinds:
            inst_pct += p
    # 排序: 按占比降序
    breakdown = dict(sorted(breakdown.items(), key=lambda kv: -kv[1]["pct"]))
    return top10, breakdown, round(inst_pct, 2)


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
    # R34: 十大流通股东详情 + 股东类型画像 (sdltgd / jgcc / jjcg)
    try:
        top10_holders, type_breakdown, inst_free_pct = _parse_top10(data.get("sdltgd"))
        latest["top10_holders"]  = top10_holders
        latest["type_breakdown"] = type_breakdown
        latest["inst_free_pct"]  = inst_free_pct
        latest["report_date"]    = (data.get("sdltgd") or [{}])[0].get("END_DATE", "")[:10] if data.get("sdltgd") else latest.get("report_date")
        # 机构持仓总数 (jgcc): TOTAL_ORG_NUM 单季; TOTAL_SHARES_RATIO = 机构合计占股本
        jg = data.get("jgcc") or []
        if jg:
            org_nums = [_safe_float(r.get("TOTAL_ORG_NUM")) for r in jg]
            latest["inst_org_num"] = int(max(org_nums) or 0) if org_nums else 0
        else:
            latest["inst_org_num"] = 0
        # 基金持仓数 (jjcg)
        jj = data.get("jjcg") or []
        latest["fund_count"] = len(jj) if jj else 0
    except Exception as e:
        log.warning(f"东财 holder {code} top10 解析失败: {e}")
    _cache[code] = (systime.time(), latest)
    return latest


if __name__ == "__main__":
    import sys
    print(json.dumps(fetch_holder_info(sys.argv[1] if len(sys.argv) > 1 else "603137"),
                     ensure_ascii=False, indent=2))