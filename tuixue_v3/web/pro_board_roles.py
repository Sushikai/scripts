"""
专业板块角色 — 东财官方概念板块成分 (龙头 / 中军 / 杂毛)

数据源 (东财概念板块, push2delay 镜像):
  BK1661 行业龙头 → role=main (龙头)   — 东财官方行业龙头合集
  BK1662 权重股   → role=sub  (中军)   — 各行业大市值核心资产
  BK1158 微盘股   → role=spec (杂毛)   — 小市值概念票池

缓存: cache_store (Redis 主用 + SQLite fallback) TTL 12h。
新鲜度: < TTL 直接服务; TTL~72h 内用旧数据+后台刷新 (stale-while-revalidate);
超过 72h 或无数据才同步阻塞刷新。get_role() 纯内存查找, 不阻塞热路径。
"""
from __future__ import annotations

import logging
import threading
import time

import requests as _requests

from ..cache_store import get_store

log = logging.getLogger("tuixue_v3.web.pro_board_roles")

BOARDS = {
    # R98 (2026-08-12): 多平台交叉 — 现有 3 个东财官方"角色"板块 + 7 个权威指数/持仓板块.
    # 优先级从高到低: 行业龙头 > 央视50 > 上证50 > 沪深300 > 沪深300 权重 > 央国企 > 题材.
    # 同一只股先到先得, 命中前面板块即覆盖后面 (setdefault 已保证顺序).
    "main": [
        {"code": "BK1661", "name": "行业龙头"},       # 原 EM 官方行业龙头 — 龙头最高优先级
        {"code": "BK0610", "name": "央视50"},         # 央视50指数成分 — 白马蓝筹
        {"code": "BK0611", "name": "上证50"},         # 上证50 — 超级权重
    ],
    "sub": [
        {"code": "BK1662", "name": "权重股"},         # 原 EM 权重股
        {"code": "BK0612", "name": "上证180"},        # 上证180 — 权重
        {"code": "BK0500", "name": "沪深300"},        # 沪深300 — 大盘权重
        {"code": "BK0568", "name": "深成500"},        # 深成500 — 大盘权重
        {"code": "BK0536", "name": "基金重仓"},       # 主流基金重仓
        {"code": "BK0535", "name": "QFII重仓"},       # QFII 重仓
        {"code": "BK0552", "name": "机构重仓"},       # 机构重仓
        {"code": "BK0520", "name": "社保重仓"},       # 社保重仓
    ],
    "spec": [
        {"code": "BK1158", "name": "微盘股"},         # 原 EM 微盘股
        {"code": "BK0501", "name": "次新股"},         # 次新股 — 小盘/题材
        {"code": "BK0683", "name": "央国企改革"},     # 中字头/国改 — 题材股
    ],
}

# 概念板块成分变化慢 (行业龙头/权重股月度级, 微盘股每日), 12h 足够
TTL = 12 * 3600
BLOCK_AFTER = 3 * 24 * 3600   # 超过该陈旧度才同步阻塞刷新
REFRESH_COOLDOWN = 600        # 后台刷新节流: 10 分钟内最多一次

_HOSTS = [
    "push2delay.eastmoney.com",   # 镜像, 实测稳定
    "push2.eastmoney.com",        # 主站, 偶发风控
    "push2his.eastmoney.com",
]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

_store = get_store()
_STORE_KEY = "pro_board_roles:v2"  # R98: 多平台交叉, payload shape 变 (boards role→list)
_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_refresh_last_ts = 0.0

_state: dict | None = None   # {"roles": {code: role}, "built_at": float, "boards": {...}}


def _fetch_board(code: str, host: str) -> list[str]:
    """分页拉取板块成分股代码 (f12, 6 位无前缀)。pz 上限 100/页。失败抛异常。"""
    url = f"https://{host}/api/qt/clist/get"
    codes: list[str] = []
    for pn in range(1, 101):  # 上限 10000 只, 足够任何板块
        params = {"pn": str(pn), "pz": "100", "fs": f"b:{code}",
                  "fields": "f12", "fltt": "2"}
        r = _requests.get(url, params=params, headers=_HEADERS, timeout=8)
        r.raise_for_status()
        data = r.json().get("data") or {}
        total = int(data.get("total") or 0)
        diff = data.get("diff") or {}
        if not diff:
            break
        for v in diff.values():
            c = str(v.get("f12") or "").strip().zfill(6)
            if c:
                codes.append(c)
        if total and len(codes) >= total:
            break
    if not codes:
        raise RuntimeError(f"{code} 成分为空")
    return codes


