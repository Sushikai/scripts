#!/usr/bin/env python3
"""
stock/lib_common.py
龙头扫描与持仓监控的共享工具库：
- Telegram 推送（带重试）
- 交易日/交易时段判断
- akshare 数据获取
- 技术指标（MA / MACD / KDJ / 量比）
- 通用信号检测（MA/MACD 交叉、KDJ 死叉、放量突破、量价背离）
- 移动止损 / 分级预警
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from threading import Lock

import random
import urllib.request
import akshare as ak
import requests

# 全局 requests adapter: 强制 (connect, read) 双 timeout, 避免被 ban 时 TCP 握手 hang 整个 worker
from requests.adapters import HTTPAdapter
_FAST_ADAPTER = HTTPAdapter(
    max_retries=0,  # 业务层自己重试
    pool_connections=20,
    pool_maxsize=20,
)
_FAST_SESSION = requests.Session()
_FAST_SESSION.mount("https://", _FAST_ADAPTER)
_FAST_SESSION.mount("http://", _FAST_ADAPTER)
# 默认 (connect=1.5s, read=3s) 组合
def _fast_get(url, **kw):
    """带 connect+read 双超时 + 重试 0 次, 防止 hang"""
    kw.setdefault("timeout", (1.5, 3.0))
    return _FAST_SESSION.get(url, **kw)

# 注：python-telegram-bot v20+ 是纯异步库，sync 调用会触发
# "coroutine was never awaited" 警告且消息不会真正发出。
# 这里改用直接的 REST API，不依赖库版本。

# ═══════════════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════════════
STOCK_DIR = Path(__file__).parent
HERMES_DIR = Path.home() / ".hermes"
POSITIONS_FILE = STOCK_DIR / "positions.json"

# ═══════════════════════════════════════════════════════
# Telegram 配置（从 ~/.hermes/.env 读取 TELEGRAM_BOT_TOKEN）
# ═══════════════════════════════════════════════════════
def _load_tg_token():
    """从 ~/.hermes/.env 读 TELEGRAM_BOT_TOKEN，找不到则报错"""
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if tok:
                        return tok
        except Exception:
            pass
    raise RuntimeError(
        "未找到 TELEGRAM_BOT_TOKEN，请在 ~/.hermes/.env 中配置"
    )


TELEGRAM_BOT_TOKEN = _load_tg_token()
TELEGRAM_CHAT_ID = "8579393409"
TG_SEND_TIMEOUT = 20          # 单次发送超时（长消息+Markdown 解析需要时间）
TG_RETRY_TIMES = 4            # 总尝试次数
TG_RETRY_BACKOFF = 2          # 指数退避基数：2s, 4s, 8s
TG_SEND_MAX_CHARS = 3500      # 超过此长度截断（Telegram 单条消息上限 4096 字符）

_tg_lock = Lock()


# ═══════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════
def setup_logging(name: str, log_file: Path | None = None, level=logging.INFO):
    """统一日志格式：控制台 + 文件（可选）"""
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stdout)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] " + name + ": %(message)s",
        handlers=[handler],
        force=True,
    )


# ═══════════════════════════════════════════════════════
# Telegram 推送
# ═══════════════════════════════════════════════════════
def send_telegram(text: str, parse_mode: str = "Markdown", silent: bool = False) -> bool:
    """
    发送 Telegram 消息（直接 REST 调用，避开 v20+ 异步问题）。
    带 4 次重试 + 指数退避（2s/4s/8s）。失败返回 False 但不抛异常（避免监控循环崩溃）。
    超长消息自动截断到 TG_SEND_MAX_CHARS 字符。
    """
    # 截断超长消息（保留最后换行）
    if len(text) > TG_SEND_MAX_CHARS:
        text = text[:TG_SEND_MAX_CHARS - 30] + "\n... (内容过长已截断)"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_notification": silent,
    }
    # Telegram 的 MarkdownV2 比 Markdown 严格，普通 Markdown 易爆；这里用纯文本 + 显式换行，
    # 兼容所有段落中带 `*` `()` 等符号。
    if parse_mode and parse_mode != "text":
        payload["parse_mode"] = parse_mode
    with _tg_lock:
        last_err = ""
        for attempt in range(1, TG_RETRY_TIMES + 1):
            try:
                r = requests.post(url, json=payload, timeout=TG_SEND_TIMEOUT)
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                if r.status_code == 200 and data.get("ok"):
                    if attempt > 1:
                        logging.info(f"[TG成功] 第{attempt}次重试成功")
                    return True
                last_err = data.get("description") or f"HTTP {r.status_code}"
                logging.warning(f"[TG失败 x{attempt}/{TG_RETRY_TIMES}] {last_err}")
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                logging.warning(f"[TG异常 x{attempt}/{TG_RETRY_TIMES}] {last_err}")
            if attempt < TG_RETRY_TIMES:
                time.sleep(TG_RETRY_BACKOFF ** attempt)
        logging.error(f"[TG放弃] {last_err}")
        return False


# ═══════════════════════════════════════════════════════
# 时间判断
# ═══════════════════════════════════════════════════════
def is_weekday() -> bool:
    return datetime.now().weekday() < 5


def is_trading_time(now: datetime | None = None) -> bool:
    """
    A 股交易时段：9:30-11:30 / 13:00-15:00
    周末直接判否。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))


def is_data_window(now: datetime | None = None) -> bool:
    """
    数据获取窗口：含集合竞价 + 盘中 + 午休 + 盘后 30 分钟
    9:00-15:30 + 周末否。
    即使非交易时段，也能拿到上午收盘/当日收盘价 + 买卖五档（腾讯）。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 0) <= t <= dtime(15, 30)


def is_close_auction_time(now: datetime | None = None) -> bool:
    """集合竞价时段 9:15-9:25"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return dtime(9, 15) <= now.time() <= dtime(9, 25)


