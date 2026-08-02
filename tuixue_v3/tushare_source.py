#!/usr/bin/env python3
"""
tuixue_v3/tushare_source.py
Ship 2/100 — Tushare Pro 接入 (免费层 + token 注入升级)

设计:
- 不强制付费: 无 TUSHARE_TOKEN 时,所有 fetch 返回 None,优雅降级
- token 注入即用: env TUSHARE_TOKEN 或 web/_constants.TUSHARE_TOKEN 配置
- 复用现有 akshare 做免费兜底 (免费源无复权/财务/T+1 数据)
- 注册到 FetchRegistry (Ship 1 基础设施)

接口:
- tushare_daily: 日线 (复权)
- tushare_daily_basic: PE/PB/换手/市值
- tushare_financial: 财务三大表 (需要 200 积分/次)

2026-08-02 Ship 2 — 10000 轮迭代 P0 第二步
"""
from __future__ import annotations

import os
import logging
import threading
from typing import Any, Optional

from tuixue_v3.data_source_registry import FetchSource

logger = logging.getLogger(__name__)

# ─── 线程安全 token 加载 (单次) ───
_token_lock = threading.Lock()
_tushare_token: Optional[str] = None
_tushare_pro = None  # ts.pro_api() 缓存
_token_checked = False


def _load_token() -> Optional[str]:
    """从 env / web/_constants / .env.tushare 加载 token (单次加载)"""
    global _tushare_token, _tushare_pro, _token_checked
    with _token_lock:
        if _token_checked:
            return _tushare_token
        _token_checked = True

        # 优先级 1: 环境变量
        token = os.environ.get("TUSHARE_TOKEN", "").strip()

        # 优先级 2: web/_constants.py
        if not token:
            try:
                from tuixue_v3.web import _constants
                token = getattr(_constants, "TUSHARE_TOKEN", "") or ""
            except (ImportError, AttributeError):
                pass

        # 优先级 3: ~/.tushare_token 文件
        if not token:
            token_file = os.path.expanduser("~/.tushare_token")
            if os.path.isfile(token_file):
                try:
                    with open(token_file) as f:
                        token = f.read().strip()
                except Exception as e:
                    logger.debug(f"读 ~/.tushare_token 失败: {e}")

        if not token:
            logger.info("Tushare token 未配置,免费层接入将返 None (降级到 akshare)")
            return None

        try:
            import tushare as ts
            ts.set_token(token)
            _tushare_pro = ts.pro_api()
            _tushare_token = token
            logger.info(f"Tushare Pro 接入成功 (token 长度 {len(token)})")
            return token
        except ImportError:
            logger.warning("tushare 包未安装,pip install tushare 后启用")
            return None
        except Exception as e:
            logger.warning(f"Tushare 初始化失败: {e}")
            return None


def _require_data(df: Any) -> bool:
    """数据校验: 非空 DataFrame 且至少 1 行"""
    if df is None:
        return False
    try:
        return len(df) > 0
    except TypeError:
        return False


# ─── 三个 fetch 函数 ───

def _tushare_daily(code: str, days: int = 120) -> Any:
    """Tushare 日线 (含复权)"""
    token = _load_token()
    if not token or _tushare_pro is None:
        return None
    try:
        import datetime
        end = datetime.date.today().strftime("%Y%m%d")
        start = (datetime.date.today() - datetime.timedelta(days=int(days * 1.5))).strftime("%Y%m%d")
        # Tushare 需要 ts_code 格式: 600519.SH / 000001.SZ
        suffix = ".SH" if code.startswith(("6", "9", "5")) else ".SZ"
        # 北证 920xxx / 8 开头特殊处理
        if code.startswith("8") or code.startswith("4"):
            suffix = ".BJ"
        ts_code = f"{code}{suffix}"
        df = _tushare_pro.daily(
            ts_code=ts_code, start_date=start, end_date=end,
            fields="ts_code,trade_date,open,high,low,close,vol,amount",
        )
        if df is None or len(df) == 0:
            return None
        # 标准化列名 (统一前端消费)
        df = df.rename(columns={
            "trade_date": "date", "vol": "volume", "amount": "amount",
        })
        df["date"] = df["date"].astype(str)
        return df
    except Exception as e:
        logger.debug(f"tushare_daily {code} 失败: {type(e).__name__}: {e}")
        return None


def _tushare_daily_basic(code: str) -> Any:
    """Tushare daily_basic: PE/PB/换手/市值/股息率"""
    token = _load_token()
    if not token or _tushare_pro is None:
        return None
    try:
        suffix = ".SH" if code.startswith(("6", "9", "5")) else ".SZ"
        if code.startswith("8") or code.startswith("4"):
            suffix = ".BJ"
        ts_code = f"{code}{suffix}"
        df = _tushare_pro.daily_basic(
            ts_code=ts_code,
            fields="ts_code,trade_date,close,turnover_rate,pe,pe_ttm,pb,total_mv,circ_mv,dv_ratio",
        )
        if df is None or len(df) == 0:
            return None
        return df.head(60)  # 最近 60 个交易日
    except Exception as e:
        logger.debug(f"tushare_daily_basic {code} 失败: {type(e).__name__}: {e}")
        return None


def _tushare_financial(code: str) -> Any:
    """Tushare 财务三大表 (需要 200 积分/次,谨慎调用)"""
    token = _load_token()
    if not token or _tushare_pro is None:
        return None
    try:
        suffix = ".SH" if code.startswith(("6", "9", "5")) else ".SZ"
        if code.startswith("8") or code.startswith("4"):
            suffix = ".BJ"
        ts_code = f"{code}{suffix}"
        df = _tushare_pro.income(ts_code=ts_code, limit=4)
        if df is None or len(df) == 0:
            return None
        return df
    except Exception as e:
        logger.debug(f"tushare_financial {code} 失败: {type(e).__name__}: {e}")
        return None


# ─── 注册到 FetchRegistry ───

def get_sources() -> list[FetchSource]:
    """返回 3 个 FetchSource 供 bootstrap_from_legacy() 调用

    无 token 时 enabled=False, 自动跳过 (降级到 akshare)
    """
    has_token = bool(_load_token())
    return [
        FetchSource(
            name="tushare_daily",
            category="daily",
            fn=_tushare_daily,
            display_name="Tushare 日线 (复权)",
            timeout=6.0,
            priority=5,  # 比 akshare(10) 优先,但需要 token
            owner="@kai",
            requires=_require_data,
            schema_version="v2",
            tags=["pro", "auth-required", "复权"],
            enabled=has_token,
        ),
        FetchSource(
            name="tushare_daily_basic",
            category="fundamentals",
            fn=_tushare_daily_basic,
            display_name="Tushare 估值 (PE/PB/市值)",
            timeout=6.0,
            priority=5,
            owner="@kai",
            requires=_require_data,
            schema_version="v1",
            tags=["pro", "auth-required", "valuation"],
            enabled=has_token,
        ),
        FetchSource(
            name="tushare_financial",
            category="financial",
            fn=_tushare_financial,
            display_name="Tushare 财务 (利润表)",
            timeout=8.0,
            priority=10,
            owner="@kai",
            requires=_require_data,
            schema_version="v1",
            tags=["pro", "auth-required", "expensive"],  # 200 积分/次
            enabled=has_token,
        ),
    ]


def is_connected() -> bool:
    """健康检查: Tushare 是否可用 (供 /api/sources/health 显示)"""
    return bool(_load_token()) and _tushare_pro is not None
