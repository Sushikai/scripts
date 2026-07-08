"""
tuixue_v3/data_layer.py
三级热备数据层（akshare / 东财 / 同花顺 / 腾讯）+ 本地 JSON 缓存
- 单源超时 5s 自动切下一级
- 单源重试 3 次
- 本地缓存：日线 4h / 分时 30min / 基本面 24h
"""
from __future__ import annotations

import json
import logging
import time as systime
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import config as cfg
from . import lib_common as lc
from . import multi_source_fetchers as msf

log = logging.getLogger("tuixue_v3.data")

# ═══════════════════════════════════════════════════
# 缓存工具
# ═══════════════════════════════════════════════════
def _cache_path(name: str) -> Path:
    return cfg.CACHE_DIR / f"{name}.json"


def _cache_load(name: str, ttl_sec: int) -> Any | None:
    p = _cache_path(name)
    if not p.exists():
        return None
    age = systime.time() - p.stat().st_mtime
    if age > ttl_sec:
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _cache_save(name: str, data: Any) -> None:
    try:
        _cache_path(name).write_text(json.dumps(data, ensure_ascii=False, default=str))
    except Exception as e:
        log.warning(f"缓存保存失败 {name}: {e}")


def _cache_save_df(name: str, df: pd.DataFrame) -> None:
    """DataFrame 缓存：日期转字符串后存 dict-of-list"""
    if df is None or df.empty:
        return
    try:
        d = df.copy()
        if "日期" in d.columns:
            d["日期"] = d["日期"].astype(str)
        # 转 list-of-dict（更紧凑）
        records = d.to_dict(orient="records")
        _cache_save(name, records)
    except Exception as e:
        log.warning(f"DataFrame 缓存失败 {name}: {e}")


def _cache_load_df(name: str, ttl_sec: int) -> pd.DataFrame | None:
    raw = _cache_load(name, ttl_sec)
    if raw is None:
        return None
    try:
        df = pd.DataFrame(raw)
        if "日期" in df.columns:
            df["日期"] = pd.to_datetime(df["日期"])
            df = df.sort_values("日期").reset_index(drop=True)
        return df
    except Exception:
        return None


# ═══════════════════════════════════════════════════
# 全市场股票列表（带缓存）
# ═══════════════════════════════════════════════════
def fetch_stock_list() -> list[tuple[str, str]]:
    """返回 [(code, name), ...]，剔除创业板/科创板/北交所/ST"""
    cache_key = "stock_list_filtered"
    cached = _cache_load(cache_key, cfg.CACHE_TTL_FUNDAMENTAL)
    if cached:
        return [(c, n) for c, n in cached]

    raw = msf.fetch_spot_a(exclude_bjs=True, exclude_st=True) or []
    filtered = []
    for code, name in raw:
        if not code:
            continue
        # 沪市主板 60xxxx / 601xxx / 603xxx / 605xxx
        # 深市主板 000xxx / 001xxx / 002xxx（002 是中小板，已并入主板）
        # 排除：创业板 300xxx / 科创板 688xxx / 北交所 8xx / 4xx
        if code.startswith(("300", "301", "688", "689", "8", "43", "83", "87")):
            continue
        if not (code.startswith(("60", "601", "603", "605", "000", "001", "002"))):
            continue
        # ST 已在 exclude_st 阶段剔除
        if "ST" in (name or "") or "*ST" in (name or ""):
            continue
        filtered.append((code, name))

    _cache_save(cache_key, filtered)
    log.info(f"股票池过滤后: {len(filtered)} 只")
    return filtered


def fetch_stock_list_all() -> list[tuple[str, str]]:
    """返回全 A 股 [(code, name), ...]，不过滤 (用于个股名称查询)
    2026-07-08: fetch_spot_a 默认过滤创业板/科创板/北交所,改用 akshare.stock_info_a_code_name() 全量
    """
    cache_key = "stock_list_all"
    cached = _cache_load(cache_key, cfg.CACHE_TTL_FUNDAMENTAL)
    if cached:
        return [(c, n) for c, n in cached]

    out: list[tuple[str, str]] = []
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        if df is not None and not df.empty and "code" in df.columns and "name" in df.columns:
            for _, r in df.iterrows():
                code = str(r["code"]).zfill(6)
                name = str(r["name"])
                if code and name:
                    out.append((code, name))
    except Exception as e:
        log.warning(f"fetch_stock_list_all akshare 失败: {e}")
    # 兜底: 主源 + 创业板/科创板/北交所字典
    if not out:
        raw = msf.fetch_spot_a(exclude_bjs=False, exclude_st=True) or []
        out = [(c, n) for c, n in raw if c]

    _cache_save(cache_key, out)
    log.info(f"全 A 股票池: {len(out)} 只")
    return out


