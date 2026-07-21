"""
tuixue_v3/web/itick_source.py
iTick 免费 WS + REST 数据源接入 (2026-07-16 新增)。

免费层 (注册即可, 无月费):
  REST  60次/分钟, 单只实时报价
  WS    同时订阅 50 标的 tick 推送

数据源作用:
  1) lib_common._REALTIME_SOURCES 注册为 itick_rest (源9), 5次连续失败冷却 300s
  2) 后台 WS 推送 tick 到 _tick_cache, 30s poller 优先读 tick 再走 REST 链
  3) 不配 token 时 ITICK_ENABLED=False, 整个模块不加载, 自动跳过

注册地址: https://itick.org (1byte.io 同源)

API 假设 (基于行业惯例 + REST/WS 通用形态, 如字段名不匹配会在字段映射层容错):
  REST POST https://api.itick.org/sws/v1/quote
    Header: Authorization: Bearer <TOKEN>, Content-Type: application/json
    Body:   {"symbol": "700.HK", "region": "HK"}  (A 股 region=SH/SZ)
    Resp:   {"code": 0, "data": {"s": ..., "ld": ..., "o": ..., "h": ..., "l": ...,
              "ch": ..., "chp": ..., "v": ..., "t": ...}}

  WS 订阅: wss://api.itick.org/sws/v1/quote
    发送: {"action": "sub", "symbols": [...]}
    推送: {"symbol": "...", "ld": 123.45, "t": "2026-07-16T09:31:05Z"}
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import requests

from .. import config
# lib_common 在父包 tuixue_v3 下, 不在 tuixue_v3.web 下, 用 .. 导入
# (实际不需要直接调用 lib_common, REST 走 _REALTIME_SOURCES 自动冷却)

log = logging.getLogger("tuixue_v3.web.itick_source")

ITICK_ENABLED = bool(config.ITICK_TOKEN)

# ─── 字段映射 (兼容多种 iTick 字段名变体) ──────────────────────────────
# iTick 实测响应字段多为缩写 (ld=last, ch=change, chp=change_pct, etc)
# 这里做最长匹配容错, 即便字段名变了也能解析
_FIELD_ALIASES = {
    "最新价": ("ld", "last", "price", "close", "p", "current"),
    "今开":   ("o", "open", "open_price"),
    "昨收":   ("pdc", "prev_close", "previous_close", "lc", "yesterday"),
    "最高":   ("h", "high"),
    "最低":   ("l", "low"),
    "涨跌额": ("ch", "change", "change_amt"),
    "涨跌幅": ("chp", "change_pct", "change_percent"),
    "成交量": ("v", "volume", "vol"),
    "成交额": ("tu", "amount", "turnover"),
    "换手率": ("tr", "turnover_ratio", "turnover_rate"),
    "时间":   ("t", "timestamp", "time", "ts"),
}


def _normalize_a_share_code(code: str) -> tuple[str, str]:
    """6位 A 股代码 → iTick (symbol, region)
    sh: 600xxx / 601xxx / 603xxx / 605xxx / 688xxx / 689xxx (科创板)
    sz: 000xxx / 001xxx / 002xxx / 003xxx / 300xxx / 301xxx (创业板)
    """
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return code, "SH"
    return code, "SZ"


def _map_fields(raw: dict) -> dict | None:
    """iTick 缩写字段 → lib_common 标准字段。无 last/prev_close 视为无效返回 None。"""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for std_name, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            if alias in raw and raw[alias] is not None:
                v = raw[alias]
                try:
                    if std_name == "时间":
                        out[std_name] = str(v)
                    elif std_name in ("最新价", "今开", "昨收", "最高", "最低",
                                     "涨跌额", "涨跌幅", "换手率"):
                        out[std_name] = float(v)
                    else:
                        out[std_name] = float(v)
                except (ValueError, TypeError):
                    pass
                break
    # 至少需要最新价 + 昨收才视为有效
    if "最新价" in out and "昨收" in out and out["昨收"] > 0:
        # 补涨跌幅 (若 iTick 没给)
        if "涨跌幅" not in out or out["涨跌幅"] == 0:
            out["涨跌幅"] = (out["最新价"] - out["昨收"]) / out["昨收"] * 100
        if "涨跌额" not in out:
            out["涨跌额"] = out["最新价"] - out["昨收"]
        return out
    return None


# ─── REST 接口 ──────────────────────────────────────────────
def fetch_itick_rest(code: str) -> dict | None:
    """单只 A 股 iTick 实时报价。

    5s 硬超时 (避免沙箱 DNS 劫持 hang)。失败返回 None,
    lib_common._fetch_with_retry 会自动 _report_fail 累计冷却。
    """
    if not ITICK_ENABLED:
        return None
    symbol, region = _normalize_a_share_code(code)
    headers = {
        "Authorization": f"Bearer {config.ITICK_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # iTick 不同接口接受 GET/POST, 这里用 GET 兼容性最好
    url = f"{config.ITICK_REST_BASE}?symbol={symbol}&region={region}"
    try:
        r = requests.get(url, headers=headers, timeout=config.ITICK_REST_TIMEOUT)
        if r.status_code != 200:
            log.debug(f"itick_rest {code} HTTP {r.status_code}: {r.text[:120]}")
            return None
        try:
            j = r.json()
        except Exception:
            log.debug(f"itick_rest {code} 非 JSON 响应: {r.text[:120]}")
            return None
        # 兼容顶层 data / 直接 dict
        data = j.get("data") if isinstance(j, dict) and "data" in j else j
        if not isinstance(data, dict):
            return None
        norm = _map_fields(data)
        if norm:
            norm["_source"] = "itick_rest"
        return norm
    except (requests.Timeout, requests.ConnectionError, Exception) as e:
        log.debug(f"itick_rest {code} 失败: {type(e).__name__}: {str(e)[:60]}")
        return None


# ─── WS 推送 tick 缓存 ──────────────────────────────────────
_tick_cache: dict[str, tuple[float, dict]] = {}   # code → (timestamp, quote_dict)
_tick_lock = threading.Lock()


def _set_tick(code: str, q: dict) -> None:
    with _tick_lock:
        _tick_cache[code] = (time.time(), q)


def _get_tick(code: str) -> dict | None:
    """读 WS 推送的 tick (10s 内有效), 返回标准字段 dict 或 None。"""
    with _tick_lock:
        if code not in _tick_cache:
            return None
        ts, q = _tick_cache[code]
        if time.time() - ts > config.ITICK_TICK_TTL:
            return None
        return dict(q)


# ─── 后台 WS 订阅任务 ───────────────────────────────────────
_ws_thread: threading.Thread | None = None
_ws_stop = threading.Event()
_watchlist_provider: Any = None  # 由 _realtime_poller 注入, 拿自选股列表


def start_itick_ws_background(watchlist_provider=None) -> None:
    """启动 iTick WS 后台线程, 订阅自选股 + 最近访问的标的 tick。
    重复调用幂等。token 缺失时静默 noop。
    """
    global _ws_thread, _watchlist_provider
    if not ITICK_ENABLED:
        log.info("iTick token 未配置, WS 后台线程未启动")
        return
    _watchlist_provider = watchlist_provider
    if _ws_thread and _ws_thread.is_alive():
        return
    _ws_stop.clear()
    _ws_thread = threading.Thread(
        target=_ws_loop, daemon=True, name="itick-ws",
    )
    _ws_thread.start()
    log.info("iTick WS 后台线程已启动")


def stop_itick_ws() -> None:
    _ws_stop.set()


def _ws_loop() -> None:
    """WS 循环: 连接 → 订阅 → 接收 → 缓存 tick → 断线 3s 重连。"""
    import websockets   # 局部 import, 没装不影响主路径
    while not _ws_stop.is_set():
        codes = _collect_watch_codes()
        if not codes:
            time.sleep(5)
            continue
        try:
            # 兼容 websockets v10+ 和 v12+ API
            try:
                ws = websockets.connect(
                    config.ITICK_WS_URL,
                    additional_headers={"Authorization": f"Bearer {config.ITICK_TOKEN}"},
                    ping_interval=20, ping_timeout=10,
                )
            except TypeError:
                # v10: extra_headers
                ws = websockets.connect(
                    config.ITICK_WS_URL,
                    extra_headers={"Authorization": f"Bearer {config.ITICK_TOKEN}"},
                    ping_interval=20, ping_timeout=10,
                )
            with ws as conn:
                sub_msg = json.dumps({
                    "action": "sub",
                    "symbols": [_normalize_a_share_code(c)[0] for c in codes],
                })
                conn.send(sub_msg)
                log.info(f"iTick WS 已订阅 {len(codes)} 标的")
                while not _ws_stop.is_set():
                    try:
                        msg = conn.recv(timeout=1.0)
                    except TimeoutError:
                        continue
                    except Exception:
                        break
                    try:
                        evt = json.loads(msg)
                    except Exception:
                        continue
                    sym = evt.get("symbol") or evt.get("s") or ""
                    # 反查 code (iTick 返回可能只是 "000001", 我们的 keys 是 "000001")
                    if sym and sym in codes:
                        norm = _map_fields(evt.get("data") or evt)
                        if norm:
                            _set_tick(sym, norm)
                    elif sym:
                        # sym 可能是带 region 前缀, 这里宽松匹配
                        for c in codes:
                            if c in sym or sym.endswith(c):
                                norm = _map_fields(evt.get("data") or evt)
                                if norm:
                                    _set_tick(c, norm)
                                break
        except Exception as e:
            log.warning(f"iTick WS 异常 ({type(e).__name__}: {str(e)[:60]}), {config.ITICK_WS_RECONNECT_DELAY}s 后重连")
        if _ws_stop.wait(config.ITICK_WS_RECONNECT_DELAY):
            return


def _collect_watch_codes() -> list[str]:
    """收集需要订阅 WS tick 的标的 (最多 50 个, 免费层上限)。
    优先自选股, 其次 _realtime_poller 维护的最近访问标的。
    """
    codes: list[str] = []
    if _watchlist_provider:
        try:
            codes.extend(_watchlist_provider() or [])
        except Exception:
            pass
    # 去重 + 限 50
    seen = set()
    out = []
    for c in codes:
        if c and c not in seen and len(c) == 6 and c.isdigit():
            seen.add(c)
            out.append(c)
        if len(out) >= 50:
            break
    return out


# ─── CLI 调试入口 ──────────────────────────────────────────
if __name__ == "__main__":
    if not ITICK_ENABLED:
        print("TUIXUE_ITICK_TOKEN 未配置, iTick 模块未启用")
        import sys
        sys.exit(0)
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "000001"
    print(f"=== iTick REST 测试 {code} ===")
    r = fetch_itick_rest(code)
    if r:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("  失败 (token 错 / 网络抖 / 字段名变体)")
    print()
    print(f"=== WS tick 缓存测试 ===")
    print(f"  当前缓存: {len(_tick_cache)} 标的")
    for c, (ts, q) in list(_tick_cache.items())[:3]:
        print(f"  {c}: {q.get('最新价', '?')} 涨幅={q.get('涨跌幅', '?')}%")