def now_in_trade_window() -> bool:
    """广义可处理窗口：含集合竞价 + 盘中正午休息 + 收盘后 30 分钟（盘后数据可用）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 0) <= t <= dtime(15, 30)


# ═══════════════════════════════════════════════════════
# 数据源自愈：失败重试 + 多源切换
# ═══════════════════════════════════════════════════════
# 连续失败 N 次后冷却 5 分钟，自动切到备选源；
# 冷却期满后会再次探测，连续 N 次成功后再恢复正常优先级。
SOURCE_HEALTHY_THRESHOLD = 5
SOURCE_COOLDOWN_SEC = 300
SOURCE_RECOVER_THRESHOLD = 3

_source_health = {
    "akshare_em": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "东方财富(akshare)"},
    "akshare_spot": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "akshare全市场"},
    "tencent_qq": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "腾讯(qt.gtimg)"},
    "tencent_ifzq": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "腾讯(web.ifzq)"},
    "sina_hq":    {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "新浪(hq.sinajs)"},
    "em_push2delay": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "东财push2delay"},
    "em_push2his": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "东财push2his"},
    "sina_realtime": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "新浪历史"},
    "netease_163": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "网易财经"},
    "ths_10jqka": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "同花顺"},
    "yahoo_finance": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "Yahoo"},
    "em_h5api": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "东财H5"},
    "xueqiu_kline": {"fails": 0, "oks": 0, "disabled_until": 0.0, "name": "雪球"},
}
_source_lock = Lock()


def reset_source_health():
    """强制重置所有数据源冷却状态（管理员/调试用）。"""
    with _source_lock:
        for k, v in _source_health.items():
            v["fails"] = 0
            v["oks"] = 0
            v["disabled_until"] = 0.0
    return {"reset": True, "sources": list(_source_health.keys())}


def _is_disabled(src: str) -> bool:
    return time.time() < _source_health[src]["disabled_until"]


def _report_fail(src: str, err: str = ""):
    h = _source_health[src]
    h["fails"] += 1
    h["oks"] = 0
    if h["fails"] >= SOURCE_HEALTHY_THRESHOLD and h["disabled_until"] == 0.0:
        h["disabled_until"] = time.time() + SOURCE_COOLDOWN_SEC
        logging.warning(
            f"⚠️ 数据源[{h['name']}]连续{h['fails']}次失败，"
            f"冷却 {SOURCE_COOLDOWN_SEC}s，后续请求自动切备选源"
            + (f" | err={err}" if err else "")
        )


def _report_ok(src: str):
    h = _source_health[src]
    h["oks"] += 1
    h["fails"] = 0
    if h["disabled_until"] > 0 and h["oks"] >= SOURCE_RECOVER_THRESHOLD:
        logging.info(f"✅ 数据源[{h['name']}]已恢复（连续{h['oks']}次成功）")
        h["disabled_until"] = 0.0


def _health_snapshot() -> str:
    parts = []
    for k, v in _source_health.items():
        status = "❌禁用" if _is_disabled(k) else "✅正常"
        remain = ""
        if v["disabled_until"] > 0:
            remain = f" (剩{max(0, int(v['disabled_until']-time.time()))}s)"
        parts.append(f"{v['name']}: {status}{remain} 失败{v['fails']}次")
    return " | ".join(parts)


# ═══════════════════════════════════════════════════════
# 单次数据获取（带指数退避重试）
# ═══════════════════════════════════════════════════════
def _fetch_with_retry(fn, code: str, src_name: str, max_retry: int = 3):
    """
    单源内重试：失败间隔 0.5s, 1s, 2s。
    返回 (success: bool, data, err_msg)
    """
    last_err = ""
    for i in range(max_retry):
        try:
            data = fn(code)
            if data is None:
                last_err = "返回None"
            else:
                return True, data, ""
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if i < max_retry - 1:
            time.sleep(0.5 * (2 ** i))
    return False, None, last_err


# ═══════════════════════════════════════════════════════
# 多源日线
# ═══════════════════════════════════════════════════════
def _daily_akshare(code: str, days: int):
    """源1: akshare 东方财富（前复权日线）"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(
        symbol=code, period="daily",
        start_date=start, end_date=end, adjust="qfq",
    )
    if df is None or len(df) == 0:
        return None
    return df.sort_values("日期").reset_index(drop=True).tail(days).reset_index(drop=True)


