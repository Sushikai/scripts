"""
web/rotation.py — 板块资金轮动分析(2026-07-12 新增)

三大核心模块:
  1. 基础板块资金流向计算 — /api/flow
  2. 板块资金轮动识别引擎  — /api/rotation
  3. 当下热点龙头板块判定  — /api/hotspot

数据源:
  akshare.stock_board_industry_name_em   — 申万行业板块列表
  akshare.stock_board_concept_name_em    — 题材概念板块列表
  akshare.stock_sector_fund_flow_rank    — 板块实时资金净流入
  akshare.stock_board_industry_hist_em   — 板块日线资金
  akshare.stock_board_industry_cons_em   — 板块成份股
  akshare.stock_lhb_stock_detail_em      — 龙虎榜明细

所有 akshare 调用包 ThreadPoolExecutor 硬超时(默认 6s)。
不写本地数据库 — 全部 in-memory 计算,TTL 缓存在 server.TTLCache(60s)。

资金 6 类(用户口径):
  institution / northbound / quant / hot_tier1 / hot_tier2 / retail_lhasa

对齐 seat_classify.py 的 8 类 → 6 类映射:
  hot_tier3 折入 hot_tier2(三线大户归入"区域游资+",保持显示简洁)
  unknown   归入 quant(未识别席位按机器资金兜底,UI 可按"显隐"开关隐藏)
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any

import requests as _requests

from .. import config as _config
from . import seat_classify as _seat

log = logging.getLogger("tuixue_v3.web.rotation")

# ───────────────────────────────────────────────────────────
# 短时常驻线程池 — 每请求一个 future,不阻塞 event loop
# ───────────────────────────────────────────────────────────
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rot-akshare")


# ───────────────────────────────────────────────────────────
# 资金 6 类 → 8 类 seat_classify 折叠映射
# ───────────────────────────────────────────────────────────
def _map_seat_to_user6(cat8: str) -> str:
    """seat_classify 8 类 → 用户要求的 6 类(去掉 hot_tier3 / unknown 不展示)。"""
    if cat8 == "hot_tier3":
        return "hot_tier2"          # 三线折入二线区域游资
    if cat8 in ("institution", "northbound", "quant",
                "hot_tier1", "hot_tier2", "retail_lhasa"):
        return cat8
    return "quant"                 # unknown → 兜底到量化隐藏可关


SEAT_USER6_ORDER = [
    "institution", "northbound", "quant",
    "hot_tier1", "hot_tier2", "retail_lhasa",
]

SEAT_USER6_LABEL = {
    "institution":   "机构专用",
    "northbound":    "北向资金",
    "quant":         "量化程序化",
    "hot_tier1":     "顶级一线游资",
    "hot_tier2":     "二线区域游资",
    "retail_lhasa":  "散户·拉萨",
}

# 折叠版 categorize helper — 输入 list of (seat_name, amount_wan)
def _categorize6(rows: list[dict]) -> dict:
    """输入 rows=[{seat_name, buy, sell, net}, ...], 输出 6 类 sum."""
    out = {k: {"buy": 0.0, "sell": 0.0, "net": 0.0, "count": 0,
               "seats": []} for k in SEAT_USER6_ORDER}
    for r in rows or []:
        seat = (r.get("seat_name") or "").strip()
        if not seat:
            continue
        cat8 = _seat.classify_seat(seat)
        cat6 = _map_seat_to_user6(cat8)
        cat = out[cat6]
        cat["buy"]  += float(r.get("buy", 0) or 0)
        cat["sell"] += float(r.get("sell", 0) or 0)
        cat["net"]  += float(r.get("net", 0) or 0)
        cat["count"] += 1
        if len(cat["seats"]) < 5:
            alias = _seat.get_hot_money_alias(seat)
            cat["seats"].append({
                "name": seat,
                "alias": alias[0] if alias else "",
                "buy":  round(float(r.get("buy", 0) or 0), 2),
                "net":  round(float(r.get("net", 0) or 0), 2),
            })
    # 归一化为万元
    for k in out:
        out[k]["buy"]  = round(out[k]["buy"],  2)
        out[k]["sell"] = round(out[k]["sell"], 2)
        out[k]["net"]  = round(out[k]["net"],  2)
    return out


# ───────────────────────────────────────────────────────────
# 通用安全 akshare 调用 — module-global lazy import,
# 避免多 worker 并发 import race + 重复 import 阻塞
# ───────────────────────────────────────────────────────────
_ak = None
_ak_import_lock = threading.Lock()


def _get_ak():
    global _ak
    if _ak is not None:
        return _ak
    with _ak_import_lock:
        if _ak is None:
            try:
                import akshare as _mod
                _ak = _mod
                log.info("akshare module 加载完成")
            except Exception as e:                       # noqa: BLE001
                log.warning(f"akshare import 失败: {e}")
    return _ak


def _safe_ak(fn_name: str, *args, default=None, timeout: float | None = None, **kw):
    """在子线程跑 akshare 调用,主线程最多等 timeout 秒,超时返 default。"""
    timeout = float(timeout or _config.ROTATION_FUND_FETCH_TIMEOUT)

    def _call():
        ak = _get_ak()
        if ak is None:
            return default
        try:
            fn = getattr(ak, fn_name)
            return fn(*args, **kw)
        except Exception as e:                           # noqa: BLE001
            log.debug(f"akshare {fn_name} 异常: {e}")
            return default

    fut = _EXECUTOR.submit(_call)
    try:
        return fut.result(timeout=timeout)
    except FuturesTimeout:
        log.warning(f"akshare {fn_name} 超时({timeout}s)")
        return default
    except Exception as e:                               # noqa: BLE001
        log.warning(f"akshare {fn_name} 包装异常: {e}")
        return default


def _clean_nan(obj):
    """递归把 NaN/Inf → None。"""
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_nan(x) for x in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


# ───────────────────────────────────────────────────────────
# 模块 1:数据获取层
# ───────────────────────────────────────────────────────────
_BOARD_LIST_CACHE: dict[str, Any] = {"data": None, "ts": 0.0}
_BOARD_LIST_TTL = 90.0


def fetch_board_list(*, force: bool = False) -> dict:
    """申万行业 + 题材概念 板块列表(申万 31 个稳定,题材 ~200 个波动)。

    返回:{"industry": [{code, name, leader_code, leader_name, change_pct,
              net_flow_yi, turnover_yi}], "concept": [...], "ts": ...}

    dev mock: TUIXUE_DEV_MOCK_BOARDS=1 时返回确定性 mock 数据(8 出 + 8 入 + 4 概念),
              用于沙箱 / 数据源挂时验证桑基图 + 热点页视觉。
    """
    if os.environ.get("TUIXUE_DEV_MOCK_BOARDS") == "1":
        return _dev_mock_boards()

    now = time.monotonic()
    if not force and _BOARD_LIST_CACHE["data"] is not None \
            and now - _BOARD_LIST_CACHE["ts"] < _BOARD_LIST_TTL:
        return _BOARD_LIST_CACHE["data"]

    industry_raw = _safe_ak(
        "stock_board_industry_name_em", default=[],
        timeout=_config.ROTATION_FUND_FETCH_TIMEOUT,
    )
    concept_raw = _safe_ak(
        "stock_board_concept_name_em", default=[],
        timeout=_config.ROTATION_FUND_FETCH_TIMEOUT,
    )

    def _normalize(rows, kind: str) -> list[dict]:
        out = []
        for r in rows or []:
            # akshare 字段名漂移常见 — 多重 fallback
            name = (r.get("板块名称") or r.get("name")
                    or r.get("板块") or "").strip()
            code = (r.get("板块代码") or r.get("code") or "").strip()
            if not name:
                continue
            change = r.get("涨跌幅") or r.get("change_pct") or r.get("涨幅")
            try:
                change = float(change)
            except Exception:
                change = None
            net = r.get("主力净流入") or r.get("net_flow") or r.get("净流入")
            try:
                net_yi = round(float(net) / 1e8, 3) if net is not None else None
            except Exception:
                net_yi = None
            turnover = r.get("成交额") or r.get("turnover")
            try:
                turnover_yi = round(float(turnover) / 1e8, 3) if turnover is not None else None
            except Exception:
                turnover_yi = None
            leader_code = (r.get("领涨股代码") or r.get("lead_code") or "").strip()
            leader_name = (r.get("领涨股名称") or r.get("lead_name") or "").strip()
            out.append({
                "kind":       kind,
                "code":       code,
                "name":       name,
                "leader_code": leader_code,
                "leader_name": leader_name,
                "change_pct": change,
                "net_flow_yi": net_yi,
                "turnover_yi": turnover_yi,
            })
        return out

    payload = {
        "ts":        time.time(),
        "industry":  _normalize(industry_raw, "industry"),
        "concept":   _normalize(concept_raw,  "concept"),
        "sources":   {
            "industry_count": len(industry_raw or []),
            "concept_count":  len(concept_raw  or []),
        },
    }
    _BOARD_LIST_CACHE["data"] = payload
    _BOARD_LIST_CACHE["ts"] = now
    return payload


# ───────────────────────────────────────────────────────────
# dev-only mock: TUIXUE_DEV_MOCK_BOARDS=1 触发
# 8 行业流出 + 8 行业流入 + 4 概念混合,每条都过 detect_rotation / score_hotspot 阈值
# ───────────────────────────────────────────────────────────
_DEV_MOCK_LEADERS = [
    ("600519", "贵州茅台"), ("000858", "五粮液"), ("000333", "美的集团"),
    ("300750", "宁德时代"), ("002594", "比亚迪"), ("601318", "中国平安"),
    ("600276", "恒瑞医药"), ("688981", "中芯国际"), ("002415", "海康威视"),
    ("300059", "东方财富"), ("600036", "招商银行"), ("601012", "隆基绿能"),
    ("002475", "立讯精密"), ("603259", "药明康德"),
]


def _dev_mock_boards() -> dict:
    """确定性 mock — 每次返回同样数据,便于截屏对比。

    outflow 板块 net_flow_yi ∈ [-3.5, -1.5] (超过 1 亿阈值)
    inflow  板块 net_flow_yi ∈ [+1.5, +4.5]
    概念混合 4 个,数值较小 [-0.8, +0.6],主要起结构占位作用。
    """
    industry_out = [
        ("BK0438", "房地产",     -2.85, -1.85, "000002", "万科A"),
        ("BK0420", "煤炭行业",   -3.20, -2.40, "601225", "陕西煤业"),
        ("BK0485", "钢铁行业",   -1.95, -1.20, "600019", "宝钢股份"),
        ("BK0421", "石油行业",   -2.10, -0.95, "601857", "中国石油"),
        ("BK0422", "银行",       -3.50, -1.50, "601398", "工商银行"),
        ("BK0423", "保险",       -1.65, -0.85, "601318", "中国平安"),
        ("BK0475", "传媒娱乐",   -2.45, +1.20, "300413", "芒果超媒"),
        ("BK0426", "纺织服装",   -1.55, -0.45, "600400", "红豆股份"),
    ]
    industry_in = [
        ("BK0451", "半导体",     +3.20, +4.10, "688981", "中芯国际"),
        ("BK0477", "通信设备",   +2.85, +3.95, "300308", "中际旭创"),
        ("BK0478", "软件服务",   +2.40, +2.55, "600588", "用友网络"),
        ("BK0452", "电子元件",   +1.95, +2.30, "002475", "立讯精密"),
        ("BK0434", "电池",       +3.80, +5.10, "300750", "宁德时代"),
        ("BK0453", "光伏设备",   +1.75, +2.10, "601012", "隆基绿能"),
        ("BK0481", "医疗器械",   +2.15, +1.45, "300760", "迈瑞医疗"),
        ("BK0479", "创新药",     +4.50, +3.85, "603259", "药明康德"),
    ]
    concept_mix = [
        ("BK0933", "人工智能",   +0.65, +0.95, "300223", "北京君正"),
        ("BK0934", "华为产业链", +0.45, +0.55, "300308", "中际旭创"),
        ("BK0935", "机器人",     -0.85, +0.20, "300124", "汇川技术"),
        ("BK0936", "固态电池",   +0.20, +1.40, "300750", "宁德时代"),
    ]

    def _mk(rows, kind):
        out = []
        for code, name, nf, chg, lc, ln in rows:
            out.append({
                "kind":        kind,
                "code":        code,
                "name":        name,
                "leader_code": lc,
                "leader_name": ln,
                "change_pct":  chg,
                "net_flow_yi": nf,
                "turnover_yi": round(50 + abs(nf) * 3, 2),
            })
        return out

    return {
        "ts":        time.time(),
        "industry":  _mk(industry_out, "industry") + _mk(industry_in, "industry"),
        "concept":   _mk(concept_mix, "concept"),
        "sources":   {"industry_count": 16, "concept_count": 4, "mock": True},
    }


def fetch_sector_daily(sector_name: str, days: int = 5) -> list[dict]:
    """板块日线累计净流入(N 日)。

    返回:list[{date, net_flow_yi, turnover_yi, change_pct, layered: {institution..retail_lhasa 6 类}}]
    注:akshare 该粒度大多是整板块的"主力净流入",6 类分层退化到净流入字段兜底。
    """
    if not sector_name:
        return []
    raw = _safe_ak(
        "stock_sector_fund_flow_rank",
        sector_name=sector_name,
        default=[],
        timeout=_config.ROTATION_FUND_FETCH_TIMEOUT,
    )
    out = []
    for r in list(raw or [])[:days][::-1]:
        try:
            net = float(r.get("主力净流入") or r.get("净流入") or 0) / 1e8
        except Exception:
            net = 0.0
        try:
            turnover = float(r.get("成交额") or 0) / 1e8
        except Exception:
            turnover = 0.0
        try:
            chg = float(r.get("涨跌幅") or 0)
        except Exception:
            chg = 0.0
        date = str(r.get("日期") or r.get("date") or "")
        # 6 类分层:akshare 接口只给主力净流入,逐类细分不可得。
        # 兜底放 1/N 比例(避免 DB 写),让前端能展示色块但不可误读为精确。
        each = round(net / 6, 4)
        layered = {k: each for k in SEAT_USER6_ORDER}
        out.append({
            "date":        date,
            "net_flow_yi": round(net, 3),
            "turnover_yi": round(turnover, 3),
            "change_pct":  round(chg, 2),
            "layered_yi":  layered,    # 退化值,前端加注
            "_layered_note": "akshare 仅主力净流入,六类按等分兜底",
        })
    return out


def fetch_sector_intraday(sector_name: str, period: int = 5) -> list[dict]:
    """板块分钟分时资金(1min / 5min)。

    返回:list[{time, layered_yi: {6类}}]
    """
    if not sector_name:
        return []
    # 先尝试带 period 入参;若 akshare 该函数签名不认 period,fallback 拉日线 + 平均分桶
    raw = _safe_ak(
        "stock_sector_fund_flow_rank",     # 该接口本身就含「分钟」字段
        sector_name=sector_name,
        default=[],
        timeout=_config.ROTATION_FUND_FETCH_TIMEOUT,
    )
    if not raw:
        return []
    # akshare 该接口大多返回 1 行或若干行快照,不构成时间序列,降级为 1 个时点
    last = raw[0] if isinstance(raw, list) and raw else raw
    try:
        net = float(last.get("主力净流入") or 0) / 1e8
    except Exception:
        net = 0.0
    each = round(net / 6, 4)
    now = time.time()
    return [{
        "time":       time.strftime("%H:%M", time.localtime(now)),
        "price":      last.get("板块指数") or last.get("最新价"),
        "change_pct": last.get("涨跌幅"),
        "layered_yi": {k: each for k in SEAT_USER6_ORDER},
        "_note": "实时快照,六类按等分兜底",
    }]


# ───────────────────────────────────────────────────────────
# 龙虎榜关联 — 6 类资金实时聚合
# ───────────────────────────────────────────────────────────
def fetch_lhb_rows_for_securities(codes: list[str], days: int = 30) -> list[dict]:
    """给一批股票代码,拉近 N 天龙虎榜明细,合并去重。

    返回:[{date, code, name, seat_name, buy_wan, sell_wan, net_wan, reason}]
    """
    if not codes:
        return []
    # dev mock: 沙箱里 akshare 龙虎榜挂,合成已知顶级游资的 8 行记录
    # (从真实席位字典里挑会识别的别名,这样 score_hotspot 的 seat_score 能涨起来)
    if os.environ.get("TUIXUE_DEV_MOCK_BOARDS") == "1":
        known_seats = [
            ("国泰君安上海江苏路营业部",   "章盟主",   4500),
            ("中信证券杭州延安路营业部",   "赵老哥",   3800),
            ("华鑫证券上海分公司",         "炒股养家", 2900),
            ("中国中金财富证券南京中山东路营业部", "孙哥",   2700),
            ("东方证券绍兴解放南路营业部", "作手新一", 2400),
            ("兴业证券陕西分公司",         "方新侠",   2200),
            ("光大证券佛山季华六路营业部", "佛山无影脚",1800),
            ("华泰证券深圳益田路荣超商务中心营业部", "欢乐海", 1600),
        ]
        out = []
        for i, code in enumerate(codes[:12]):
            seat, alias, amt = known_seats[i % len(known_seats)]
            out.append({
                "date":      "2026-07-10",
                "code":      code,
                "name":      f"MOCK-{code}",
                "seat_name": seat,
                "buy_wan":   amt,
                "sell_wan":   amt // 4,
                "net_wan":   amt - amt // 4,
                "reason":    "[MOCK] 涨停",
            })
        return out
    out = []
    for code in codes[:30]:                       # 防爆 — 一次最多 30 只
        rows = _safe_ak(
            "stock_lhb_stock_detail_em",
            symbol=code,
            default=[],
            timeout=_config.ROTATION_LHB_FETCH_TIMEOUT,
        )
        for r in rows or []:
            try:
                buy  = float(r.get("成交额")  or r.get("buy")  or 0)
                sell = float(r.get("卖出额")  or r.get("sell") or 0)
            except Exception:
                buy = sell = 0
            seat = (r.get("营业部名称") or r.get("席位名称")
                    or r.get("seat_name") or "").strip()
            if not seat:
                continue
            out.append({
                "date":      str(r.get("交易日期") or r.get("日期") or ""),
                "code":      code,
                "name":      r.get("名称") or r.get("name") or "",
                "seat_name": seat,
                "buy_wan":   round(buy, 2),
                "sell_wan":  round(sell, 2),
                "net_wan":   round(buy - sell, 2),
                "reason":    r.get("上榜原因") or r.get("解读") or "",
            })
    return out


# ───────────────────────────────────────────────────────────
# 模块 2:轮动识别引擎
# ───────────────────────────────────────────────────────────
def _momentum(board: dict) -> float:
    """板块资金动量 = N日净流入 / 板块总成交。
    返回百分比(2.0 表示 2%)。"""
    nf = board.get("net_flow_yi") or 0
    to = board.get("turnover_yi") or 0
    if to <= 0:
        return 0.0
    return round(100 * nf / to, 3)


def _rotation_strength(out_yi: float, in_yi: float, divergence: float) -> float:
    """轮动强度:迁徙规模(0-50) + 涨跌幅背离(0-50) → 总分 0-100。"""
    mag = min(50.0, (out_yi + in_yi) * 2.0)     # 1 亿 / 项 → 2 分;10 亿封顶
    div = max(0.0, min(50.0, abs(divergence) * 10.0))   # 每 1% 背离 = 10 分
    return round(mag + div, 2)


def detect_rotation(boards: list[dict], *, top_outflow_n: int = 8,
                    top_inflow_n: int = 8) -> dict:
    """轮动识别。

    输入:board list({name, net_flow_yi, change_pct, turnover_yi})
    输出:
      - outflow: [{name, out_yi, change_pct}]
      - inflow:  [{name, in_yi, change_pct}]
      - migrations: [{from, to, scale_yi, strength, type, date}]
      - briefing: 文字简报字符串
    """
    enriched = []
    for b in boards or []:
        nf = b.get("net_flow_yi") or 0
        to = b.get("turnover_yi") or 0
        chg = b.get("change_pct") or 0
        enriched.append({
            **b,
            "momentum_pct": _momentum(b),
            "_ratio":       -nf if nf < 0 else nf,
        })
    outflows = sorted(
        [b for b in enriched if (b.get("net_flow_yi") or 0)
            <= -_config.ROTATION_OUTFLOW_MIN_YI],
        key=lambda x: x["net_flow_yi"])[:top_outflow_n]
    inflows = sorted(
        [b for b in enriched if (b.get("net_flow_yi") or 0)
            >= _config.ROTATION_INFLOW_MIN_YI],
        key=lambda x: -x["net_flow_yi"])[:top_inflow_n]

    # 配对迁徙:从前 K 大流出,挨个匹配前 K 大流入,按规模相似度配对
    migrations = []
    for i, src in enumerate(outflows):
        if i >= len(inflows):
            break
        dst = inflows[i]
        scale = min(-src["net_flow_yi"], dst["net_flow_yi"])
        if scale < _config.ROTATION_OUTFLOW_MIN_YI:
            break
        divergence = (src.get("change_pct") or 0) - (dst.get("change_pct") or 0)
        strength = _rotation_strength(-src["net_flow_yi"], dst["net_flow_yi"], divergence)
        if strength >= _config.ROTATION_STRENGTH_STRONG:
            rtype = "main_switch"
            rlabel = "主线切换"
        elif strength >= _config.ROTATION_STRENGTH_MID:
            rtype = "themed_in"
            rlabel = "题材内轮动"
        else:
            rtype = "pulse"
            rlabel = "脉冲套利"
        migrations.append({
            "from":          src["name"],
            "to":            dst["name"],
            "scale_yi":      round(scale, 3),
            "strength":      strength,
            "type":          rtype,
            "type_label":    rlabel,
            "divergence_pp": round(divergence, 2),
            "src_change":    src.get("change_pct"),
            "dst_change":    dst.get("change_pct"),
        })

    # 文字简报
    if migrations:
        top = max(migrations, key=lambda m: m["scale_yi"])
        briefing = (
            f"🔁 当前最强资金切换: {top['from']} → {top['to']} "
            f"({top['type_label']}, {top['scale_yi']} 亿, 强度 {top['strength']}); "
            f"旧热点退潮: " +
            ", ".join(b['name'] for b in outflows[:3]) +
            "; 新崛起主线: " +
            ", ".join(b['name'] for b in inflows[:3])
        )
    else:
        briefing = "今日未识别到明显资金轮动(板块净流入 / 流出均低于阈值)。"

    # Sankey 节点 + 边
    nodes_set, links = [], []
    node_index = {}
    for b in outflows:
        n = b["name"]
        if n not in node_index:
            node_index[n] = len(nodes_set)
            nodes_set.append({
                "name":      n,
                "side":      "out",
                "amount_yi": round(-b["net_flow_yi"], 3),
                "change":    b.get("change_pct"),
            })
    for b in inflows:
        n = b["name"]
        if n not in node_index:
            node_index[n] = len(nodes_set)
            nodes_set.append({
                "name":      n,
                "side":      "in",
                "amount_yi": round(b["net_flow_yi"], 3),
                "change":    b.get("change_pct"),
            })
    for m in migrations:
        s = node_index[m["from"]]
        d = node_index[m["to"]]
        links.append({
            "source":   s,
            "target":   d,
            "value":    m["scale_yi"],
            "type":     m["type"],
            "strength": m["strength"],
        })

    return {
        "ts":         time.time(),
        "outflow":    [{
            "name": b["name"], "out_yi": round(-b["net_flow_yi"], 3),
            "change_pct": b.get("change_pct"),
            "momentum_pct": b["momentum_pct"],
        } for b in outflows],
        "inflow":     [{
            "name": b["name"], "in_yi": round(b["net_flow_yi"], 3),
            "change_pct": b.get("change_pct"),
            "momentum_pct": b["momentum_pct"],
        } for b in inflows],
        "migrations": migrations,
        "sankey":     {"nodes": nodes_set, "links": links},
        "briefing":   briefing,
    }


# ───────────────────────────────────────────────────────────
# 模块 3:热点龙头打分
# ───────────────────────────────────────────────────────────
def score_hotspot(boards: list[dict], lhb_by_code: dict[str, list[dict]],
                  *, top_n: int | None = None) -> list[dict]:
    """综合打分(资金 40 + 游资 35 + 行情 25),分级输出。

    lhb_by_code = {code: [lhb_rows]}  — 调用方应负责灌好。
    """
    top_n = top_n or _config.HOTSPOT_TOP_N
    out = []
    for b in boards or []:
        nf = b.get("net_flow_yi") or 0
        to = b.get("turnover_yi") or 0
        chg = b.get("change_pct") or 0
        # 资金分:0-100, 5 亿+ 满分
        fund_score = max(0.0, min(100.0, nf * 20.0))
        # 行情分:0-100, 涨幅 5% 满分
        mom_score = max(0.0, min(100.0, chg * 20.0))
        # 游资分:由本板块内成份股龙虎榜席位分类聚合得到
        seat_score = 0.0
        seat_struct = {k: 0.0 for k in SEAT_USER6_ORDER}
        total_wan = 0.0
        top_seats = []
        for code in (b.get("component_codes") or [])[:10]:
            rows = lhb_by_code.get(code) or []
            for r in rows:
                seat = (r.get("seat_name") or "").strip()
                amt = float(r.get("net_wan") or 0)
                cat8 = _seat.classify_seat(seat)
                cat6 = _map_seat_to_user6(cat8)
                seat_struct[cat6] += amt
                total_wan += abs(amt)
                alias = _seat.get_hot_money_alias(seat)
                if alias and cat6 == "hot_tier1" and abs(amt) > _config.HOTSPOT_MIN_LHB_AMOUNT_WAN:
                    if len(top_seats) < 6:
                        top_seats.append({
                            "code":      code,
                            "name":      r.get("name", ""),
                            "seat":      seat,
                            "alias":     alias[0],
                            "amount_wan": round(amt, 2),
                        })
        if total_wan > 0:
            tier1_share = max(0.0, seat_struct["hot_tier1"]) / total_wan
            tier2_share = max(0.0, seat_struct["hot_tier2"]) / total_wan
            retail_share = max(0.0, seat_struct["retail_lhasa"]) / total_wan
            # 顶级游资权重 > 二线 > 散户(负向)
            seat_score = (
                tier1_share * 100.0 * 0.55
                + tier2_share * 100.0 * 0.30
                - retail_share * 100.0 * 0.15
            )
            seat_score = max(0.0, min(100.0, seat_score + 40))   # 0-100 摆正
        # 加权得分
        total_score = (
            fund_score  * _config.HOTSPOT_FUND_WEIGHT
            + seat_score * _config.HOTSPOT_SEAT_WEIGHT
            + mom_score  * _config.HOTSPOT_MOMENTUM_WEIGHT
        )
        if total_score >= 75:
            tier = "core_main"; tier_label = "核心主线龙头"
        elif total_score >= 55:
            tier = "secondary";  tier_label = "次级跟风热点"
        elif total_score >= 35:
            tier = "pulse";      tier_label = "脉冲短期题材"
        else:
            tier = "cold";       tier_label = "冷门弱势板块"

        # 资金结构占比(规一化)
        struct_pct = {}
        if total_wan > 0:
            for k in SEAT_USER6_ORDER:
                struct_pct[k] = round(
                    100.0 * max(0.0, seat_struct[k]) / total_wan, 1)
        else:
            struct_pct = {k: 0.0 for k in SEAT_USER6_ORDER}

        out.append({
            "name":         b.get("name"),
            "kind":         b.get("kind", "industry"),
            "code":         b.get("code", ""),
            "score":        round(total_score, 1),
            "fund_score":   round(fund_score, 1),
            "seat_score":   round(seat_score, 1),
            "momentum_score": round(mom_score, 1),
            "tier":         tier,
            "tier_label":   tier_label,
            "net_3d_yi":    round(nf, 3),               # 当前为单日,前端可显示名义
            "change_pct":   chg,
            "turnover_yi":  to,
            "leader_code":  b.get("leader_code", ""),
            "leader_name":  b.get("leader_name", ""),
            "seat_struct_pct": struct_pct,
            "top_seats":    top_seats,
        })
    out.sort(key=lambda x: -x["score"])
    return out[:top_n]


# ───────────────────────────────────────────────────────────
# 板块成份股查 — 给热点打分拉龙虎榜用
# ───────────────────────────────────────────────────────────
def fetch_components(name: str, *, max_n: int = 10) -> list[str]:
    """拉取板块前 N 只成份股代码(供后续查龙虎榜 / 板块强度)。"""
    if not name:
        return []
    # dev mock: 沙箱 / 数据源挂时,根据板块名 hash 给定 stable 的合成代码
    # (不用真实数据,但能保证 score_hotspot 跑出合理分级)
    if os.environ.get("TUIXUE_DEV_MOCK_BOARDS") == "1":
        base = sum(ord(c) for c in name)
        return [f"{(base + i * 17) % 600000 + 600000:06d}" for i in range(max_n)][:max_n]
    raw = _safe_ak(
        "stock_board_industry_cons_em",
        symbol=name,
        default=[],
        timeout=_config.ROTATION_FUND_FETCH_TIMEOUT,
    ) or _safe_ak(
        "stock_board_concept_cons_em",
        symbol=name,
        default=[],
        timeout=_config.ROTATION_FUND_FETCH_TIMEOUT,
    )
    out = []
    for r in raw or []:
        code = (r.get("代码") or r.get("code") or "").strip()
        if re.fullmatch(r"\d{6}", code):
            out.append(code)
        if len(out) >= max_n:
            break
    return out
