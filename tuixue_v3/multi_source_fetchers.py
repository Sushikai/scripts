#!/usr/bin/env python3
"""
multi_source_fetchers.py
共用数据接口糅合层（多源逃生 + 本地缓存）。

为 stock 目录下各脚本（emotion / fund_flow_chart / dragon_scanner /
dragon_backtest / position_monitor 等）提供统一的兜底拉取函数，
避免单点依赖 akshare / 东方财富 / 同花顺 任一接口断连时整体挂死。

约定：
  - 所有 fetch_* 函数优先本地缓存（7 天），再尝试主源，再 1-2 个备源，最后 None / []
  - 主源优先选稳定官方 / 接口稳的；备源选 akshare
  - 不抛异常给上层；调用方只需 if df is None / if not rows
  - 内置 _http_get 自带指数退避 + gzip 解压
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

try:
    import requests
except ImportError:
    requests = None  # 调用方需自检

try:
    import pandas as pd
except ImportError:
    pd = None

LOG = logging.getLogger("multi_source")
if not LOG.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ═══════════════════════════════════════════════════════
# 通用：HTTP GET + 缓存路径
# ═══════════════════════════════════════════════════════
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://quote.eastmoney.com/",
}

CACHE_DIR = Path.home() / ".hermes" / "cache" / "multi_source"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL = timedelta(days=7)


def _http_get(url: str, params: dict | None = None, retries: int = 2,
              timeout: int = 6, sleep_base: float = 1.0,
              max_sleep: float = 6.0) -> Any:
    """带指数退避 + gzip 解压的 HTTP GET。失败返 None。
    2026-07-11: 默认 retries 3→2,timeout 10→6,sleep 收紧,
    避免单源挂 30s+ 拖死端点。已显式传参的调用方不受影响。"""
    if requests is None:
        return None
    last_err = ""
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            content = r.content
            if r.headers.get("Content-Encoding") == "gzip" or content[:2] == b"\x1f\x8b":
                try:
                    content = gzip.decompress(content)
                except Exception:
                    pass
            return json.loads(content.decode("utf-8", errors="ignore"))
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
            if i < retries - 1:
                wait = min(sleep_base * (2 ** i), max_sleep)
                time.sleep(wait)
    LOG.debug("HTTP GET %s 失败: %s", url, last_err)
    return None


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.json"


def _load_json_cache(name: str, ttl: timedelta = CACHE_TTL) -> Any:
    p = _cache_path(name)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        ts = d.get("ts")
        if ts and datetime.now() - datetime.fromisoformat(ts) < ttl:
            return d.get("data")
    except Exception:
        return None
    return None


def _save_json_cache(name: str, data: Any) -> None:
    try:
        _cache_path(name).write_text(json.dumps(
            {"ts": datetime.now().isoformat(), "data": data},
            ensure_ascii=False, default=str))
    except Exception as e:
        LOG.debug("cache save %s 失败: %s", name, e)


# ═══════════════════════════════════════════════════════
# 1. 交易日历：YYYY-MM-DD 集合
# ═══════════════════════════════════════════════════════
def fetch_trade_dates(force_refresh: bool = False) -> set[str] | None:
    """
    A股交易日历。
    主源：akshare.tool_trade_date_hist_sina
    备源：东方财富 datacenter 交易日历（mrsj）
    兜底：陈旧缓存 / 工作日近似（None 让调用方自己用 weekday）
    """
    cache_name = "trade_dates"
    if not force_refresh:
        cached = _load_json_cache(cache_name)
        if cached:
            return set(cached)

    # 主源：akshare
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty:
            norm = set()
            for d in df["trade_date"].tolist():
                s = str(d)
                if len(s) == 8 and s.isdigit():
                    s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
                norm.add(s[:10])
            if norm:
                _save_json_cache(cache_name, sorted(norm))
                return norm
    except Exception as e:
        LOG.warning("akshare 交易日历失败: %s", e)

    # 备源：东财 datacenter
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_TRADE_DATE_CAL",
            "columns": "TRADE_DATE,TRADE_DATE_INT",
            "pageNumber": 1, "pageSize": 5000,
        }
        d = _http_get(url, params)
        rows = (d.get("result") or {}).get("data") if d else None
        if rows:
            norm = set()
            for r in rows:
                tdi = str(r.get("TRADE_DATE_INT", ""))
                if len(tdi) == 8 and tdi.isdigit():
                    norm.add(f"{tdi[:4]}-{tdi[4:6]}-{tdi[6:]}")
            if norm:
                _save_json_cache(cache_name, sorted(norm))
                return norm
    except Exception as e:
        LOG.debug("em 交易日历备源失败: %s", e)

    # 兜底：陈旧缓存
    cached = _load_json_cache(cache_name, ttl=timedelta(days=365))
    if cached:
        LOG.info("交易日历回退陈旧缓存（>7天）")
        return set(cached)
    return None


# ═══════════════════════════════════════════════════════
# 2. 涨停股池：每日所有涨停股
# ═══════════════════════════════════════════════════════
def fetch_zt_pool(date_str: str) -> list[dict]:
    """
    单日涨停股池。
    主源：akshare.stock_zt_pool_em(date)              ← 含 封板资金/首次封板时间/炸板次数
    备源：akshare.stock_zt_pool_zbgc_em(date)         ← 炸板股池
    兜底：EM datacenter 9501 RPT_LIMIT_BOARD_INFO     ← 当前接口常 down
    返回 [{code, name, streak, limit_price, limit_order_amount, amount, first_time, ...}]
    """
    out: list[dict] = []

    # 主源
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=date_str)
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                try:
                    out.append({
                        "code": str(r.get("代码", "")).zfill(6),
                        "name": str(r.get("名称", "")),
                        "streak": int(r.get("连板数", 1) or 1),
                        "limit_price": float(r.get("最新价", 0) or 0),
                        "limit_order_amount": float(r.get("封板资金", 0) or 0),
                        "amount": float(r.get("成交额", 0) or 0),
                        "market_cap": float(r.get("流通市值", 0) or 0),
                        "sector": str(r.get("所属行业", "")),
                        "first_time": str(r.get("首次封板时间", "")),
                        "burst_count": int(r.get("炸板次数", 0) or 0),
                        "last_time": str(r.get("最后封板时间", "")),
                        "turnover_pct": float(r.get("换手率", 0) or 0),
                    })
                except Exception:
                    continue
            if out:
                return out
    except Exception as e:
        LOG.warning("akshare_zt_pool %s 失败: %s", date_str, e)

    # 备源：炸板股池（字段不完整，仅含 涨停价/首次封板时间/炸板次数）
    try:
        import akshare as ak
        df = ak.stock_zt_pool_zbgc_em(date=date_str)
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                try:
                    out.append({
                        "code": str(r.get("代码", "")).zfill(6),
                        "name": str(r.get("名称", "")),
                        "streak": 0,
                        "limit_price": float(r.get("涨停价", 0) or 0),
                        "limit_order_amount": 0.0,
                        "amount": 0.0,
                        "market_cap": 0.0,
                        "sector": "",
                        "first_time": str(r.get("首次封板时间", "")),
                        "burst_count": int(r.get("炸板次数", 0) or 0),
                        "last_time": "",
                    })
                except Exception:
                    continue
            if out:
                LOG.info("%s 回退到炸板股池（无封单数据）", date_str)
                return out
    except Exception as e:
        LOG.debug("akshare_zt_zbgc %s 失败: %s", date_str, e)

    # 兜底：EM datacenter 9501
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_LIMIT_BOARD_INFO",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,LIMIT_PRICE,FLD_LIMIT_ORDER_VOLUME,LIMIT_ORDER_AMOUNT,FLD_AMOUNT,FLD_MARKET_CAP,BOARD_NAME,OPEN_TIME,FIRST_LIMIT_UP_TIME,LIMIT_UP_TIMES",
            "filter": f'(TRADE_DATE>="{date_str}")',
            "pageNumber": 1, "pageSize": 500,
        }
        d = _http_get(url, params, retries=2)
        rows = (d.get("result") or {}).get("data") if d else None
        if rows:
            for r in rows:
                try:
                    out.append({
                        "code": str(r.get("SECURITY_CODE", "")).zfill(6),
                        "name": str(r.get("SECURITY_NAME_ABBR", "")),
                        "streak": int(r.get("LIMIT_UP_TIMES", 1) or 1),
                        "limit_price": float(r.get("LIMIT_PRICE", 0) or 0),
                        "limit_order_amount": float(r.get("LIMIT_ORDER_AMOUNT", 0) or 0),
                        "amount": float(r.get("FLD_AMOUNT", 0) or 0),
                        "market_cap": float(r.get("FLD_MARKET_CAP", 0) or 0),
                        "sector": str(r.get("BOARD_NAME", "")),
                        "first_time": str(r.get("FIRST_LIMIT_UP_TIME", "")),
                        "burst_count": 0,
                        "last_time": "",
                    })
                except Exception:
                    continue
            if out:
                return out
    except Exception as e:
        LOG.debug("em 9501 涨停池失败: %s", e)

    return out


# ═══════════════════════════════════════════════════════
# 3. 行业板块列表：[{name, code}]
# ═══════════════════════════════════════════════════════
def fetch_sector_industries() -> list[dict]:
    """
    全部行业板块列表（~90 个，code=BKxxxx）。
    主源：akshare.ths（同花顺）  ← 字段齐全
    备源：akshare.em             ← EM 接口
    兜底：push2delay.eastmoney.com clist 多页
    """
    cache_name = "sector_industries"
    cached = _load_json_cache(cache_name)
    if cached:
        return cached

    rows: list[dict] = []

    # 主源：同花顺
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_ths()
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                rows.append({"name": str(r.get("name", "")), "code": str(r.get("code", ""))})
            if rows:
                _save_json_cache(cache_name, rows)
                return rows
    except Exception as e:
        LOG.warning("ths 板块列表失败: %s", e)

    # 备源：东方财富 EM
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                rows.append({
                    "name": str(r.get("板块名称", "")),
                    "code": str(r.get("板块代码", "")),
                })
            if rows:
                _save_json_cache(cache_name, rows)
                return rows
    except Exception as e:
        LOG.warning("em 板块列表失败: %s", e)

    # 兜底：push2delay 多页
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        for pn in range(1, 8):
            params = {
                "pn": pn, "pz": 30, "po": 1, "np": 1,
                "fltt": 2, "invt": 2, "fid": "f3",
                "fs": "m:90+t:2",
                "fields": "f12,f14",
            }
            d = _http_get(url, params, retries=2)
            diff = (d.get("data") or {}).get("diff") if d else None
            if not diff:
                break
            for it in diff:
                rows.append({"name": it.get("f14", ""), "code": it.get("f12", "")})
            if len(diff) < 30:
                break
        if rows:
            _save_json_cache(cache_name, rows)
            return rows
    except Exception as e:
        LOG.debug("push2delay 板块列表失败: %s", e)

    return rows


# ═══════════════════════════════════════════════════════
# 4. 板块成分股：[(code, name)]
# ═══════════════════════════════════════════════════════
def fetch_sector_constituents(sector_code: str, sector_name: str = "") -> list[tuple[str, str]]:
    """
    板块成分股。
    主源：akshare.stock_board_industry_cons_em(code)  (2026-07 起东财接口挂死,加 5s 硬超时)
    备源：东财 push2 clist (BK 行业)
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
    out: list[tuple[str, str]] = []

    # 主源 — 5s 硬超时,东财接口 hang 时不拖死整条 screen
    def _ak_main():
        import akshare as ak
        df = ak.stock_board_industry_cons_em(symbol=sector_code)
        if df is None or df.empty:
            return []
        return [(str(r.get("代码", "")).zfill(6), str(r.get("名称", "")))
                for _, r in df.iterrows()]

    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            rows = ex.submit(_ak_main).result(timeout=5)
            if rows:
                return rows
        except FutTimeout:
            LOG.warning("em cons %s 超时 5s,东财接口挂死,跳过", sector_code)
        except Exception as e:
            LOG.warning("em cons %s 失败: %s", sector_code, e)

    # 备源：push2 clist (BK 行业)
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        for pn in range(1, 6):
            params = {
                "pn": pn, "pz": 50, "po": 1, "np": 1,
                "fltt": 2, "invt": 2, "fid": "f3",
                "fs": f"b:{sector_code}+f:!50",
                "fields": "f12,f14",
            }
            d = _http_get(url, params, retries=2)
            diff = (d.get("data") or {}).get("diff") if d else None
            if not diff:
                break
            for it in diff:
                out.append((str(it.get("f12", "")).zfill(6), str(it.get("f14", ""))))
            if len(diff) < 50:
                break
    except Exception as e:
        LOG.debug("push2 cons %s 失败: %s", sector_code, e)

    return out