# ═══════════════════════════════════════════════════
# 日 K 线（带三级热备 + 缓存）
# ═══════════════════════════════════════════════════
def fetch_daily(code: str, days: int = 120, force: bool = False) -> pd.DataFrame | None:
    """优先 SQLite 索引缓存(共享) → 拉三级热备 → 双写 JSON/SQLite"""
    if not force:
        try:
            from . import cache_db
            cached = cache_db.daily().get(code, days)
            if cached is not None and len(cached) >= days * 0.7:
                return cached
        except Exception as e:
            log.debug(f"sqlite cache 读失败 {code}: {e}")

        # 兼容老 JSON 缓存
        cache_key = f"daily_{code}_{days}"
        cached = _cache_load_df(cache_key, cfg.CACHE_TTL_DAILY)
        if cached is not None and len(cached) >= days * 0.7:
            try:
                from . import cache_db
                cache_db.daily().set(code, cached)
            except Exception:
                pass
            return cached

    t0 = systime.time()
    df = lc.fetch_daily(code, days=days)
    elapsed = systime.time() - t0

    if df is None or df.empty:
        log.warning(f"[{code}] 日线三级热备全部失败(耗时 {elapsed:.1f}s)")
        return None

    if elapsed > cfg.DATA_TIMEOUT_SEC:
        log.warning(f"[{code}] 日线耗时 {elapsed:.1f}s 超阈值")

    try:
        from . import cache_db
        cache_db.daily().set(code, df)
    except Exception as e:
        log.debug(f"sqlite cache 写失败 {code}: {e}")
    _cache_save_df(f"daily_{code}_{days}", df)
    return df


# ═══════════════════════════════════════════════════
# 分时 K 线（带缓存）
# ═══════════════════════════════════════════════════
def fetch_intraday(code: str, date_str: str | None = None, force: bool = False) -> pd.DataFrame | None:
    """分时 1 分钟 K。date_str: YYYYMMDD；None 为最近一个交易日。

    3 种逃生:
      1) lib_common.fetch_intraday_min   — 主（如不存在则跳过）
      2) akshare.stock_zh_a_hist_min_em  — 备 1
      3) None                              — 兜底（让上层标"分时不可用"）
    """
    cache_key = f"intraday_{code}_{date_str or 'latest'}"
    if not force:
        cached = _cache_load_df(cache_key, cfg.CACHE_TTL_INTRADAY)
        if cached is not None:
            return cached

    # 主
    t0 = systime.time()
    df = None
    try:
        if hasattr(lc, "fetch_intraday_min"):
            df = lc.fetch_intraday_min(code, period="1")
    except Exception as e:
        log.debug(f"fetch_intraday_min {code} 失败: {e}")

    # 备 1: akshare
    if df is None or df.empty:
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist_min_em(symbol=code, period="1", adjust="qfq")
        except Exception as e:
            log.debug(f"akshare hist_min {code} 失败: {e}")

    elapsed = systime.time() - t0
    if df is None or (hasattr(df, "empty") and df.empty):
        log.warning(f"[{code}] 分时获取失败（{elapsed:.1f}s）")
        return None

    _cache_save_df(cache_key, df)
    return df


# ═══════════════════════════════════════════════════
# 实时行情（用于当日实时选股）
# ═══════════════════════════════════════════════════
def fetch_realtime_snapshot(code: str) -> dict | None:
    """单只实时：最新价 / 涨跌幅 / 换手 / 成交额 / 总市值 / 流通市值"""
    cache_key = f"realtime_{code}"
    cached = _cache_load(cache_key, cfg.CACHE_TTL_INTRADAY)
    if cached:
        return cached
    rt = lc.fetch_realtime(code)
    if not rt:
        return None
    _cache_save(cache_key, rt)
    return rt


def fetch_spot_realtime() -> list[dict]:
    """全市场实时行情快照（仅在手动选股时调用，避免频繁抓取）"""
    raw = msf.fetch_spot_a(exclude_bjs=True, exclude_st=False) or []
    # raw 是 [(code, name)]，需要补全字段
    return [{"code": c, "name": n} for c, n in raw]