def _daily_sina(code: str, days: int):
    """
    源2: 新浪历史日线（不复权）。
    接口：https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
    """
    mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    symbol = f"{mkt}{code}"
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": days + 10}
    r = requests.get(url, params=params, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://finance.sina.com.cn/"})
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    import pandas as pd
    rows = []
    for d in data[-days:]:
        rows.append({
            "日期": d.get("day"),
            "开盘": float(d.get("open", 0)),
            "最高": float(d.get("high", 0)),
            "最低": float(d.get("low", 0)),
            "收盘": float(d.get("close", 0)),
            "成交量": float(d.get("volume", 0)),
            "成交额": 0.0,
            "涨跌幅": 0.0,
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["涨跌幅"] = df["收盘"].pct_change() * 100
    df["涨跌幅"] = df["涨跌幅"].fillna(0)
    return df.reset_index(drop=True)


def _daily_tencent(code: str, days: int):
    """
    源3: 腾讯前复权日线（最稳定，2026-07 东财 push2 限频后切换至此）
    接口：https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600000,day,,,N,qfq
    每只股票单独请求（腾讯不支持批量），用 ThreadPool 并行由调用方控制。
    """
    mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    symbol = f"{mkt}{code}"
    # 要 2x 倍数保险（周末/节假日裁掉一部分）
    n = max(days + 10, 60)
    params = [("param", f"{symbol},day,,,{n},qfq")]
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    r = requests.get(url, params=params, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://gu.qq.com/"})
    r.raise_for_status()
    import pandas as pd
    d = r.json()
    if not d or d.get("code") != 0:
        return None
    arr = (d.get("data") or {}).get(symbol, {}).get("qfqday", [])
    if not arr:
        return None
    rows = []
    for x in arr[-days:]:
        # 格式: [日期, 开盘, 收盘, 最高, 最低, 成交量]  成交量单位是"手"（1手=100股）
        try:
            date_s, op, cl, hi, lo, vol = x[0], x[1], x[2], x[3], x[4], x[5]
            op, cl, hi, lo, vol = float(op), float(cl), float(hi), float(lo), float(vol)
        except (TypeError, ValueError, IndexError):
            continue
        rows.append({
            "日期": pd.to_datetime(date_s),
            "开盘": op, "收盘": cl, "最高": hi, "最低": lo,
            "成交量": vol,
            "成交额": op * vol * 100,  # 元 = 价格 × 手 × 100股/手
            "涨跌幅": 0.0,  # 后算
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["涨跌幅"] = df["收盘"].pct_change() * 100
    df["涨跌幅"] = df["涨跌幅"].fillna(0)
    return df.reset_index(drop=True)


def _daily_eastmoney_push2delay(code: str, days: int):
    """
    源4: 东财 push2delay 直连日线（push2 限频时的备用）。
    接口：https://push2delay.eastmoney.com/api/qt/stock/kline/get
    """
    market = "1" if code.startswith(("6", "9", "5")) else "0"
    secid = f"{market}.{code}"
    n = max(days + 10, 60)
    url = "https://push2delay.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101, "fqt": 1, "beg": 0, "end": 20500000,
    }
    r = requests.get(url, params=params, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://quote.eastmoney.com/"})
    r.raise_for_status()
    import pandas as pd
    d = r.json()
    klines = ((d.get("data") or {}).get("klines")) or []
    if not klines:
        return None
    rows = []
    for line in klines[-days:]:
        # 格式: "2024-07-02,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率"
        parts = line.split(",")
        if len(parts) < 11:
            continue
        try:
            rows.append({
                "日期": pd.to_datetime(parts[0]),
                "开盘": float(parts[1]), "收盘": float(parts[2]),
                "最高": float(parts[3]), "最低": float(parts[4]),
                "成交量": float(parts[5]), "成交额": float(parts[6]),
                "涨跌幅": float(parts[8]),
            })
        except (ValueError, IndexError):
            continue
    if not rows:
        return None
    return pd.DataFrame(rows).reset_index(drop=True)


# ═══════════════════════════════════════════════════════
# 2026-07-08 Arthur: 数据源全挂 (600519), 加 6 新源兜底
# ═══════════════════════════════════════════════════════
def _daily_sina_realtime(code: str, days: int):
    """源5: 新浪财经历史日线 (hq.sinajs.cn)"""
    mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    symbol = f"{mkt}{code}"
    # 新浪历史接口需要 secid + date range
    scale = 240  # 日线
    datalen = max(days + 20, 90)
    url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var=loadHistory/{symbol}?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"
    r = requests.get(url, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    r.raise_for_status()
    text = r.text
    # 去掉 JSONP 包裹
    if "(" in text and text.rstrip().endswith(")"):
        text = text[text.index("(")+1:text.rstrip().rindex(")")]
    import pandas as pd
    data = json.loads(text)
    if not data:
        return None
    rows = []
    for x in data[-days:]:
        try:
            rows.append({
                "日期": pd.to_datetime(x["d"]),
                "开盘": float(x["o"]),
                "收盘": float(x["c"]),
                "最高": float(x["h"]),
                "最低": float(x["l"]),
                "成交量": float(x["v"]),
                "成交额": float(x["v"]) * float(x["c"]),  # 估算
                "涨跌幅": 0.0,
            })
        except (KeyError, ValueError, TypeError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["涨跌幅"] = df["收盘"].pct_change() * 100
    df["涨跌幅"] = df["涨跌幅"].fillna(0)
    return df.reset_index(drop=True)


def _daily_163(code: str, days: int):
    """源6: 网易财经日线 (api.money.126.net)"""
    mkt = "0" if code.startswith(("6", "9", "5")) else "1"
    symbol = f"{mkt}{code}"
    url = f"https://api.money.126.net/data/feed/{symbol},money.api?callback=callback"
    r = requests.get(url, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://money.163.com/"})
    r.raise_for_status()
    text = r.text
    if "(" in text and ")" in text:
        text = text[text.index("(")+1:text.rindex(")")]
    import pandas as pd
    data = json.loads(text)
    # 网易日线在 data["data"][0]["klines"] 或 data[symbol]["klines"]
    klines = []
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, dict) and "klines" in v:
                klines = v["klines"]
                break
    if not klines:
        return None
    rows = []
    for x in klines[-days:]:
        try:
            parts = x.split(",")
            if len(parts) < 6:
                continue
            rows.append({
                "日期": pd.to_datetime(parts[0]),
                "开盘": float(parts[1]),
                "收盘": float(parts[2]),
                "最高": float(parts[3]),
                "最低": float(parts[4]),
                "成交量": float(parts[5]),
                "成交额": float(parts[5]) * float(parts[2]),
                "涨跌幅": float(parts[7]) if len(parts) > 7 else 0.0,
            })
        except (ValueError, IndexError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["涨跌幅"] = df["涨跌幅"].fillna(0)
    return df.reset_index(drop=True)


def _daily_ths(code: str, days: int):
    """源7: 同花顺日线 (basic.10jqka.com.cn)"""
    mkt = "1" if code.startswith(("6", "9", "5")) else "0"
    symbol = f"{mkt}.{code}"
    # 同花顺 K线接口
    n = max(days + 20, 90)
    url = f"https://basic.10jqka.com.cn/api/stock/{symbol}/kline?limit={n}&fields=open,close,high,low,volume,amount,change"
    r = requests.get(url, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://basic.10jqka.com.cn/"})
    r.raise_for_status()
    import pandas as pd
    data = r.json()
    klines = data.get("data", {}).get("kline") or data.get("kline") or []
    if not klines:
        return None
    rows = []
    for x in klines[-days:]:
        try:
            rows.append({
                "日期": pd.to_datetime(x.get("date") or x.get("time")),
                "开盘": float(x["open"]),
                "收盘": float(x["close"]),
                "最高": float(x["high"]),
                "最低": float(x["low"]),
                "成交量": float(x["volume"]),
                "成交额": float(x.get("amount", 0)),
                "涨跌幅": float(x.get("change", 0)),
            })
        except (KeyError, ValueError, TypeError):
            continue
    if not rows:
        return None
    return pd.DataFrame(rows).reset_index(drop=True)


def _daily_yahoo(code: str, days: int):
    """源8: Yahoo Finance 日线 (query1.finance.yahoo.com)"""
    # A股 Yahoo 用 .SS / .SZ 后缀
    if code.startswith(("6", "9", "5")):
        symbol = f"{code}.SS"
    else:
        symbol = f"{code}.SZ"
    # Unix timestamp
    import time as _t
    end = int(_t.time())
    start = end - days * 86400 * 2  # 给节假日缓冲
    url = f"https://query1.finance.yahoo.com/v7/finance/download/{symbol}?period1={start}&period2={end}&interval=1d&events=history"
    r = requests.get(url, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.yahoo.com/"})
    r.raise_for_status()
    import pandas as pd
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    if df.empty:
        return None
    df = df.tail(days).reset_index(drop=True)
    df["日期"] = pd.to_datetime(df["Date"])
    df = df.rename(columns={"Open":"开盘","Close":"收盘","High":"最高","Low":"最低","Volume":"成交量","Adj Close":"AdjClose"})
    df["涨跌幅"] = df["收盘"].pct_change() * 100
    df["涨跌幅"] = df["涨跌幅"].fillna(0)
    df["成交额"] = df["AdjClose"] * df["成交量"]
    return df[["日期","开盘","收盘","最高","最低","成交量","成交额","涨跌幅"]]


def _daily_eastmoney_h5(code: str, days: int):
    """源9: 东财 H5 接口 (push2his.eastmoney.com)"""
    mkt = "1" if code.startswith(("6", "9", "5")) else "0"
    symbol = f"{mkt}.{code}"
    n = max(days + 20, 90)
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": symbol,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",  # 日线
        "fqt": "1",    # 前复权
        "beg": "0",
        "end": "20500101",
        "lmt": str(n),
    }
    r = requests.get(url, params=params, timeout=4,
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    r.raise_for_status()
    import pandas as pd
    data = r.json()
    klines = (data.get("data") or {}).get("klines") or []
    if not klines:
        return None
    rows = []
    for x in klines[-days:]:
        try:
            parts = x.split(",")
            if len(parts) < 6:
                continue
            rows.append({
                "日期": pd.to_datetime(parts[0]),
                "开盘": float(parts[1]),
                "收盘": float(parts[2]),
                "最高": float(parts[3]),
                "最低": float(parts[4]),
                "成交量": float(parts[5]),
                "成交额": float(parts[6]) if len(parts) > 6 else 0,
                "涨跌幅": float(parts[8]) if len(parts) > 8 else 0.0,
            })
        except (ValueError, IndexError):
            continue
    if not rows:
        return None
    return pd.DataFrame(rows).reset_index(drop=True)


def _daily_xueqiu(code: str, days: int):
    """源10: 雪球日线 (stock.xueqiu.com)"""
    mkt = "SH" if code.startswith(("6", "9", "5")) else "SZ"
    symbol = f"{mkt}{code}"
    n = max(days + 20, 90)
    # 雪球需要先取 begin / end 时间戳
    import time as _t
    end = int(_t.time() * 1000)
    start = end - days * 86400 * 1000 * 2
    url = "https://stock.xueqiu.com/v5/stock/chart/kline.json"
    params = {
        "symbol": symbol,
        "begin": str(start),
        "period": "day",
        "type": "before",
        "count": str(n),
        "indicator": "kline",
    }
    r = requests.get(url, params=params, timeout=4,
                     headers={
                         "User-Agent": "Mozilla/5.0",
                         "Referer": "https://xueqiu.com/",
                         "Cookie": "device_id=test",  # 雪球基本都失败, 但还是试试
                     })
    r.raise_for_status()
    import pandas as pd
    data = r.json()
    cols = (data.get("data") or {}).get("column") or []
    items = (data.get("data") or {}).get("item") or []
    if not cols or not items:
        return None
    # 找各列索引
    idx = {name: cols.index(name) if name in cols else -1 for name in ["timestamp","open","close","high","low","volume","amount","change"]}
    rows = []
    for x in items[-days:]:
        try:
            import datetime as _dt
            ts = int(x[idx["timestamp"]]) / 1000
            rows.append({
                "日期": pd.to_datetime(_dt.datetime.fromtimestamp(ts)),
                "开盘": float(x[idx["open"]]),
                "收盘": float(x[idx["close"]]),
                "最高": float(x[idx["high"]]),
                "最低": float(x[idx["low"]]),
                "成交量": float(x[idx["volume"]]),
                "成交额": float(x[idx["amount"]]) if idx["amount"] >= 0 else 0,
                "涨跌幅": float(x[idx["change"]]) if idx["change"] >= 0 else 0.0,
            })
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    if not rows:
        return None
    return pd.DataFrame(rows).reset_index(drop=True)


_DAILY_SOURCES = [
    ("tencent_qq", _daily_tencent),  # 2026-07 起最稳定
    ("em_push2delay", _daily_eastmoney_push2delay),
    ("akshare_em", _daily_akshare),
    ("sina_hq", _daily_sina),
    ("sina_realtime", _daily_sina_realtime),
    ("netease_163", _daily_163),
    ("ths_10jqka", _daily_ths),
    ("yahoo_finance", _daily_yahoo),
    ("em_h5api", _daily_eastmoney_h5),
    ("xueqiu_kline", _daily_xueqiu),
]


def fetch_daily(code: str, days: int = 120):
    """
    多源获取日线：按顺序尝试，主源失败自动切备选。
    每个源内部 3 次重试（0.5/1/2s 退避）；连续 5 次失败冷却 5 分钟。
    返回 DataFrame 或 None。
    """
    with _source_lock:
        last_err = ""
        tried = []
        for src_name, fn in _DAILY_SOURCES:
            if _is_disabled(src_name):
                tried.append(f"{src_name}=跳过")
                continue
            # 用 lambda 把 days 绑定到源函数上
            ok, data, err = _fetch_with_retry(lambda c: fn(c, days), code, src_name, max_retry=3)
            tried.append(f"{src_name}={'ok' if ok else 'fail'}")
            if ok and data is not None and len(data) > 0:
                _report_ok(src_name)
                return data
            _report_fail(src_name, err)
            last_err = err
        logging.warning(f"日线 {code} 全部源失败 | 尝试链={' -> '.join(tried)} | 最后err={last_err}")
        if random.random() < 0.1:
            logging.warning(f"📊 数据源健康: {_health_snapshot()}")
        return None


# ═══════════════════════════════════════════════════════
# 大盘环境判定（2026-07-03 Arthur：解决 5 月震荡市失血）
# ═══════════════════════════════════════════════════════
def _index_tencent(code: str, days: int) -> "pd.DataFrame | None":
    """指数源 1: 腾讯前复权日线（最稳定）"""
    import pandas as pd
    symbol_map = {
        "000300": "sh000300", "000905": "sh000905", "000852": "sh000852",
        "399006": "sz399006", "399001": "sz399001",
        "000001": "sh000001", "000688": "sh000688",
    }
    symbol = symbol_map.get(code, f"sh{code}")
    try:
        # 腾讯不复权接口, 简单可靠
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{days},qfq"
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return None
        j = r.json()
        klines = j.get("data", {}).get(symbol, {}).get("qfqday") or j.get("data", {}).get(symbol, {}).get("day")
        if not klines:
            return None
        rows = []
        for k in klines:
            # 格式: [日期, 开, 收, 高, 低, 成交量, ...]
            if len(k) < 6:
                continue
            rows.append({
                "日期": k[0], "开盘": float(k[1]), "收盘": float(k[2]),
                "最高": float(k[3]), "最低": float(k[4]),
                "成交量": float(k[5]) if k[5] else 0,
            })
        if not rows:
            return None
        return pd.DataFrame(rows).tail(days).reset_index(drop=True)
    except Exception as e:
        logging.debug(f"tencent index {code} 失败: {e}")
        return None


def _index_sina(code: str, days: int) -> "pd.DataFrame | None":
    """指数源 2: 新浪历史日线"""
    import pandas as pd
    symbol_map = {
        "000300": "sh000300", "000905": "sh000905", "000852": "sh000852",
        "399006": "sz399006", "399001": "sz399001",
        "000001": "sh000001", "000688": "sh000688",
    }
    symbol = symbol_map.get(code, f"sh{code}")
    try:
        url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return None
        j = r.json()
        if not j:
            return None
        rows = []
        for k in j:
            if len(k) < 6:
                continue
            rows.append({
                "日期": k[0], "开盘": float(k[1]), "收盘": float(k[2]),
                "最高": float(k[5]) if len(k) > 5 else float(k[3]),
                "最低": float(k[4]) if len(k) > 4 else 0,
                "成交量": 0,
            })
        if not rows:
            return None
        return pd.DataFrame(rows).tail(days).reset_index(drop=True)
    except Exception as e:
        logging.debug(f"sina index {code} 失败: {e}")
        return None


def _index_em_push2(code: str, days: int) -> "pd.DataFrame | None":
    """指数源 3: 东财 push2delay 直连"""
    import pandas as pd
    # 沪深 1.xxxxx, 深证 0.xxxxx
    if code.startswith("399") or code.startswith("39"):
        secid = f"0.{code}"
    else:
        secid = f"1.{code}"
    try:
        url = (
            f"https://push2delay.eastmoney.com/api/qt/stock/kline/get?"
            f"secid={secid}&fields1=f1,f2,f3,f4,f5"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
            f"&klt=101&fqt=1&beg=0&end=20500101"
        )
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return None
        j = r.json()
        klines = j.get("data", {}).get("klines")
        if not klines:
            return None
        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            rows.append({
                "日期": parts[0], "开盘": float(parts[1]), "收盘": float(parts[2]),
                "最高": float(parts[3]), "最低": float(parts[4]),
                "成交量": float(parts[5]),
            })
        if not rows:
            return None
        return pd.DataFrame(rows).tail(days).reset_index(drop=True)
    except Exception as e:
        logging.debug(f"em_push2 index {code} 失败: {e}")
        return None


def _index_akshare(code: str, days: int) -> "pd.DataFrame | None":
    """指数源 4 (兜底): akshare stock_zh_index_daily"""
    import pandas as pd
    symbol_map = {
        "000300": "sh000300", "000905": "sh000905", "000852": "sh000852",
        "399006": "sz399006", "399001": "sz399001",
    }
    symbol = symbol_map.get(code, f"sh{code}")
    try:
        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is None or len(df) == 0:
            return None
        rename = {"date": "日期", "close": "收盘", "open": "开盘",
                  "high": "最高", "low": "最低", "volume": "成交量"}
        df = df.rename(columns=rename)
        if "成交量" not in df.columns:
            df["成交量"] = 0
        return df.tail(days).reset_index(drop=True)
    except Exception as e:
        logging.debug(f"akshare index {code} 失败: {e}")
        return None


def fetch_index_daily(code: str = "000300", days: int = 30) -> "pd.DataFrame | None":
    """
    多源热备获取大盘指数日线（默认沪深300 000300）。
    顺序: tencent_qq → em_push2delay → sina_hq → akshare
    返回 DataFrame 含 日期/开盘/收盘/最高/最低/成交量 列。
    """
    sources = [
        ("tencent_qq", _index_tencent),
        ("em_push2delay", _index_em_push2),
        ("sina_hq", _index_sina),
        ("akshare_em", _index_akshare),
    ]
    last_err = ""
    for src_name, fn in sources:
        try:
            df = fn(code, days)
            if df is not None and len(df) > 0:
                return df
            last_err = f"{src_name}=empty"
        except Exception as e:
            last_err = f"{src_name}={e}"
    logging.warning(f"指数 {code} 全部源失败 | 最后err={last_err}")
    return None


def get_market_regime() -> dict:
    """
    大盘环境判定（返回用于龙头策略过滤的状态）

    判定逻辑：
    1. 沪深 300 (000300) 收盘 > MA20 → bull（可推主升浪）
    2. 沪深 300 收盘 < MA20 且 > MA60 → bear（震荡市，不推主升浪）
    3. 沪深 300 收盘 < MA60 → crash（清仓观望）
    4. 数据源失败 → unknown（保守，不推主升浪）

    返回:
    {
      "regime": "bull" | "bear" | "crash" | "unknown",
      "close": float,
      "ma20": float,
      "ma60": float,
      "score_threshold": int,  # 推荐及格分：bull=70, bear=95, crash=999
      "allow_dragon": bool,    # 是否允许推送龙头
    }
    """
    try:
        df = fetch_index_daily("000300", days=60)
        if df is None or len(df) < 25:
            return {
                "regime": "unknown",
                "score_threshold": 95,
                "allow_dragon": False,
                "note": "数据缺失，保守不推",
            }
        calc_indicators(df, ma_periods=(5, 10, 20, 60))
        last = df.iloc[-1]
        close = float(last["收盘"])
        ma20 = float(last.get("MA20", 0)) if _pd_notnull(last.get("MA20", 0)) else 0
        ma60 = float(last.get("MA60", 0)) if _pd_notnull(last.get("MA60", 0)) else 0

        if ma20 <= 0 or ma60 <= 0:
            return {
                "regime": "unknown", "close": close, "ma20": ma20, "ma60": ma60,
                "score_threshold": 95, "allow_dragon": False,
                "note": "MA 计算失败，保守不推",
            }

        if close > ma20:
            regime = "bull"
            score_threshold = 70
            allow = True
        elif close > ma60:
            regime = "bear"
            score_threshold = 95  # 震荡市只推最强
            allow = True
        else:
            regime = "crash"
            score_threshold = 999
            allow = False

        return {
            "regime": regime,
            "close": close,
            "ma20": ma20,
            "ma60": ma60,
            "score_threshold": score_threshold,
            "allow_dragon": allow,
            "note": f"沪深300 {close:.0f} vs MA20 {ma20:.0f} vs MA60 {ma60:.0f}",
        }
    except Exception as e:
        logging.warning(f"大盘环境判定失败: {e}")
        return {
            "regime": "unknown",
            "score_threshold": 95,
            "allow_dragon": False,
            "note": f"异常: {e}",
        }


# ═══════════════════════════════════════════════════════
# 多源实时行情
# ═══════════════════════════════════════════════════════
def _realtime_tencent(code: str):
    """源1: 腾讯 qt.gtimg.cn（实时全字段）"""
    mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    url = f"https://qt.gtimg.cn/q={mkt}{code}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
    except Exception as e:
        logging.debug(f"tencent realtime {code} 网络失败: {e}")
        return None
    if r.status_code != 200:
        return None
    raw = r.content.decode("gbk", errors="ignore")
    if "=" not in raw or "\"" not in raw:
        return None
    body = raw.split("\"")[1]
    fields = body.split("~")
    # 字段顺序参考: 1=名字,2=代码,3=现价,4=昨收,5=今开,6=成交量(手),
    # 30=时间,31=涨跌额,32=涨跌幅,33=最高,34=最低,37=成交额(万)
    if len(fields) < 40:
        return None
    try:
        return {
            "最新价": float(fields[3]) if fields[3] else 0,
            "今开":   float(fields[5]) if fields[5] else 0,
            "昨收":   float(fields[4]) if fields[4] else 0,
            "最高":   float(fields[33]) if fields[33] else 0,
            "最低":   float(fields[34]) if fields[34] else 0,
            "涨跌幅": float(fields[32]) if fields[32] else 0,
            "成交量": float(fields[6]) * 100 if fields[6] else 0,
            "成交额": float(fields[37]) * 10000 if fields[37] else 0,
            "时间":   fields[30],
        }
    except (ValueError, IndexError):
        return None


def _realtime_sina(code: str):
    """源2: 新浪 hq.sinajs.cn（实时行情）"""
    mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    url = f"https://hq.sinajs.cn/list={mkt}{code}"
    try:
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
        }, timeout=8)
    except Exception as e:
        logging.debug(f"sina realtime {code} 网络失败: {e}")
        return None
    if r.status_code != 200:
        return None
    raw = r.content.decode("gbk", errors="ignore")
    if "=\"" not in raw:
        return None
    body = raw.split("\"")[1]
    fields = body.split(",")
    if len(fields) < 32:
        return None
    try:
        # 0=名字,1=今开,2=昨收,3=现价,4=最高,5=最低,
        # 6=买入价,7=卖出价,8=成交量,9=成交额
        return {
            "最新价": float(fields[3]) if fields[3] else 0,
            "今开":   float(fields[1]) if fields[1] else 0,
            "昨收":   float(fields[2]) if fields[2] else 0,
            "最高":   float(fields[4]) if fields[4] else 0,
            "最低":   float(fields[5]) if fields[5] else 0,
            "涨跌幅": (float(fields[3]) - float(fields[2])) / float(fields[2]) * 100 if fields[2] else 0,
            "成交量": float(fields[8]) if fields[8] else 0,
            "成交额": float(fields[9]) if fields[9] else 0,
            "时间":   f"{fields[30]} {fields[31]}",
        }
    except (ValueError, IndexError):
        return None


def _realtime_akshare(code: str):
    """源3: akshare hist_min_em（盘中分钟线最后一根）"""
    df = ak.stock_zh_a_hist_min_em(symbol=code, period="1", adjust="qfq")
    if df is None or len(df) == 0:
        return None
    last = df.iloc[-1]
    return {
        "最新价": float(last["收盘"]),
        "今开":   float(last["开盘"]),
        "最高":   float(last["最高"]),
        "最低":   float(last["最低"]),
        "成交量": float(last["成交量"]),
        "成交额": float(last["成交额"]),
        "时间":   str(last.get("时间", "")),
    }


def _realtime_em_push2(code: str):
    """源3 (新): 东财 push2 实时快照 (备选, 抗 ban)"""
    try:
        if code.startswith(("6", "9", "5")):
            secid = f"1.{code}"
        else:
            secid = f"0.{code}"
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={secid}&fields=f43,f44,f45,f46,f47,f48,f60,f169,f170,"
            f"f171,f168,f50,f167,f117,f292"
        )
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code != 200:
            return None
        j = r.json()
        d = j.get("data") or {}
        if not d:
            return None
        price = d.get("f43", 0) / 100
        last_close = d.get("f60", 0) / 100
        if not price or not last_close:
            return None
        return {
            "最新价": price,
            "今开":   d.get("f46", 0) / 100,
            "昨收":   last_close,
            "最高":   d.get("f44", 0) / 100,
            "最低":   d.get("f45", 0) / 100,
            "涨跌幅": (price - last_close) / last_close * 100,
            "成交量": d.get("f47", 0),
            "成交额": d.get("f48", 0),
            "时间":   time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        logging.debug(f"em_push2 realtime {code} 失败: {e}")
        return None


def _realtime_tencent_ifzq(code: str):
    """源4 (新): 腾讯 web.ifzq 实时快照 (qt 字段全字段)
    注: 不用此源, 巨慢且返回结构复杂, 改用 tencent_qq (qt.gtimg.cn) 即可
    """
    return None  # 禁用, 走 tencent_qq (qt.gtimg.cn) 0.1s 就能拿到


def _realtime_em_push2his(code: str):
    """源5 (新): 东财 push2his 1分钟K线最后一根 = 当前价 (抗 ban 兜底)"""
    try:
        if code.startswith(("6", "9", "5")):
            secid = f"1.{code}"
        else:
            secid = f"0.{code}"
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt=1&fqt=1&beg=0&end=20500101"
        )
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code != 200:
            return None
        j = r.json()
        klines = j.get("data", {}).get("klines")
        if not klines:
            return None
        last = klines[-1].split(",")
        if len(last) < 6:
            return None
        # 格式: 日期,开,收,高,低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
        return {
            "最新价": float(last[2]),
            "今开":   float(last[1]),
            "昨收":   float(last[2]) - float(last[9]) if len(last) > 9 else 0,
            "最高":   float(last[3]),
            "最低":   float(last[4]),
            "涨跌幅": float(last[8]) if len(last) > 8 else 0,
            "成交量": float(last[5]),
            "成交额": float(last[6]) if len(last) > 6 else 0,
            "时间":   last[0],
        }
    except Exception as e:
        logging.debug(f"em_push2his realtime {code} 失败: {e}")
        return None


def _realtime_akshare_sina(code: str):
    """源6 (新): akshare stock_zh_a_spot_em 列表查询 (终极兜底)"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is None or len(df) == 0:
            return None
        row = df[df["代码"] == code]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            "最新价": float(r.get("最新价", 0)),
            "今开":   float(r.get("今开", 0)),
            "昨收":   float(r.get("昨收", 0)),
            "最高":   float(r.get("最高", 0)),
            "最低":   float(r.get("最低", 0)),
            "涨跌幅": float(r.get("涨跌幅", 0)),
            "成交量": float(r.get("成交量", 0)),
            "成交额": float(r.get("成交额", 0)),
            "时间":   time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        logging.debug(f"akshare_spot realtime {code} 失败: {e}")
        return None


_REALTIME_SOURCES = [
    ("tencent_qq",       _realtime_tencent),         # 源1: 腾讯 qt.gtimg (0.1s)
    ("tencent_ifzq",     _realtime_tencent_ifzq),   # 源2: 腾讯 web.ifzq (0.1s)
    ("em_push2his",      _realtime_em_push2his),    # 源3: 东财 push2his K线 (抗 ban)
    ("em_push2delay",         _realtime_em_push2),       # 源4: 东财 push2 (偶尔被 ban)
    ("sina_hq",          _realtime_sina),           # 源5: 新浪 hq.sinajs (经常 timeout)
    ("akshare_spot",     _realtime_akshare_sina),   # 源6: akshare 全市场查表 (4-8s, 终极兜底)
    ("akshare_em",       _realtime_akshare),        # 源7: akshare hist_min
]


def _index_realtime_em(code: str) -> dict | None:
    """指数实时行情（东财 push2，2026-07 起频繁返回空，作为 fallback 保留）"""
    try:
        if code.startswith(("399", "39")):
            secid = f"0.{code}"
        else:
            secid = f"1.{code}"
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={secid}&fields=f43,f44,f45,f46,f47,f48,f60,f169,f170,"
            f"f171,f168,f50,f167,f117,f292"
        )
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code != 200:
            return None
        j = r.json()
        d = j.get("data") or {}
        if not d:
            return None
        price = d.get("f43", 0) / 100
        last_close = d.get("f60", 0) / 100
        if not price or not last_close:
            return None
        return {
            "最新价": price,
            "今开":   d.get("f46", 0) / 100,
            "昨收":   last_close,
            "最高":   d.get("f44", 0) / 100,
            "最低":   d.get("f45", 0) / 100,
            "涨跌幅": (price - last_close) / last_close * 100,
            "成交量": d.get("f47", 0),
            "成交额": d.get("f48", 0),
            "时间":   time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        logging.debug(f"em index realtime {code} 失败: {e}")
        return None


def _index_realtime_qq(code: str) -> dict | None:
    """指数实时行情（腾讯 qt.gtimg，正确处理指数前缀）

    2026-07 切换：东财 push2 接口持续返回空（被 ban），十准的前缀规则对
    000xxx 指数会拼成 sz000xxx 拿到股票而非指数。这里直接走腾讯 qt.gtimg，
    字段顺序与个股一致：3=现价, 4=昨收, 5=今开, 6=成交量(手),
    30=时间, 32=涨跌幅, 33=最高, 34=最低, 37=成交额(万)。
    """
    try:
        # 指数前缀：000xxx → sh (上证/沪深300/科创50/上证50)，
        #         399xxx → sz (深证成指/创业板/中证500/深证100)
        mkt = "sh" if code.startswith("000") else "sz"
        url = f"https://qt.gtimg.cn/q={mkt}{code}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        if r.status_code != 200:
            return None
        raw = r.content.decode("gbk", errors="ignore")
        if "=" not in raw or '"' not in raw:
            return None
        body = raw.split('"')[1]
        fields = body.split("~")
        if len(fields) < 40:
            return None
        price = float(fields[3]) if fields[3] else 0
        last_close = float(fields[4]) if fields[4] else 0
        if not price or not last_close:
            return None
        return {
            "最新价": price,
            "今开":   float(fields[5]) if fields[5] else 0,
            "昨收":   last_close,
            "最高":   float(fields[33]) if fields[33] else 0,
            "最低":   float(fields[34]) if fields[34] else 0,
            "涨跌幅": float(fields[32]) if fields[32] else 0,
            "成交量": float(fields[6]) * 100 if fields[6] else 0,
            "成交额": float(fields[37]) * 10000 if fields[37] else 0,
            "时间":   fields[30],
        }
    except Exception as e:
        logging.debug(f"qq index realtime {code} 失败: {e}")
        return None


def fetch_realtime(code: str) -> dict | None:
    """
    多源实时行情：腾讯 → 东财 → 新浪 → akshare 分钟线。
    每源 3 次重试（0.5/1/2s），连续失败 5 次冷却 5 分钟。
    akshare hist_min_em 在盘后仍能拿到当天最后一根分钟线（11:30 / 15:00），
    保证盘中午休也有"今日收盘价"，不会误用昨收。
    """
    # 指数代码（000xxx / 399xxx）走专用指数实时源
    # 顺序：腾讯 qt.gtimg（最快，正确处理指数前缀）→ 东财 push2（历史上更全但 2026-07 被 ban）
    # → 新浪 hq.sinajs → 复用下面的个股多源循环兜底
    if (len(code) == 6 and
        (code.startswith("000") or code.startswith("399") or
         code.startswith("399") or code in ("000300", "000905", "000852",
                                            "399006", "399001", "000001", "000688"))):
        for label, fn in (
            ("tencent_qq_index",  _index_realtime_qq),
            ("em_push2delay_idx", _index_realtime_em),
        ):
            try:
                rt = fn(code)
            except Exception:
                rt = None
            if rt:
                rt["_source"] = label
                rt["_fetch_time"] = time.strftime("%H:%M:%S")
                return rt
        # 指数专用源都失败 → 继续走下面的个股多源循环（含 sina/akshare）
        # 注意：_realtime_tencent/_realtime_sina 对 000xxx 指数前缀是错的，
        # 但万一主路径全挂，至少 akshare hist_min_em 还能兜底拿到当日收盘价
    with _source_lock:
        last_err = ""
        tried = []
        for src_name, fn in _REALTIME_SOURCES:
            if _is_disabled(src_name):
                tried.append(f"{src_name}=跳过")
                continue
            ok, data, err = _fetch_with_retry(fn, code, src_name, max_retry=3)
            tried.append(f"{src_name}={'ok' if ok else 'fail'}")
            if ok and data is not None:
                _report_ok(src_name)
                # 加个时间标记：调用方用 data["数据时段"] 判断"实时/上午收盘/午休"
                data["_source"] = src_name
                data["_fetch_time"] = time.strftime("%H:%M:%S")
                return data
            _report_fail(src_name, err)
            last_err = err
        logging.warning(f"实时 {code} 全部源失败 | 尝试链={' -> '.join(tried)} | err={last_err}")
        return None


def fetch_realtime_change(code: str) -> float:
    """
    多源今日涨跌幅（按实时价/昨收算），优先从 fetch_realtime 取。
    """
    rt = fetch_realtime(code)
    if rt and rt.get("涨跌幅") is not None:
        return float(rt["涨跌幅"])
    # 兜底：资金流接口
    try:
        market = "sz" if code.startswith(("0", "3", "15")) else "ss"
        fund = ak.stock_individual_fund_flow(stock=code, market=market)
        if fund is not None and len(fund) > 0:
            return float(fund.iloc[-1]["涨跌幅"])
    except Exception:
        pass
    return 0.0


# ═══════════════════════════════════════════════════════
# 主力资金流（东财 push2his）——识别庄家出货/吸筹
# ═══════════════════════════════════════════════════════
def fetch_main_fund_flow(code: str) -> dict | None:
    """
    拉今日资金流（按单笔成交额分类）：
    - 主力净流入占比:  (超大单 + 大单)
    - 超大单净流入占比:  >100 万
    - 大单净流入占比:    20~100 万
    - 中单净流入占比:    4~20 万
    - 小单净流入占比:    <4 万

    返回: {
        'main_pct': 主力净流入占比(%),
        'super_pct': 超大单净流入占比(%),
        'big_pct': 大单净流入占比(%),
        'mid_pct': 中单净流入占比(%),
        'small_pct': 小单净流入占比(%),
    } 或 None
    """
    try:
        secid = f"1.{code}" if code.startswith(("6", "9", "5")) else f"0.{code}"
        url = "https://push2his.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": secid,
            "fields": "f184,f185,f186,f187,f188",
            "invt": 2,
            "fltt": 2,
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        }
        r = requests.get(url, params=params, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        d = r.json().get("data") or {}
        if not d:
            return None
        # f184=主力, f185=超大单, f186=大单, f187=中单, f188=小单（单位：万元）
        return {
            "main_net": float(d.get("f184", 0) or 0),     # 主力净流入（万元）
            "super_net": float(d.get("f185", 0) or 0),   # 超大单净流入
            "big_net": float(d.get("f186", 0) or 0),     # 大单净流入
            "mid_net": float(d.get("f187", 0) or 0),     # 中单净流入
            "small_net": float(d.get("f188", 0) or 0),   # 小单净流入
        }
    except Exception as e:
        logging.debug(f"资金流 {code} 拉取失败: {e}")
        return None


def detect_main_force_exit(code: str, lookback_days: int = 5) -> dict | None:
    """
    主力出仓检测：连续 N 天主力净流出 + 价格上涨（典型派发形态）
    或单日主力净流出超 2000 万 + 股价下跌（砸盘）

    返回:
        {
            'is_exiting': bool,        # 是否在出仓
            'severity': '低'|'中'|'高',  # 严重程度
            'consecutive_out_days': int,  # 连续流出天数
            'total_main_out_5d': float,  # 5日累计主力净流出（万元）
            'today_main_net': float,     # 今日主力净流入（万元）
            'reason': str,                # 触发原因描述
        }
    """
    import urllib.request, urllib.parse
    # 备选域名（防 push2his 被风控）
    eastmoney_urls = [
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get",
    ]
    try:
        import pandas as pd
        # 用东财的历史资金流接口
        secid = f"1.{code}" if code.startswith(("6", "9", "5")) else f"0.{code}"
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "klt": 101,  # 日线
            "lmt": lookback_days,
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        }
        # 备选域名重试（push2his 偶尔被 RST）
        # 代理支持（环境变量 HTTPS_PROXY/HTTP_PROXY/PROXY_ALL）：
        #   - HTTP/HTTPS 代理：http://ip:port 或 http://user:pass@ip:port
        #   - SOCKS5 代理：socks5://ip:port 或 socks5h://user:pass@ip:port
        proxy_url = os.environ.get("PROXY_ALL") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        proxy_handler = None
        if proxy_url:
            if proxy_url.startswith(("socks5", "socks4", "socks")):
                # SOCKS 代理：用 urllib 的 SOCKS handler（PySocks 装好即可）
                try:
                    from urllib.request import build_opener, ProxyHandler
                    # PySocks 会自动注册 schemes
                    proxy_handler = ProxyHandler({
                        'http': proxy_url,
                        'https': proxy_url,
                    })
                except Exception as e:
                    logging.debug(f"  SOCKS 代理初始化失败: {e}")
                    proxy_handler = None
            else:
                # HTTP/HTTPS 代理
                proxy_handler = urllib.request.ProxyHandler({
                    'http': proxy_url,
                    'https': proxy_url,
                })

        klines = None
        urls_to_try = eastmoney_urls
        for base_url in urls_to_try:
            try:
                full = base_url + "?" + urllib.parse.urlencode(params)
                req = urllib.request.Request(full, headers=headers)
                if proxy_handler:
                    opener = urllib.request.build_opener(proxy_handler)
                    with opener.open(req, timeout=10) as resp:
                        raw_data = resp.read().decode("utf-8")
                else:
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        raw_data = resp.read().decode("utf-8")
                d = json.loads(raw_data) if raw_data else {}
                klines = (d.get("data") or {}).get("klines") or []
                if klines:
                    break
            except Exception as e:
                logging.debug(f"  资金流域名 {base_url} 失败: {type(e).__name__}: {e}")
                continue
        if not klines:
            return None
        # 字段：日期, 主力净流入, 主力净占比, 超大单净流入, 超大单净占比, 大单, 中单, 小单
        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 8:
                continue
            rows.append({
                "date": parts[0],
                "main_net": float(parts[1]),     # 主力净流入（元）
                "super_net": float(parts[3]),    # 超大单净流入
                "big_net": float(parts[5]),
                "mid_net": float(parts[6]),
                "small_net": float(parts[7]),
            })
        if not rows:
            return None
        df = pd.DataFrame(rows)

        # 转换：元 → 亿元（除以 1亿）
        df["main_net_yi"] = df["main_net"] / 1e8
        df["super_net_yi"] = df["super_net"] / 1e8
        df["big_net_yi"] = df["big_net"] / 1e8
        df["small_net_yi"] = df["small_net"] / 1e8

        # 1) 连续流出天数
        consecutive = 0
        for v in df["main_net_yi"]:
            if v < 0:
                consecutive += 1
            else:
                break

        # 2) 5日累计主力净流出
        total_out = float(df["main_net_yi"][df["main_net_yi"] < 0].sum())  # 负数（亿元）

        # 3) 今日主力净流入
        today_main = float(df["main_net_yi"].iloc[-1])

        # 4) 散户是否在接盘（小单净流入为正 + 主力净流出）
        small_in = float(df["small_net_yi"].iloc[-1]) > 0

        # 严重度判定
        is_exiting = False
        severity = "低"
        reasons = []

        # A. 连续 ≥3 天主力净流出
        if consecutive >= 3:
            is_exiting = True
            severity = "中" if consecutive < 5 else "高"
            reasons.append(f"主力连续{consecutive}天净流出")

        # B. 5日累计主力净流出 > 0.5 亿
        if total_out < -0.5:
            is_exiting = True
            if abs(total_out) > 2:
                severity = "高"
            elif severity != "高":
                severity = "中"
            reasons.append(f"5日累计主力净流出{abs(total_out):.2f}亿")

        # C. 今日主力单日砸盘 > 0.2 亿
        if today_main < -0.2:
            is_exiting = True
            if abs(today_main) > 0.5 and severity != "高":
                severity = "高"
            reasons.append(f"今日主力砸盘{abs(today_main):.2f}亿")

        # D. 典型派发：主力流出 + 散户接盘
        if today_main < -0.1 and small_in:
            is_exiting = True
            if severity == "低":
                severity = "中"
            reasons.append("主力出货+散户接盘(典型派发)")

        reason = " | ".join(reasons) if reasons else "未检测到主力出仓"
        # 每日资金流明细（2026-07-02 新增）
        daily_breakdown = []
        for _, row in df.iterrows():
            daily_breakdown.append({
                "date": row["date"],
                "main_net": float(row["main_net_yi"]),  # 亿元
                "super_net": float(row["super_net_yi"]),
                "small_net": float(row["small_net_yi"]),
            })
        return {
            "is_exiting": is_exiting,
            "severity": severity,
            "consecutive_out_days": consecutive,
            "total_main_out_5d": total_out,
            "today_main_net": today_main,
            "small_in": small_in,
            "reason": reason,
            "daily_breakdown": daily_breakdown,  # 每日资金流明细
        }
    except Exception as e:
        logging.debug(f"主力出仓检测 {code} 失败: {e}")
        return None


# ═══════════════════════════════════════════════════════
# 技术指标
# ═══════════════════════════════════════════════════════
def calc_indicators(df, ma_periods=(5, 10, 20, 60)):
    """
    输入日线 DataFrame，附加 MA / MACD / KDJ / 量均线。
    原地修改。原 df 列为：日期 / 代码 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额 / 振幅 / 涨跌幅 / 涨跌额 / 换手率。
    """
    if df is None or len(df) < 30:
        return df
    for p in ma_periods:
        if len(df) >= p:
            df[f"MA{p}"] = df["收盘"].rolling(p).mean()

    ema_fast = df["收盘"].ewm(span=12, adjust=False).mean()
    ema_slow = df["收盘"].ewm(span=26, adjust=False).mean()
    df["DIF"] = ema_fast - ema_slow
    df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["MACD"] = (df["DIF"] - df["DEA"]) * 2

    low_n = df["最低"].rolling(9).min()
    high_n = df["最高"].rolling(9).max()
    rsv = (df["收盘"] - low_n) / (high_n - low_n) * 100
    df["K"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    df["D"] = df["K"].ewm(alpha=1 / 3, adjust=False).mean()
    df["J"] = 3 * df["K"] - 2 * df["D"]

    df["VOL_MA5"] = df["成交量"].rolling(5).mean()
    df["VOL_MA20"] = df["成交量"].rolling(20).mean()
    return df


# ═══════════════════════════════════════════════════════
# 通用信号检测
# ═══════════════════════════════════════════════════════
def _pd_notnull(x) -> bool:
    if x is None:
        return False
    try:
        import math
        if isinstance(x, float) and math.isnan(x):
            return False
        return True
    except Exception:
        return False


def detect_ma_cross(df) -> tuple[str | None, str | None]:
    """MA5 与 MA10 交叉：(type, level) e.g. ('MA金叉','买入关注')"""
    if df is None or len(df) < 11:
        return None, None
    last, prev = df.iloc[-1], df.iloc[-2]
    if not (_pd_notnull(last["MA5"]) and _pd_notnull(last["MA10"])):
        return None, None
    if prev["MA5"] <= prev["MA10"] and last["MA5"] > last["MA10"]:
        return "MA金叉", "买入关注"
    if prev["MA5"] >= prev["MA10"] and last["MA5"] < last["MA10"]:
        return "MA死叉", "卖出信号"
    return None, None


def detect_macd_cross(df) -> tuple[str | None, str | None]:
    if df is None or len(df) < 30:
        return None, None
    last, prev = df.iloc[-1], df.iloc[-2]
    if not (_pd_notnull(last["DIF"]) and _pd_notnull(last["DEA"])):
        return None, None
    if prev["DIF"] <= prev["DEA"] and last["DIF"] > last["DEA"]:
        return "MACD金叉", "买入信号"
    if prev["DIF"] >= prev["DEA"] and last["DIF"] < prev["DEA"] and last["DIF"] < last["DEA"]:
        return "MACD死叉", "卖出信号"
    return None, None


def detect_kdj_cross(df, k_threshold_long=80, k_threshold_short=20) -> list[tuple[str, str]]:
    """
    KDJ 真交叉：前日 K<D, 当日 K>D 且 K<k_threshold_short → 超卖金叉
              前日 K>D, 当日 K<D 且 K>k_threshold_long  → 超买死叉
    """
    out = []
    if df is None or len(df) < 15:
        return out
    last, prev = df.iloc[-1], df.iloc[-2]
    if not (_pd_notnull(last["K"]) and _pd_notnull(last["D"])):
        return out
    if prev["K"] <= prev["D"] and last["K"] > last["D"] and last["K"] < k_threshold_short:
        out.append(("KDJ金叉(超卖)", "买入信号"))
    if prev["K"] >= prev["D"] and last["K"] < last["D"] and last["K"] > k_threshold_long:
        out.append(("KDJ死叉(超买)", "卖出信号"))
    return out


def detect_volume_breakout(df, price, vol_today, vol_ma5, breakout_vol_ratio=2.0) -> str | None:
    """放量突破 MA20：量比 ≥ 阈值 + 当日上穿 MA20"""
    if df is None or len(df) < 22 or not _pd_notnull(df["MA20"].iloc[-1]):
        return None
    last, prev = df.iloc[-1], df.iloc[-2]
    if vol_ma5 <= 0:
        return None
    vol_ratio = vol_today / vol_ma5
    if vol_ratio >= breakout_vol_ratio and price > last["MA20"] and prev["收盘"] <= last["MA20"]:
        return f"放量突破MA20 量比{vol_ratio:.2f}"
    return None


def detect_volume_price_divergence(df, price) -> str | None:
    """顶背离：价格接近20日新高，但近期成交明显缩量"""
    if df is None or len(df) < 25:
        return None
    high_20 = float(df["最高"].tail(20).max())
    if price < high_20 * 0.99:
        return None
    recent_vol = float(df["成交量"].tail(5).mean())
    prev_vol = float(df["成交量"].tail(20).iloc[:-5].mean())
    if prev_vol <= 0 or recent_vol >= prev_vol * 0.6:
        return None
    return f"顶背离 价格新高 成交量缩至{recent_vol / prev_vol * 100:.0f}%"


# ═══════════════════════════════════════════════════════
# 持仓管理：移动止损 / 分级预警
# ═══════════════════════════════════════════════════════
def calc_trailing_stop(pnl_pct: float, peak_pnl: float, base_stop: float = -8) -> float:
    """
    移动止损线（回撤对追踪）：
      涨幅 < 5%：固定 -8%
      5–10%：止损线锁 0%
      10–15%：锁 +5%
      ≥15%：锁 +10%
    """
    if peak_pnl >= 15:
        return max(10, pnl_pct - 5)
    if peak_pnl >= 10:
        return max(5, pnl_pct - 5)
    if peak_pnl >= 5:
        return max(0, pnl_pct - 5)
    return base_stop


def load_positions() -> dict:
    """读取 positions.json；不存在则返回空 schema"""
    if POSITIONS_FILE.exists():
        try:
            data = json.loads(POSITIONS_FILE.read_text())
            return data if isinstance(data, dict) else {"positions": {}}
        except Exception as e:
            logging.error(f"positions.json 解析失败: {e}")
    return {"positions": {}}


def save_positions(data: dict):
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSITIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════
# 收盘 / 开盘播报
# ═══════════════════════════════════════════════════════
def market_open_broadcast_window(now: datetime | None = None) -> bool:
    """9:00-9:01 早安播报"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return dtime(9, 0) <= now.time() <= dtime(9, 1)


def market_close_summary_window(now: datetime | None = None) -> bool:
    """15:30-15:40 收盘汇总"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return dtime(15, 30) <= now.time() <= dtime(15, 40)