def _fetch_all() -> dict:
    """抓取所有板块 (R98: 多平台交叉, 14 个板块覆盖 ~2500 只); 任一失败即抛异常 (整体回退旧数据)。"""
    last_err = None
    # 扁平化: 按优先级顺序拼成 (role, board_info) 列表, setdefault 保证先到先得
    ordered_boards: list[tuple[str, dict]] = []
    for role in ("main", "sub", "spec"):
        for info in BOARDS.get(role, []):
            ordered_boards.append((role, info))
    log.info(f"pro_board_roles 启动: {len(ordered_boards)} 个板块, "
             f"main={sum(1 for r,_ in ordered_boards if r=='main')} "
             f"sub={sum(1 for r,_ in ordered_boards if r=='sub')} "
             f"spec={sum(1 for r,_ in ordered_boards if r=='spec')}")
    for host in _HOSTS:
        try:
            roles: dict[str, str] = {}
            for role, info in ordered_boards:
                try:
                    for c in _fetch_board(info["code"], host):
                        roles.setdefault(c, role)
                except Exception as be:
                    log.warning(f"pro_board_roles 板块 {info['code']} 失败: {be}, 跳过")
                    continue
            counts = {}
            for role in ("main", "sub", "spec"):
                counts[role] = sum(1 for r in roles.values() if r == role)
            return {
                "roles":    roles,
                "built_at": time.time(),
                "boards":   {role: [{"code": info["code"], "name": info["name"]}
                                    for info in BOARDS.get(role, [])]
                             for role in ("main", "sub", "spec")},
                "counts":   counts,
            }
        except Exception as e:
            last_err = e
            log.warning(f"pro_board_roles 抓取失败 host={host}: {e}")
    raise RuntimeError(f"全部数据源失败: {last_err}")


def _save(payload: dict) -> None:
    try:
        _store.set(_STORE_KEY, payload, ttl=TTL)
    except Exception as e:
        log.warning(f"pro_board_roles 写缓存失败: {e}")


def _load_from_store() -> dict | None:
    try:
        p = _store.get(_STORE_KEY)
        if p and isinstance(p, dict) and p.get("roles"):
            return p
    except Exception as e:
        log.warning(f"pro_board_roles 读缓存失败: {e}")
    return None


def _ensure_loaded() -> None:
    """保证 _state 有效。TTL 内/旧数据可用 → 不阻塞; 否则同步刷新一次。"""
    global _state
    st = _state
    if st is not None:
        age = time.time() - st["built_at"]
        if age < TTL:
            return
        if age < BLOCK_AFTER:
            _spawn_refresh_once()
            return
    with _LOCK:
        st = _state
        if st is not None and (time.time() - st["built_at"] < BLOCK_AFTER):
            return  # 另一线程刚刷新完
        cached = _load_from_store()
        if cached is not None:
            _state = cached
        try:
            fresh = _fetch_all()
        except Exception:
            if _state is None:
                _state = {"roles": {}, "built_at": 0, "boards": {}, "error": True}
                log.warning("pro_board_roles 无任何可用数据, 角色标注降级为空")
            return
        _state = fresh
        _save(fresh)


def _spawn_refresh_once() -> None:
    global _refresh_last_ts
    with _REFRESH_LOCK:
        if time.time() - _refresh_last_ts < REFRESH_COOLDOWN:
            return
        _refresh_last_ts = time.time()
    threading.Thread(target=_bg_refresh, name="pro-board-roles-refresh",
                     daemon=True).start()


def _bg_refresh() -> None:
    try:
        fresh = _fetch_all()
        _state = fresh
        _save(fresh)
        log.info(f"pro_board_roles 后台刷新完成: "
                 f"main={len([1 for r in fresh['roles'].values() if r == 'main'])} "
                 f"sub={len([1 for r in fresh['roles'].values() if r == 'sub'])} "
                 f"spec={len([1 for r in fresh['roles'].values() if r == 'spec'])}")
    except Exception as e:
        log.debug(f"pro_board_roles 后台刷新失败: {e}")


def get_role(code: str) -> str:
    """返回 "" | "main" | "sub" | "spec" — 纯内存查找, 不抛异常。"""
    try:
        _ensure_loaded()
    except Exception as e:
        log.debug(f"pro_board_roles get_role 加载失败: {e}")
    st = _state
    if not st:
        return ""
    return st.get("roles", {}).get(str(code or "").strip().zfill(6), "")


def role_stats() -> dict:
    """给前端图例用: 各角色数量 / 数据时间 / 板块映射。"""
    try:
        _ensure_loaded()
    except Exception:
        pass
    st = _state
    if not st or not st.get("roles"):
        return {"counts": {}, "built_at": 0, "source": "东财概念板块", "ok": False}
    counts = {"main": 0, "sub": 0, "spec": 0}
    for r in st["roles"].values():
        if r in counts:
            counts[r] += 1
    return {
        "counts":   counts,
        "built_at": st.get("built_at", 0),
        "boards":   st.get("boards", {}),
        "source":   "东财概念板块",
        "ok":       True,
    }