# ═══════════════════════════════════════════════════════
# 4b. Prefilter 数据源（2026-07 推荐池重构新增）
# ═══════════════════════════════════════════════════════
def fetch_recent_zt_pool(days: int = 3) -> dict[str, dict]:
    """
    近 N 个交易日涨停池合并。
    返回 {code: {"name", "zt_count", "total_streak", "sector", "last_date"}}，
    zt_count = 该股在近 days 天内涨停过的天数，total_streak = 累计连板数。

    2026-07：原 fetch_trade_dates 返回的日期格式是 "YYYY-MM-DD"，而 fetch_zt_pool
    要 "YYYYMMDD"。这里统一用 datetime 解析 + 转 compact 格式，并只取 <= 今天的日期
    （trade_dates 偶尔会含未来日期，需过滤）。
    """
    import datetime as _dt
    today = _dt.date.today()
    all_dates_raw = sorted(fetch_trade_dates() or [], reverse=True)
    dates: list[str] = []
    for d in all_dates_raw:
        try:
            dt = _dt.datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt > today:
            continue
        dates.append(dt.strftime("%Y%m%d"))
        if len(dates) >= days:
            break
    agg: dict[str, dict] = {}
    for d in dates:
        try:
            pool = fetch_zt_pool(d) or []
        except Exception as e:
            LOG.warning("recent_zt %s 失败: %s", d, e)
            continue
        for row in pool:
            code = row.get("code", "")
            if not code:
                continue
            cur = agg.setdefault(code, {
                "code": code,
                "name": row.get("name", ""),
                "zt_count": 0,
                "total_streak": 0,
                "sector": row.get("sector", ""),
                "last_date": d,
                "last_streak": 0,
            })
            cur["zt_count"] += 1
            cur["total_streak"] += int(row.get("streak", 1) or 1)
            if d >= cur["last_date"]:
                cur["last_date"] = d
                cur["last_streak"] = int(row.get("streak", 1) or 1)
                cur["sector"] = row.get("sector") or cur["sector"]
                cur["name"] = row.get("name") or cur["name"]
    LOG.info("recent_zt_pool days=%s → %d 只", days, len(agg))
    return agg


