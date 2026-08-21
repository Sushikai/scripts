"""web/intraday_workers.py — R135 方向C: _fetch_intraday_for_date 的 4 个 worker 抽

模块抽出原因:
  原 _fetch_intraday_for_date 338 行,内嵌 _push / _ak_parse / _ak_worker /
  _sina_worker / _tencent_worker / _ef_worker / _mark_fast_done 7 个内嵌闭包。
  内嵌闭包共享 out/results/lock/done_early,不易单测, 改 worker 时容易碰状态。

设计:
  所有 workers 都是纯函数 — 接受必需的依赖 (push 回调 / mkt / is_today / ymd) 作为参数,
  返回 None (成功即已 push), 由 caller 集中管理线程与 early-exit 状态。
  避免 web.intraday_workers ↔ web.server 循环 import (依赖注入而非 import)。

用法 (server.py):
  from .intraday_workers import (
      ak_worker, sina_worker, tencent_worker, efinance_worker,
      parse_akshare_intraday,
  )

  # 在 _fetch_intraday_for_date 内启动 4 线程:
  workers = [
      threading.Thread(target=ak_worker, args=(code, is_today, ymd, _push), daemon=True),
      threading.Thread(target=sina_worker, args=(code, mkt, date_str, _push), daemon=True),
      threading.Thread(target=tencent_worker, args=(code, mkt, is_today, ymd, _push), daemon=True),
      threading.Thread(target=efinance_worker, args=(code, ymd, _push), daemon=True),
  ]
"""
from __future__ import annotations
import logging
from typing import Callable, Optional

_log = logging.getLogger(__name__)


def parse_akshare_intraday(df, src_name: str, push_fn: Callable) -> None:
    """akshare DataFrame → push 一组 tick.

    push_fn(src_name, ticks, prev_close) 由 caller 提供 (持有 lock + results).
    """
    tcks = []
    pc = None
    for _, r in df.iterrows():
        from ..lib_common import _safe_float
        tcks.append({
            "time":        str(r.get("时间", "")),
            "price":       _safe_float(r.get("成交价", r.get("收盘", r.get("最新价")))),
            "open":        _safe_float(r.get("开盘", 0)) or None,
            "high":        _safe_float(r.get("最高", 0)) or None,
            "low":         _safe_float(r.get("最低", 0)) or None,
            "volume_hand": _safe_float(r.get("手数", r.get("成交量"))),
            "amount":      _safe_float(r.get("成交额", 0)) or None,
            "side":        str(r.get("买卖盘性质", "")),
        })
        if r.get("昨收") is not None and pc is None:
            pc = _safe_float(r.get("昨收"))
    if tcks:
        push_fn(src_name, tcks, pc)


def ak_worker(code: str, is_today: bool, ymd: str, push_fn: Callable) -> None:
    """akshare tick 抓取 — 今日走 stock_intraday_em, 历史走 stock_zh_a_hist_min_em."""
    # BSE (8/92/43 开头) akshare 走 SSE 拉 tick 会撞 secid 拼错 → RemoteDisconnected 卡死 25s+
    if code.startswith(("8", "92", "43")):
        return
    try:
        import akshare as ak
        if is_today:
            df = ak.stock_intraday_em(symbol=code)
            parse_akshare_intraday(df, "akshare_intraday_em", push_fn)
        else:
            try:
                df = ak.stock_zh_a_hist_min_em(
                    symbol=code, period="1", start_date=ymd, end_date=ymd, adjust="qfq")
                if df is not None and not df.empty:
                    parse_akshare_intraday(df, "akshare_1m", push_fn)
                    return
            except Exception:
                pass
            df = ak.stock_zh_a_hist_min_em(
                symbol=code, period="5", start_date=ymd, end_date=ymd, adjust="qfq")
            parse_akshare_intraday(df, "akshare_5m", push_fn)
    except Exception as e:
        _log.info(f"akshare 并行异常: {e}")


