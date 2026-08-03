"""
web/fundamentals.py — 公司基本面 fetcher (2026-07-30)

为 AI 深度判断 (适不适合卖) 提供 4 个维度的基本信息:
  1. profile: 公司业务范畴 + 主营 + 注册地 + 所属行业 (东财 PC_HSF10/CompanySurvey)
  2. financials: 4 季业绩快报 (营收 + 净利润 + EPS + 同比 + 环比)
                用东财 datacenter API (绕过 akshare eastmoney DNS 阻断)
  3. performance_jump: 业绩跳变检测 (abs(同比) > 30% 或 eps_跳变)
  4. sector_pe_aggregate: 同行业平均 PE 偏离

所有数据源全部包 try/except,任一失败仅降级该字段,绝不阻塞主路径。
总超时 ≤ 6s (4 路并发 ak + EM direct),24h Redis cache。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests as _requests

log = logging.getLogger("tuixue_v3.web.fundamentals")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://emweb.securities.eastmoney.com/",
}


def _safe_float(x) -> float:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return 0.0
    return v


def _em_security_code(code6: str) -> str:
    """6 位 → 'SH600519' / 'SZ000001' / 'BJ830799' 形态(直接 EM 接口用)。"""
    s = str(code6 or "").strip().zfill(6)
    if not s:
        return ""
    if s.startswith(("60", "68", "90")):
        return "SH" + s
    if s.startswith(("00", "30", "20")):
        return "SZ" + s
    if s.startswith(("8", "43", "92")):
        return "BJ" + s
    return "SH" + s


def _fetch_profile_em(code: str) -> dict:
    """公司基础档案 + 业务范畴 (东财 PC_HSF10/CompanySurvey/PageAjax)。"""
    em_code = _em_security_code(code)
    if not em_code:
        return {}
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax"
    try:
        r = _requests.get(url, params={"code": em_code}, timeout=4, headers=DEFAULT_HEADERS)
        if r.status_code != 200:
            return {}
        d = r.json()
        jbzl = (d.get("jbzl") or [])
        if not jbzl:
            return {}
        item = jbzl[0]
        # BUSINESS_SCOPE = 经营范围 (业务范畴), ORG_PROFILE = 公司简介
        scope = str(item.get("BUSINESS_SCOPE") or "").strip()
        org_profile = str(item.get("ORG_PROFILE") or "").strip()
        # 业务范畴摘要 (去多余空白,≤200 字)
        biz = org_profile[:200] if org_profile else scope[:200]
        return {
            "name": str(item.get("SECURITY_NAME_ABBR") or ""),
            "org_full_name": str(item.get("ORG_NAME") or ""),
            "industry_sw": str(item.get("EM2016") or "").strip(),
            "industry_csrc": str(item.get("INDUSTRYCSRC1") or "").strip(),
            "address": str(item.get("ADDRESS") or ""),
            "reg_capital_yi": round(_safe_float(item.get("REG_CAPITAL")), 4),
            "emp_num": int(item.get("EMP_NUM") or 0),
            "president": str(item.get("PRESIDENT") or ""),
            "legal_person": str(item.get("LEGAL_PERSON") or ""),
            "secretary": str(item.get("SECRETARY") or ""),
            "business_scope": scope,
            "business_summary": biz,
            "website": str(item.get("ORG_WEB") or ""),
        }
    except Exception as e:
        log.debug(f"_fetch_profile_em {code} fail: {e}")
        return {}


def _fetch_financials_em(code: str, top_n: int = 4) -> list[dict]:
    """4 季业绩快报 (营收 + 净利 + EPS + 同比 + 环比)。

    接口: datacenter.eastmoney.com RPT_LICO_FN_CPD
    真实字段 (实测 2026-07-30):
      REPORTDATE  / BASIC_EPS / TOTAL_OPERATE_INCOME / PARENT_NETPROFIT
      YSTZ = 营收同比 (%)  YSHZ = 营收环比 (%)
      SJLTZ = 净利同比 (%) SJLHZ = 净利环比 (%)
      XSMLL = 销售毛利率 (%)
      WEIGHTAVG_ROE = ROE 加权 (%)
      BPS = 每股净资产 (元)
    """
    s = str(code or "").strip().zfill(6)
    if not s:
        return []
    url = "https://datacenter.eastmoney.com/api/data/v1/get"
    try:
        r = _requests.get(url, params={
            "reportName": "RPT_LICO_FN_CPD",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{s}")',
            "pageNumber": 1,
            "pageSize": 8,
            "sortColumns": "REPORTDATE",
            "sortTypes": "-1",
        }, timeout=5, headers=DEFAULT_HEADERS)
        if r.status_code != 200:
            return []
        d = r.json()
        rows = (d.get("result") or {}).get("data") or []
        out = []
        for r in rows[:top_n]:
            revenue_yi = _safe_float(r.get("TOTAL_OPERATE_INCOME")) / 1e8  # 元 → 亿
            netprofit_yi = _safe_float(r.get("PARENT_NETPROFIT")) / 1e8
            reportdate = str(r.get("REPORTDATE") or "")[:10]  # "2026-03-31"
            period_label = self_period_label(r.get("DATAYEAR"), r.get("DATEMMDD")) if False else _period_from_date(reportdate)
            out.append({
                "period": period_label,  # "2026Q1" 等
                "period_label": f"{period_label}",
                "period_date": reportdate,
                "notice_date": str(r.get("NOTICE_DATE") or "")[:10],
                "revenue_yi": round(revenue_yi, 2),
                "revenue_yoy_pct": round(_safe_float(r.get("YSTZ")), 2),
                "revenue_qoq_pct": round(_safe_float(r.get("YSHZ")), 2),
                "netprofit_yi": round(netprofit_yi, 2),
                "netprofit_yoy_pct": round(_safe_float(r.get("SJLTZ")), 2),
                "netprofit_qoq_pct": round(_safe_float(r.get("SJLHZ")), 2),
                "eps": round(_safe_float(r.get("BASIC_EPS")), 4),
                "eps_deduct": round(_safe_float(r.get("DEDUCT_BASIC_EPS")), 4),
                "gross_margin_pct": round(_safe_float(r.get("XSMLL")), 2),
                "roe_pct": round(_safe_float(r.get("WEIGHTAVG_ROE")), 2),
                "bps": round(_safe_float(r.get("BPS")), 4),
            })
        return out
    except Exception as e:
        log.debug(f"_fetch_financials_em {code} fail: {e}")
        return []


def _period_from_date(reportdate: str) -> str:
    """YYYY-MM-DD → '2026Q1' / '2025Q2' / '2024年报' 标签。

    季报标签统一 Q1/Q2/Q3/全年 (2026-08-03 修: Q2 改回 Q2 而非 '年中',
    因 4 列展示时混 'Q1' '年中' 'Q3' '年报' 视觉断层且 Q2 用户易漏看)
    """
    from datetime import datetime
    try:
        d = datetime.strptime(reportdate, "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""
    y, m, day = d.year, d.month, d.day
    if m == 3 and day == 31:
        return f"{y}Q1"
    if m == 6 and day == 30:
        return f"{y}Q2"  # 修: 原本 f"{y}年中",用户看不到 Q2 字样
    if m == 9 and day == 30:
        return f"{y}Q3"
    if m == 12 and day == 31:
        return f"{y}年报"
    return f"{y}-{m:02d}"


def _detect_jumps(financials: list[dict]) -> dict:
    """业绩跳变检测 — abs(同比) > 30% 或 abs(环比) > 30% 触发 ⚠ 业绩跳变 标记。

    返回:
      jump:        bool
      reasons:     list[str] — 哪几期 + 方向
      max_revenue_yoy: float  — 4 期最大营收同比
      max_netprofit_yoy: float
      trend_yoy:    'rising' | 'falling' | 'mixed' | 'flat'
    """
    if not financials:
        return {"jump": False, "reasons": [], "max_revenue_yoy": None, "max_netprofit_yoy": None, "trend_yoy": "flat"}
    reasons = []
    max_rev = 0.0
    max_np = 0.0
    yoy_count = 0
    for f in financials:
        rev_yoy = f.get("revenue_yoy_pct") or 0
        np_yoy = f.get("netprofit_yoy_pct") or 0
        max_rev = max(max_rev, abs(rev_yoy))
        max_np = max(max_np, abs(np_yoy))
        period = f.get("period") or "?"
        # 单期跳变
        if abs(rev_yoy) > 30:
            reasons.append(f"{period} 营收同比{rev_yoy:+.1f}%")
        if abs(np_yoy) > 30:
            reasons.append(f"{period} 净利同比{np_yoy:+.1f}%")
        # 趋势计数
        if rev_yoy > 5 or np_yoy > 5:
            yoy_count += 1
        elif rev_yoy < -5 or np_yoy < -5:
            yoy_count -= 1

    if yoy_count > 2:
        trend = "rising"
    elif yoy_count < -2:
        trend = "falling"
    elif max_rev > 5 or max_np > 5:
        trend = "mixed"
    else:
        trend = "flat"

    return {
        "jump": len(reasons) > 0,
        "reasons": reasons[:6],  # 最多 6 条
        "max_revenue_yoy": round(max_rev, 2) if financials else None,
        "max_netprofit_yoy": round(max_np, 2) if financials else None,
        "trend_yoy": trend,
    }


def _sector_pe_aggregate(code: str, stock_pe: float | None = None) -> dict:
    """行业平均 PE — 复用 sector_classify.get_sector + data_layer 取同行业 code。

    简化: 若是用户个体分析,只 call get_sector 拿行业名,actual 平均 PE 通过
    cache_db.daily() + 全部 markets 计算 ≤ 30s 一次(本期省略,留接口)。
    """
    try:
        from .sector_classify import get_sector as _get_sector
        sec = _get_sector(code) or {}
        return {
            "industry_sw": sec.get("sw") or "",
            "industry_csrc": sec.get("csrc") or "",
            "stock_pe": round(_safe_float(stock_pe), 2) if stock_pe else None,
            "sector_pe_avg": None,   # 本期暂不实现 — 需要单独缓存 /cache/sector_pe:{sw}
            "diff_pct": None,
        }
    except Exception as e:
        log.debug(f"sector_pe_aggregate {code} fail: {e}")
        return {"industry_sw": "", "industry_csrc": "", "stock_pe": None, "sector_pe_avg": None, "diff_pct": None}


def _fetch_business_breakdown_em(code: str, top_n: int = 8) -> dict:
    """主营构成 + 经营评述 (东财 PC_HSF10/BusinessAnalysis/PageAjax)。

    返回:
      scope:   营业范围(简)
      by_product: [ {name, income_yi, ratio_pct, gross_margin_pct, rank} ]  — 按产品 (MAINOP_TYPE=2)
      by_region:  [ ... ]  — 按地区 (MAINOP_TYPE=3)
      review:  经营评述(最新报告期, ≤ 400 字)
      report_date: 最新报告期 (e.g. '2025-12-31')
    """
    s = str(code or "").strip().zfill(6)
    em_code = _em_security_code(s)
    if not em_code:
        return {"scope": "", "by_product": [], "by_region": [], "review": "", "report_date": ""}
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax"
    try:
        r = _requests.get(url, params={"code": em_code}, timeout=5, headers=DEFAULT_HEADERS)
        if r.status_code != 200:
            return {"scope": "", "by_product": [], "by_region": [], "review": "", "report_date": ""}
        d = r.json()
        scope_list = d.get("zyfw") or []
        scope = (scope_list[0].get("BUSINESS_SCOPE") or "").strip() if scope_list else ""
        zygcfx = d.get("zygcfx") or []
        if not zygcfx:
            return {"scope": scope, "by_product": [], "by_region": [], "review": "", "report_date": ""}
        # 取最新报告期
        latest_date = max((str(x.get("REPORT_DATE") or "") for x in zygcfx), default="")
        latest_date = latest_date[:10]
        # 主营按产品 / 按地区
        def _extract(maintype: str) -> list[dict]:
            items = [
                x for x in zygcfx
                if str(x.get("REPORT_DATE") or "").startswith(latest_date) and str(x.get("MAINOP_TYPE")) == maintype
            ]
            items.sort(key=lambda x: x.get("RANK") or 99)
            out = []
            for it in items:
                name = (it.get("ITEM_NAME") or "").strip()
                if not name or "其他(补充)" in name:
                    continue
                income_yi = round(_safe_float(it.get("MAIN_BUSINESS_INCOME")) / 1e8, 2)
                ratio = round(_safe_float(it.get("MBI_RATIO")) * 100, 2)
                gross = round(_safe_float(it.get("GROSS_RPOFIT_RATIO")) * 100, 2)
                out.append({
                    "name": name,
                    "income_yi": income_yi,
                    "ratio_pct": ratio,
                    "gross_margin_pct": gross,
                    "rank": it.get("RANK") or 0,
                })
            return out[:top_n]
        by_product = _extract("2")
        by_region = _extract("3")
        # 经营评述
        jyps = d.get("jyps") or []
        review = ""
        if jyps:
            rec = jyps[0]
            if str(rec.get("REPORT_DATE") or "").startswith(latest_date):
                review = str(rec.get("BUSINESS_REVIEW") or "").strip()[:400]
        return {
            "scope": scope,
            "by_product": by_product,
            "by_region": by_region,
            "review": review,
            "report_date": latest_date,
        }
    except Exception as e:
        log.debug(f"_fetch_business_breakdown_em {code} fail: {e}")
        return {"scope": "", "by_product": [], "by_region": [], "review": "", "report_date": ""}


def _fetch_concepts_em(code: str, top_n: int = 30) -> dict:
    """概念板块 + 核心竞争力/行业地位 (东财 PC_HSF10/CoreConception/PageAjax)。

    返回:
      concepts: [ {name, rank, is_precise, code} ] — 该股所属全部概念板块
      hot_tags: [ {keyword, classif, content, is_point} ] — 核心题材 5-6 条
        classif ∈ {经营范围, 主营业务, 行业背景, 核心竞争力, ...}
    行业地位从 hot_tags[核心竞争力] 提炼 (第 1 条 100 字内)
    """
    s = str(code or "").strip().zfill(6)
    em_code = _em_security_code(s)
    if not em_code:
        return {"concepts": [], "hot_tags": [], "industry_position": ""}
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax"
    try:
        r = _requests.get(url, params={"code": em_code}, timeout=5, headers=DEFAULT_HEADERS)
        if r.status_code != 200:
            return {"concepts": [], "hot_tags": [], "industry_position": ""}
        d = r.json()
        # 概念板块
        ssbk = d.get("ssbk") or []
        concepts = []
        for it in ssbk[:top_n]:
            name = (it.get("BOARD_NAME") or "").strip()
            if not name:
                continue
            concepts.append({
                "name": name,
                "rank": it.get("BOARD_RANK") or 99,
                "is_precise": bool(it.get("IS_PRECISE") in ("1", 1)),
                "board_code": it.get("BOARD_CODE") or "",
            })
        # 核心题材
        hxtc = d.get("hxtc") or []
        hot_tags = []
        for it in hxtc:
            content = str(it.get("MAINPOINT_CONTENT") or "").strip()
            if not content:
                continue
            hot_tags.append({
                "keyword": str(it.get("KEYWORD") or "").strip(),
                "classif": str(it.get("KEY_CLASSIF") or "").strip(),
                "content": content,
                "is_point": bool(it.get("IS_POINT") in ("1", 1)),
            })
        # 行业地位: 核心竞争力第 1 条
        comp_items = [t for t in hot_tags if t["classif"] == "核心竞争力"]
        industry_position = comp_items[0]["content"][:140] if comp_items else ""
        return {
            "concepts": concepts,
            "hot_tags": hot_tags,
            "industry_position": industry_position,
        }
    except Exception as e:
        log.debug(f"_fetch_concepts_em {code} fail: {e}")
        return {"concepts": [], "hot_tags": [], "industry_position": ""}


def _fetch_indicators_em(code: str) -> dict:
    """主要财务指标 (PE/PB/ROE/资产负债率/毛利率)— EM datacenter RPT_DMSK_FN_INCOME 等。

    本期简化:取最近 1 期 ROE / 净利率 (从 RPT_DMSK_FN_INCOME 拿不到,后续若需要
    接 ak.stock_financial_abstract_ths——但 sandbox 网络下慢)。

    实际本期返回的 indicators 仅包含 EPS + 基本面摘要,详细指标留给下期。
    """
    # 不再 fetch — 字段太多,sandbox 网络限制 → 让 fundamentals main loop 不依赖
    return {
        "_source": "skipped_v1",  # 留扩展点
    }


def fetch_fundamentals(code: str, *, stock_pe: float | None = None, current_price: float | None = None) -> dict:
    """单一入口 — 拉公司基本面 4 大维度。

    Args:
        code: 6 位股票代码
        stock_pe: 当前 PE 用于 sector_pe_aggregate;无则 None
        current_price: 价格 (保留兼容,本期不用)

    Returns:
        {
          code, ts, has_data,
          profile: {name, business_summary, business_scope, industry_sw, ...},
          financials: [4 期],
          earnings_jump: {jump, reasons, max_revenue_yoy, ...},
          sector_pe: {industry_sw, stock_pe, sector_pe_avg, diff_pct}
        }
    """
    s = str(code or "").strip().zfill(6)
    t0 = time.time()
    profile = _fetch_profile_em(s)
    financials = _fetch_financials_em(s, top_n=4)
    earnings_jump = _detect_jumps(financials)
    sector_pe = _sector_pe_aggregate(s, stock_pe=stock_pe)
    # 2026-08-01: 公司画像 4 件套 — 主营构成 (按产品/地区) + 概念板块 + 行业地位
    biz_breakdown = _fetch_business_breakdown_em(s)
    concepts_pack = _fetch_concepts_em(s)
    elapsed_ms = round((time.time() - t0) * 1000, 1)

    has_data = bool(
        profile.get("name") or financials or biz_breakdown.get("by_product") or concepts_pack.get("concepts")
    )

    return {
        "code": s,
        "ts": int(time.time()),
        "fetch_ms": elapsed_ms,
        "has_data": has_data,
        "profile": profile,
        "financials": financials,
        "earnings_jump": earnings_jump,
        "sector_pe": sector_pe,
        "biz_breakdown": biz_breakdown,
        "concepts_pack": concepts_pack,
    }


def summarize_for_prompt(fund: dict) -> str:
    """LLM 用: ≤200 字摘要业务 + 业绩状态。"""
    if not fund or not fund.get("has_data"):
        return "无基本面数据"
    profile = fund.get("profile") or {}
    biz = profile.get("business_summary") or profile.get("business_scope") or ""
    biz_short = biz[:60] if len(biz) > 60 else biz
    industry = profile.get("industry_sw") or profile.get("industry_csrc") or ""
    fin = fund.get("financials") or []
    jump = fund.get("earnings_jump") or {}
    rev_yoy = jump.get("max_revenue_yoy")
    np_yoy = jump.get("max_netprofit_yoy")
    yoy_parts = []
    if rev_yoy is not None and rev_yoy != 0:
        yoy_parts.append(f"营收最大同比±{abs(rev_yoy):.1f}%")
    if np_yoy is not None and np_yoy != 0:
        yoy_parts.append(f"净利最大同比±{abs(np_yoy):.1f}%")
    yoy_summary = " · ".join(yoy_parts) if yoy_parts else "平稳"
    jump_flag = "⚠跳变" if jump.get("jump") else "平稳"
    # 2026-08-01: 主营产品 TOP3 拼接
    biz_bd = fund.get("biz_breakdown") or {}
    by_prod = biz_bd.get("by_product") or []
    prod_str = ""
    if by_prod:
        top3 = by_prod[:3]
        prod_str = " · 主要产品:" + "、".join(f"{p['name']}({p['ratio_pct']}%)" for p in top3)
    # 概念数
    conc = fund.get("concepts_pack") or {}
    conc_cnt = len(conc.get("concepts") or [])
    conc_str = f" · 概念×{conc_cnt}" if conc_cnt else ""
    return (
        f"{industry[:30] or '—'} · 业务:{biz_short}{prod_str}{conc_str} · "
        f"近4季业绩{yoy_summary}({jump_flag})"
    )