def fetch_hot_sectors(top_n_flow: int = 15, top_n_pct: int = 10) -> list[dict]:
    """
    今日热门板块 = (主力净流入 Top N) ∪ (涨幅 Top N)。

    2026-07-08: 用户要求优先东财。东财 stock_sector_fund_flow_rank +
    stock_board_industry_name_em 仍 RemoteDisconnected → 5s 硬超时快速失败,
    THS 同花顺接口做兜底（一次拿 90 板块含涨跌幅+净流入+领涨股）。

    返回 [{"code", "name", "rank_flow", "rank_pct", "rank_kind", "rank", "change_pct", "net_inflow"}]
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

    # 1) 优先东财 (5s 硬超时)
    df_em = None
    name_to_code_em: dict[str, str] = {}

    def _em_main():
        import akshare as ak
        return ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")

    def _em_name():
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return {}
        return {str(r.get("板块名称", "")): str(r.get("板块代码", ""))
                for _, r in df.iterrows()}

    # 2026-07-09: 不用 with 块, 手动 shutdown(wait=False) 才能不被 __exit__ 阻塞
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        f_main = ex.submit(_em_main)
        f_name = ex.submit(_em_name)
        try:
            df_em = f_main.result(timeout=5)
        except (FutTimeout, Exception) as e:
            LOG.warning("hot_sector 东财主源 5s 超时/失败: %s", e)
        try:
            name_to_code_em = f_name.result(timeout=5) or {}
        except (FutTimeout, Exception) as e:
            LOG.warning("hot_sector 东财 name 接口 5s 超时/失败: %s", e)
    finally:
        ex.shutdown(wait=False)

    if df_em is not None and not df_em.empty and name_to_code_em:
        LOG.info("hot_sector 使用东财数据源")
        sectors_em: dict[str, dict] = {}
        pct_ranked = df_em[~df_em["名称"].astype(str).str.contains("ST", na=False)] \
            .sort_values("涨跌幅", ascending=False)
        flow_ranked = df_em.sort_values("主力净流入-净额", ascending=False)

        def _ingest_em(df, kind):
            for i, (_, r) in enumerate(df.iterrows(), start=1):
                name = str(r.get("名称", ""))
                code = name_to_code_em.get(name, name)
                cur = sectors_em.setdefault(code, {
                    "code": code, "name": name,
                    "rank_flow": None, "rank_pct": None,
                    "change_pct": float(r.get("涨跌幅", 0) or 0),
                    "net_inflow": float(r.get("主力净流入-净额", 0) or 0),
                })
                if kind == "flow":
                    cur["rank_flow"] = i
                    cur["net_inflow"] = float(r.get("主力净流入-净额", 0) or 0)
                else:
                    cur["rank_pct"] = i
                    cur["change_pct"] = float(r.get("涨跌幅", 0) or 0)

        _ingest_em(flow_ranked.head(top_n_flow), "flow")
        _ingest_em(pct_ranked.head(top_n_pct), "pct")

        out_em: list[dict] = []
        for code, s in sectors_em.items():
            kinds = []
            if s["rank_flow"] is not None:
                kinds.append("flow")
            if s["rank_pct"] is not None:
                kinds.append("pct")
            out_em.append({
                "code": code,
                "name": s["name"],
                "rank_flow": s["rank_flow"],
                "rank_pct": s["rank_pct"],
                "rank_kind": "+".join(kinds) or "none",
                "rank": min([r for r in (s["rank_flow"], s["rank_pct"]) if r is not None]),
                "change_pct": s["change_pct"],
                "net_inflow": s["net_inflow"],
            })
        out_em.sort(key=lambda x: x["rank"])
        LOG.info("hot_sectors(东财) → %d 个", len(out_em))
        return out_em

    # 2) 兜底: THS 同花顺 (8s 硬超时)
    LOG.info("hot_sector 东财不可用,降级 THS")
    summary_df = None
    name_to_code: dict[str, str] = {}

    def _ths_summary():
        import akshare as ak
        return ak.stock_board_industry_summary_ths()

    def _ths_name():
        import akshare as ak
        df = ak.stock_board_industry_name_ths()
        out = {}
        for _, r in df.iterrows():
            out[str(r.get("name", ""))] = str(r.get("code", ""))
        return out

    # 2026-07-09: 同上, 不用 with, 手动 shutdown
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        try:
            summary_df = ex.submit(_ths_summary).result(timeout=8)
        except (FutTimeout, Exception) as e:
            LOG.warning("hot_sector summary_ths 8s 超时/失败: %s", e)
        try:
            name_to_code = ex.submit(_ths_name).result(timeout=8) or {}
        except (FutTimeout, Exception) as e:
            LOG.warning("hot_sector name_ths 8s 超时/失败: %s", e)
    finally:
        ex.shutdown(wait=False)

    if summary_df is None or summary_df.empty:
        return []

    pct_ranked = summary_df[~summary_df["板块"].str.contains("ST", na=False)] \
        .sort_values("涨跌幅", ascending=False)
    pct_top = pct_ranked.head(top_n_pct)
    flow_ranked = summary_df.sort_values("净流入", ascending=False)
    flow_top = flow_ranked.head(top_n_flow)

    sectors: dict[str, dict] = {}

    def _ingest(df, kind):
        for i, (_, r) in enumerate(df.iterrows(), start=1):
            name = str(r.get("板块", ""))
            code = name_to_code.get(name, "")
            if not code:
                continue
            cur = sectors.setdefault(code, {
                "code": code, "name": name,
                "rank_flow": None, "rank_pct": None,
                "change_pct": float(r.get("涨跌幅", 0) or 0),
                "net_inflow": float(r.get("净流入", 0) or 0),
            })
            if kind == "flow":
                cur["rank_flow"] = i
                cur["net_inflow"] = float(r.get("净流入", 0) or 0)
            else:
                cur["rank_pct"] = i
                cur["change_pct"] = float(r.get("涨跌幅", 0) or 0)

    _ingest(flow_top, "flow")
    _ingest(pct_top, "pct")

    out: list[dict] = []
    for code, s in sectors.items():
        kinds = []
        if s["rank_flow"] is not None:
            kinds.append("flow")
        if s["rank_pct"] is not None:
            kinds.append("pct")
        out.append({
            "code": code,
            "name": s["name"],
            "rank_flow": s["rank_flow"],
            "rank_pct": s["rank_pct"],
            "rank_kind": "+".join(kinds) or "none",
            "rank": min([r for r in (s["rank_flow"], s["rank_pct"]) if r is not None]),
            "change_pct": s["change_pct"],
            "net_inflow": s["net_inflow"],
        })
    out.sort(key=lambda x: x["rank"])
    LOG.info("hot_sectors(THS) → %d 个 (flow=%s + pct=%s)",
             len(out), top_n_flow, top_n_pct)
    return out


def filter_zt_by_hot_sectors(zt_pool: dict[str, dict],
                             hot_sectors: list[dict]) -> dict[str, dict]:
    """
    过滤涨停池：只保留「所属行业 ∈ 热门板块名集合」的股票。

    2026-07：EM cons 接口对 THS 881xxx code 持续 RemoteDisconnected；改成直接用
    fetch_zt_pool 返回的 sector 字段做匹配，避开成分股拉取。匹配规则：
      - sector 名精确匹配板块名
      - sector 名 ∈ {hot_sector.name 的子串} (防 sector 字段是 "半导体" 而板块是 "半导体" 但前面有"国产"之类的前缀)
      - 反向：板块名 ∈ sector (sector 字段常被截断如 "半导体" vs "半导体材料")

    返回过滤后的 zt_pool 子集 (dict[code, info])，并附加 hot_sector_rank / hot_sector_name 字段。
    """
    if not hot_sectors:
        return {}
    hot_names = [s["name"] for s in hot_sectors]
    out: dict[str, dict] = {}
    for code, info in zt_pool.items():
        sec = info.get("sector", "") or ""
        if not sec:
            continue
        matched = None
        for hs in hot_sectors:
            hn = hs["name"]
            # 双向 substring 匹配
            if hn in sec or sec in hn:
                matched = hs
                break
        if matched is None:
            continue
        new_info = dict(info)
        new_info["hot_sector_rank"] = matched["rank"]
        new_info["hot_sector_kind"] = matched["rank_kind"]
        new_info["hot_sector_name"] = matched["name"]
        out[code] = new_info
    LOG.info("filter_zt_by_hot_sectors: %d → %d (匹配 %d 个热门板块)",
             len(zt_pool), len(out), len(hot_sectors))
    return out


# ═══════════════════════════════════════════════════════
# 5. 龙虎榜（单日明细）
# ═══════════════════════════════════════════════════════
def fetch_lhb_detail(date_str: str) -> Any:
    """
    单日龙虎榜明细。
    主源：akshare.stock_lhb_detail_em(start, end)
    备源：东财 datacenter LHB（待补字段较多，这里先只主源 + 日内缓存）
    """
    cache_name = f"lhb_{date_str}"
    cached = _load_json_cache(cache_name, ttl=timedelta(days=2))
    if cached is not None:
        return cached  # list of dict or []

    try:
        import akshare as ak
        df = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        if df is None or df.empty:
            _save_json_cache(cache_name, [])
            return pd.DataFrame() if pd else []
        # 过滤退市股
        if "名称" in df.columns:
            df = df[~df["名称"].astype(str).str.contains("退", na=False)]
        result = df.reset_index(drop=True)
        _save_json_cache(cache_name, result.to_dict(orient="records"))
        return result
    except Exception as e:
        LOG.warning("龙虎榜 %s 拉取失败: %s", date_str, e)
        return None


def fetch_lhb_hyyyb(date_str: str) -> list[dict] | None:
    """
    龙虎榜席位明细（营业部 → 买入股票清单）。
    主源：akshare.stock_lhb_hyyyb_em(start, end)
    备源：暂无（EM 9501 已 down）
    返 list[dict]（含 营业部名称/买入股票 等字段），失败返 None。
    """
    cache_name = f"lhb_hyyyb_{date_str}"
    cached = _load_json_cache(cache_name, ttl=timedelta(days=2))
    if cached is not None:
        return cached  # list of dict or []

    try:
        import akshare as ak
        df = ak.stock_lhb_hyyyb_em(start_date=date_str, end_date=date_str)
        if df is None or df.empty:
            _save_json_cache(cache_name, [])
            return []
        result = df.reset_index(drop=True).to_dict(orient="records")
        _save_json_cache(cache_name, result)
        return result
    except Exception as e:
        LOG.warning("akshare lhb_hyyyb %s 失败: %s", date_str, e)
        return None  # 调用方降级


# ═══════════════════════════════════════════════════════
# 6. 北向资金概览
# ═══════════════════════════════════════════════════════
def fetch_north_flow_summary() -> Any:
    """
    沪深港通资金净买入概览（4 行）。
    主源：akshare.stock_hsgt_fund_flow_summary_em
    备源：东财 push2 北向资金
    """
    cache_name = "north_flow_summary"
    cached = _load_json_cache(cache_name, ttl=timedelta(hours=2))
    if cached is not None:
        return pd.DataFrame(cached) if pd else cached

    try:
        import akshare as ak
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is not None and not df.empty:
            _save_json_cache(cache_name, df.to_dict(orient="records"))
            return df
    except Exception as e:
        LOG.warning("akshare 北向资金失败: %s", e)

    # 备源：push2 北向资金（北向资金页面 API）
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 10, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fs": "m:6+m:7+m:8",  # 沪/深/京
            "fields": "f12,f14,f2,f3,f8",
        }
        d = _http_get(url, params, retries=2)
        diff = (d.get("data") or {}).get("diff") if d else None
        if diff and pd:
            df = pd.DataFrame(diff)
            _save_json_cache(cache_name, df.to_dict(orient="records"))
            return df
    except Exception as e:
        LOG.debug("push2 北向失败: %s", e)

    return None


# ═══════════════════════════════════════════════════════
# 7. 全 A 实时行情：[(code, name)]（用于回测/扫描）
# ═══════════════════════════════════════════════════════
def fetch_spot_a(exclude_bjs: bool = True, exclude_st: bool = True) -> list[tuple[str, str]]:
    """
    全 A 实时行情（代码+名称）。
    主源：push2delay 直连
    备源：akshare.stock_zh_a_spot_em
    兜底：返回空
    """
    out: list[tuple[str, str]] = []

    # 主源：push2delay（akshare 经常断）
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        for pn in range(1, 50):
            params = {
                "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深京全 A
                "fields": "f12,f14",
            }
            d = _http_get(url, params, retries=2)
            diff = (d.get("data") or {}).get("diff") if d else None
            if not diff:
                break
            for it in diff:
                code = str(it.get("f12", "")).zfill(6)
                name = str(it.get("f14", ""))
                if not code or not name:
                    continue
                if exclude_bjs and (code.startswith(("688", "689", "8", "4", "30"))):
                    continue
                if exclude_st and ("ST" in name or "退" in name):
                    continue
                out.append((code, name))
            if len(diff) < 100:
                break
        if out:
            return out
    except Exception as e:
        LOG.warning("push2delay spot 失败: %s", e)

    # 备源：akshare
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("代码", "")).zfill(6)
                name = str(row.get("名称", ""))
                if not code:
                    continue
                if exclude_bjs and code.startswith(("688", "689", "8", "4", "30")):
                    continue
                if exclude_st and ("ST" in name or "退" in name):
                    continue
                out.append((code, name))
    except Exception as e:
        LOG.debug("akshare spot 失败: %s", e)

    return out


# ═══════════════════════════════════════════════════════
# 8. 个股当日分钟线
# ═══════════════════════════════════════════════════════
def fetch_intraday_min(code: str, period: str = "1") -> Any:
    """
    个股当日分钟 K 线。
    主源：akshare.stock_zh_a_hist_min_em(symbol, period, adjust='qfq')
    备源：腾讯 qt.gtimg.cn 分钟线（如有）
    """
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist_min_em(symbol=code, period=period, adjust="qfq")
        if df is not None and not df.empty:
            return df
    except Exception as e:
        LOG.warning("akshare 分钟线 %s 失败: %s", code, e)

    # 备源：暂时无（腾讯分钟线鉴权严格）
    LOG.debug("分钟线 %s 全部源失败", code)
    return None


# ═══════════════════════════════════════════════════════
# 9. 大单交易（盘后/盘中）
# ═══════════════════════════════════════════════════════
def fetch_big_deals(limit: int = 20) -> Any:
    """
    实时大单交易（东财大单精灵）。
    主源：akshare.stock_fund_flow_big_deal
    备源：东财 push2 大单流
    """
    try:
        import akshare as ak
        df = ak.stock_fund_flow_big_deal()
        if df is not None and not df.empty:
            return df.head(limit).reset_index(drop=True)
    except Exception as e:
        LOG.warning("akshare 大单流失败: %s", e)

    # 备源：暂时跳过
    return None


# ═══════════════════════════════════════════════════════
# CLI 调试
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    cmds = {
        "trade_dates": lambda: sorted(fetch_trade_dates() or []),
        "sectors":     lambda: fetch_sector_industries(),
        "north":       lambda: (fetch_north_flow_summary() or pd.DataFrame()).to_string() if pd else "pd not avail",
        "spot":        lambda: f"共 {len(fetch_spot_a())} 只",
        "zt":          lambda: fetch_zt_pool(sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y%m%d")),
        "min":         lambda: (fetch_intraday_min(sys.argv[2]) if len(sys.argv) > 2 else "need code"),
    }
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(f"用法: python3 multi_source_fetchers.py <{'|'.join(cmds.keys())}> [args...]")
        sys.exit(1)
    print(cmds[sys.argv[1]]())