# ═══════════════════════════════════════════════════
# 板块数据
# ═══════════════════════════════════════════════════
def fetch_sector_industries() -> list[dict]:
    """全市场行业板块列表"""
    cache_key = "sector_industries"
    cached = _cache_load(cache_key, cfg.CACHE_TTL_FUNDAMENTAL)
    if cached:
        return cached
    data = msf.fetch_sector_industries() or []
    _cache_save(cache_key, data)
    return data


def fetch_sector_constituents(sector_code: str, sector_name: str = "") -> list[tuple[str, str]]:
    return lc.fetch_sector_constituents(sector_code, sector_name) or []


# ═══════════════════════════════════════════════════
# 涨停 / 炸板 / 连板 / 封单
# ═══════════════════════════════════════════════════
def fetch_limit_up_pool(date_str: str | None = None) -> list[dict]:
    """涨停池（含涨停价、连板数、封单金额、炸板次数等）

    3 种逃生:
      1) 同包 multi_source_fetchers.fetch_zt_pool       — 主
      2) akshare.stock_zt_pool_em(date)               — 备 1
      3) 返回空列表                                    — 兜底
    """
    today = date_str or datetime.now().strftime("%Y%m%d")
    # 主
    try:
        from . import multi_source_fetchers as msf
        return msf.fetch_zt_pool(today) or []
    except Exception:
        pass
    # 备 1
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=today)
        if df is not None and not df.empty:
            out = []
            for _, r in df.iterrows():
                out.append({
                    "code": str(r.get("代码", "")).zfill(6),
                    "name": str(r.get("名称", "")),
                    "streak": int(r.get("连板数", 1) or 1),
                    "limit_price": float(r.get("最新价", 0) or 0),
                })
            return out
    except Exception:
        pass
    return []


# ═══════════════════════════════════════════════════
# 交易日历
# ═══════════════════════════════════════════════════
def fetch_trade_dates(start: str, end: str) -> list[str]:
    """返回 [YYYYMMDD, ...] 升序

    2 种逃生:
      1) 同包 multi_source_fetchers.fetch_trade_dates
      2) akshare 工具交易日工具
    """
    try:
        from . import multi_source_fetchers as msf
        dates = msf.fetch_trade_dates()
    except Exception:
        try:
            import akshare as ak
            dates = ak.tool_trade_date_hist_sina()["date"].dt.strftime("%Y%m%d").tolist()
        except Exception:
            dates = []
    if isinstance(dates, set):
        dates = sorted(dates)
    elif isinstance(dates, list):
        dates = sorted(set(dates))
    return [d for d in dates if start.replace("-", "") <= d <= end.replace("-", "")]


# ═══════════════════════════════════════════════════
# 批量拉取（日线）— 回测用
# ═══════════════════════════════════════════════════
def batch_fetch_daily(codes: list[str], days: int = 250, progress_every: int = 50) -> dict[str, pd.DataFrame]:
    """批量拉日线，结果以 {code: df} 返回。回测预热用。"""
    out: dict[str, pd.DataFrame] = {}
    for i, c in enumerate(codes):
        df = fetch_daily(c, days=days, force=False)
        if df is not None and not df.empty:
            out[c] = df
        if (i + 1) % progress_every == 0:
            log.info(f"  batch 日线进度 {i+1}/{len(codes)} 命中 {len(out)}")
    log.info(f"batch_fetch_daily 完成：请求 {len(codes)} 命中 {len(out)}")
    return out


# ═══════════════════════════════════════════════════
# 健康检查
# ═══════════════════════════════════════════════════
def data_health_check() -> dict:
    """对外暴露：用于 run_stock_screen 前的故障预判"""
    health = {"daily_ok": False, "spot_ok": False, "sector_ok": False, "ts": datetime.now().isoformat()}
    try:
        df = fetch_daily("000001", days=30, force=True)
        health["daily_ok"] = df is not None and len(df) > 10
    except Exception as e:
        health["daily_err"] = str(e)
    try:
        sp = msf.fetch_spot_a(exclude_bjs=True, exclude_st=True)
        health["spot_ok"] = bool(sp) and len(sp) > 100
    except Exception as e:
        health["spot_err"] = str(e)
    try:
        si = msf.fetch_sector_industries()
        health["sector_ok"] = bool(si) and len(si) > 30
    except Exception as e:
        health["sector_err"] = str(e)
    log.info(f"健康检查: {health}")
    return health