def sina_worker(code: str, mkt: str, date_str: str, push_fn: Callable) -> None:
    """sina 5min K 抓取 — 无昨收列, prev_close=None 留给 caller 兜底."""
    try:
        import requests as _req, json as _json
        url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        r = _req.get(url, params={"symbol": f"{mkt}{code}", "scale": "5", "ma": "no", "datalen": "1440"},
                     timeout=8, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        if r.status_code != 200 or not r.text.strip().startswith("["):
            return
        from ..lib_common import _safe_float
        arr = _json.loads(r.text)
        tcks = []
        day_prefix = date_str
        for it in arr:
            day = it.get("day", "")
            if not day.startswith(day_prefix):
                continue
            tcks.append({
                "time":        day[11:19] if len(day) >= 19 else day,
                "price":       _safe_float(it.get("close")),
                "open":        _safe_float(it.get("open")) or None,
                "high":        _safe_float(it.get("high")) or None,
                "low":         _safe_float(it.get("low")) or None,
                "volume_hand": _safe_float(it.get("volume")),
                "amount":      None,
                "side":        "",
            })
        if tcks:
            push_fn("sina_5m", tcks, None)
    except Exception as e:
        _log.info(f"sina 并行异常: {e}")


def tencent_worker(code: str, mkt: str, is_today: bool, ymd: str, push_fn: Callable) -> None:
    """腾讯分钟抓取 — 今日 minute/query,历史 mkline m1."""
    try:
        import requests as _req, json as _json
        if is_today:
            url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
            r = _req.get(url, params={"code": f"{mkt}{code}"}, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            if r.status_code != 200:
                return
            j = r.json()
            raw = (j.get("data") or {}).get(f"{mkt}{code}", {}).get("data", {}).get("data") or []
            from ..lib_common import _safe_float
            tcks = []
            for line in raw:
                parts = line.split(" ")
                if len(parts) < 3:
                    continue
                t = parts[0]
                if len(t) == 4 and t.isdigit():
                    t = f"{t[:2]}:{t[2:]}:00"
                p = _safe_float(parts[1])
                if p is None or p == 0 or not t:
                    continue
                tcks.append({
                    "time": t, "price": p,
                    "volume_hand": _safe_float(parts[2]) if len(parts) > 2 else None,
                    "amount":      _safe_float(parts[3]) if len(parts) > 3 else None,
                    "open": None, "high": None, "low": None, "side": "",
                })
            if tcks:
                push_fn("tencent_minute", tcks, None)
        else:
            url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
            r = _req.get(url, params={"param": f"{mkt}{code},m1,,1600"}, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            if r.status_code != 200:
                return
            node = (r.json().get("data") or {}).get(f"{mkt}{code}", {}) or {}
            m1 = node.get("m1") or []
            from ..lib_common import _safe_float
            pc = None
            tcks = []
            for row in m1:
                if not row or len(row) < 5:
                    continue
                ts = str(row[0])
                if len(ts) < 12:
                    continue
                day8 = ts[:8]
                c = _safe_float(row[1])
                if day8 < ymd:
                    pc = c
                    continue
                if day8 != ymd:
                    continue
                hhmm = ts[8:12]
                tcks.append({
                    "time":        f"{hhmm[:2]}:{hhmm[2:]}:00",
                    "price":       c,
                    "open":        _safe_float(row[2]) or None,
                    "high":        _safe_float(row[3]) or None,
                    "low":         _safe_float(row[4]) or None,
                    "volume_hand": _safe_float(row[5]) if len(row) > 5 else None,
                    "amount":      _safe_float(row[7]) if len(row) > 7 else None,
                    "side":        "",
                })
            if tcks:
                push_fn("tencent_m1", tcks, pc)
    except Exception as e:
        _log.info(f"tencent 并行异常: {e}")


def efinance_worker(code: str, ymd: str, push_fn: Callable) -> None:
    """efinance 5min K 兜底 — BSE 不走 (secid 启发式会拼错)."""
    if code.startswith(("8", "92", "43")):
        return
    try:
        import threading as _thr
        bx = {"df": None}
        def _run():
            try:
                import efinance as ef
                bx["df"] = ef.stock.get_quote_history(code, beg=ymd, end=ymd, klt=5, fqt=1)
            except Exception as e:
                _log.info(f"efinance inner thread 异常: {e}")
                bx["err"] = str(e)
        t = _thr.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=8)
        df = bx["df"]
        if df is None or df.empty:
            return
        from ..lib_common import _safe_float
        tcks, pc = [], None
        for _, r in df.iterrows():
            tm = str(r.get("时间", r.get("日期", "")))
            tcks.append({
                "time": tm, "price": _safe_float(r.get("收盘", r.get("最新价"))),
                "volume_hand": _safe_float(r.get("成交量")),
                "amount":      _safe_float(r.get("成交额")),
                "open":        _safe_float(r.get("开盘")),
                "high":        _safe_float(r.get("最高")),
                "low":         _safe_float(r.get("最低")),
                "side": "",
            })
        if tcks:
            push_fn("efinance_5m", tcks, None)
    except Exception as e:
        _log.info(f"efinance 并行异常: {e}")