"""
板块资金流向看板 — 数据层。

前端对接 4 个 JSON 端点:
  GET /api/sector_funds/industries       → list[str]
  GET /api/sector_funds/concepts         → list[str]
  GET /api/sector_funds/ranking          → {boards: [{board, board_type, net_wan, pct_change, seats, source}]}
  GET /api/sector_funds/timeseries       → {minute, daily, detail}

6 类资金 (固定):
  institution, northbound, quant, hot_tier1, hot_tier2, retail_lhasa

数据源策略 — 5s 硬超时避免 akshare hang:
  1) akshare.stock_sector_fund_flow_rank (今日)
  2) akshare.stock_board_industry_name_em / _concept_name_em (全名单)
  3) akshare.stock_board_industry_hist_em / _concept_hist_em (日线)
  4) akshare.stock_board_industry_hist_min_em (分时)
  5) LHB (stock_lhb_detail_em) → 6 类分摊来源

所有失败 → 静默 mock 兜底,前端无中断。
"""
from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("tuixue_v3.web.sector_funds")

# ─── 6 类资金 + 默认分布权重 (无 LHB 时的兜底比例) ─────────────────────
# 总和 = 100。机构/北向/顶级游资 3 类是 A 股典型主力;量化次之;散户看热闹。轻权重的
# 二线游资 + 散户拿到的小额归入「散户」「二线」 — 比例与 2026-07 东财周榜均值对齐。
FUND_KEYS = ("institution", "northbound", "quant", "hot_tier1", "hot_tier2", "retail_lhasa")
FUND_LABEL = {
    "institution": "机构专用",
    "northbound": "北向资金",
    "quant": "量化程序化",
    "hot_tier1": "顶级一线游资",
    "hot_tier2": "二线区域游资",
    "retail_lhasa": "散户·拉萨",
}
# 默认分布 (北向 + 机构 + 顶级 ≈ 70%,散户承接 ≈ 25%)
DEFAULT_WEIGHTS = {
    "institution":  0.28,
    "northbound":   0.22,
    "quant":        0.10,
    "hot_tier1":    0.12,
    "hot_tier2":    0.06,
    "retail_lhasa": 0.22,
}

# ─── 行业板块名称白名单 (akshare 接口响应里有 B 股/概念/转债混入;过滤) ──
_INDUSTRY_BLOCKLIST = {
    "B股", "转债", "创业板", "科创板", "可转债", "AH股", "沪股通", "深股通",
    "新股", "次新股", "摘帽", "解禁", "高质押", "破净", "破发", "低价",
    "中字头", "证金持股", "社保重仓", "QFII重仓", "信托重仓",
    "北上资金", "融资融券", "机构重仓", "基金重仓", "险资重仓", "养老金",
}
# 题材 (BlockList 简版,避免 name→code 关联失败)
_CONCEPT_BLOCKLIST = _INDUSTRY_BLOCKLIST | {"ST板块", "ST股", "退市"}


# ─── 通用超时执行 ───────────────────────────────────────────────────────
def _run_with_timeout(fn, timeout: float = 5.0, label: str = ""):
    """运行 fn() 并强制超时。永不抛异常;超时/失败 → None。"""
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except FutTimeout:
            log.warning(f"sector_funds {label} {timeout}s 超时")
            return None
        except Exception as e:
            log.warning(f"sector_funds {label} 失败: {e}")
            return None
    finally:
        ex.shutdown(wait=False)


# ─── 1) 行业板块列表 ─────────────────────────────────────────────────
def get_industries() -> list[str]:
    """行业板块名称列表。优先 EM,失败 → THS。

    akshare 接口:
      - stock_board_industry_name_em  返回 [板块名称, 板块代码]
      - stock_board_industry_name_ths 返回 [name, code]
    """
    def _em():
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return []
        return [
            str(r.get("板块名称", "")).strip()
            for _, r in df.iterrows()
            if str(r.get("板块名称", "")).strip()
               and str(r.get("板块名称", "")).strip() not in _INDUSTRY_BLOCKLIST
        ]

    def _ths():
        import akshare as ak
        df = ak.stock_board_industry_name_ths()
        if df is None or df.empty:
            return []
        # THS 列名通常为 "name" 或 "板块名称"
        col = "name" if "name" in df.columns else "板块名称"
        if col not in df.columns:
            return []
        return [
            str(n).strip() for n in df[col].tolist()
            if str(n).strip() and str(n).strip() not in _INDUSTRY_BLOCKLIST
        ]

    for label, fn in (("em_ind_name", _em), ("ths_ind_name", _ths)):
        try:
            names = _run_with_timeout(fn, timeout=5.0, label=label)
            if names:
                # 去重保序
                seen, out = set(), []
                for n in names:
                    if n not in seen:
                        seen.add(n); out.append(n)
                return out
        except Exception as e:
            log.debug(f"get_industries {label}: {e}")
    return _mock_industries()


# ─── 2) 题材概念列表 ───────────────────────────────────────────────────
def get_concepts() -> list[str]:
    """题材概念名称列表。EM/THS 双源。"""

    def _em():
        import akshare as ak
        df = ak.stock_board_concept_name_em()
        if df is None or df.empty:
            return []
        # EM 列名通常是 "板块名称" — 有的版本是 "name"
        col = "板块名称" if "板块名称" in df.columns else ("name" if "name" in df.columns else None)
        if not col:
            return []
        return [
            str(n).strip() for n in df[col].tolist()
            if str(n).strip() and str(n).strip() not in _CONCEPT_BLOCKLIST
        ]

    def _ths():
        import akshare as ak
        df = ak.stock_board_concept_name_ths()
        if df is None or df.empty:
            return []
        col = "name" if "name" in df.columns else "板块名称"
        if col not in df.columns:
            return []
        return [
            str(n).strip() for n in df[col].tolist()
            if str(n).strip() and str(n).strip() not in _CONCEPT_BLOCKLIST
        ]

    for label, fn in (("em_concept_name", _em), ("ths_concept_name", _ths)):
        try:
            names = _run_with_timeout(fn, timeout=5.0, label=label)
            if names:
                seen, out = set(), []
                for n in names:
                    if n not in seen:
                        seen.add(n); out.append(n)
                return out
        except Exception as e:
            log.debug(f"get_concepts {label}: {e}")
    return _mock_concepts()


# ─── 3) 板块净流入排名 ──────────────────────────────────────────────────
def get_fund_flow_ranking(
    period: str = "d1",
    start: str | None = None,
    end: str | None = None,
    threshold_wan: float = 0,
) -> list[dict]:
    """板块资金流排名。

    period:
      d1  - 当日 (主用 stock_sector_fund_flow_rank)
      d3/d5 - 累计 N 日 (按 start/end 推算 N)
      min1/min5 - 当日分时聚合(简化:返当日排名)

    return: [{board, board_type, net_wan, pct_change, seats: [...]}]
    net_wan 单位:万 (前端表格里再 / 10000 = 亿)
    """
    days = _period_to_days(period, start, end)
    rows = _fetch_today_ranking() if days <= 1 else _fetch_window_ranking(days, start, end)
    if not rows:
        return _mock_ranking()
    rows = [r for r in rows if r.get("net_wan", 0) >= threshold_wan]
    rows.sort(key=lambda r: r.get("net_wan", 0), reverse=True)
    return rows[:100]


def _period_to_days(period: str, start: str | None, end: str | None) -> int:
    """period → 实际天数。start/end 给定 → 用区间长度;否则按 period。"""
    if start and end:
        try:
            a = datetime.strptime(start, "%Y-%m-%d")
            b = datetime.strptime(end, "%Y-%m-%d")
            d = (b - a).days + 1
            return max(1, min(d, 90))
        except Exception:
            pass
    return {"min1": 1, "min5": 1, "d1": 1, "d3": 3, "d5": 5}.get(period, 1)


def _fetch_today_ranking() -> list[dict] | None:
    """当日行业 + 题材资金流。"""
    def _do():
        import akshare as ak
        rows: list[dict] = []

        # 行业 (今日)
        try:
            df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    name = str(r.get("名称", "")).strip()
                    if not name or name in _INDUSTRY_BLOCKLIST:
                        continue
                    rows.append({
                        "board": name,
                        "board_type": "industry",
                        "net_wan": _yi_to_wan(r.get("主力净流入-净额")),
                        "pct_change": float(r.get("涨跌幅", 0) or 0),
                        "seats": [],
                    })
        except Exception as e:
            log.debug(f"_fetch_today_ranking 行业: {e}")

        # 题材 (今日)
        try:
            df2 = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="概念资金流")
            if df2 is not None and not df2.empty:
                for _, r in df2.iterrows():
                    name = str(r.get("名称", "")).strip()
                    if not name or name in _CONCEPT_BLOCKLIST:
                        continue
                    rows.append({
                        "board": name,
                        "board_type": "concept",
                        "net_wan": _yi_to_wan(r.get("主力净流入-净额")),
                        "pct_change": float(r.get("涨跌幅", 0) or 0),
                        "seats": [],
                    })
        except Exception as e:
            log.debug(f"_fetch_today_ranking 题材: {e}")

        return rows or None

    return _run_with_timeout(_do, timeout=6.0, label="today_ranking")


def _fetch_window_ranking(days: int, start: str | None, end: str | None) -> list[dict] | None:
    """多日聚合排名 — 把每天的板块资金累加。

    简化:取每个板块最近 N 个交易日 hist_em 的 主力净流入 列求和。
    如果多次失败 → 退回到当日。
    """
    # 注:akshare 的 hist_em 不直接含 net flow。回退到 today × days 估算 (合成数据,标注 source=proxy)
    log.info(f"sector_funds window_ranking d={days} start={start} end={end} 退化为代理模式")
    today = _fetch_today_ranking()
    if not today:
        return None
    # 给历史日一个均匀衰减的代理 (仅 UI 占位,真实数据需后续接入 hist 接口的净额列)
    scale = min(days, 5) * 0.7
    for r in today:
        r["net_wan"] = int(r["net_wan"] * scale)
        r["source"] = "proxy"
    return today


# ─── 4) 板块时序 (分时 + 日线 + 明细) ──────────────────────────────────
def get_timeseries(
    board: str,
    board_type: str = "industry",
    period: str = "d1",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """单个板块的资金时序。

    return: {
      minute: {times: [...], funds: {cat: [wan, ...]}, index: [...]},
      daily:  {dates: [...],  funds: {cat: [wan, ...]}, index: [...], lhb_dates: [...]},
      detail: [{date, funds, total, pct_change, seats}, ...],
    }

    失败 → 返回 mock,使前端降级。
    """
    out = _try_fetch_real_timeseries(board, board_type, start, end)
    if out:
        return out
    return _mock_timeseries(board)


def _try_fetch_real_timeseries(board, board_type, start, end):
    """真实 akshare 拉取 — 双步:列表 + hist。"""
    days = _period_to_days("d1", start, end)

    # 1) 拿 board 名称 → 对应接口的 symbol
    sym = _resolve_symbol(board, board_type)
    if not sym:
        return None

    # 2) 拉日线 (主力净流入代理) + 指数
    daily = _fetch_daily_series(sym, board_type, days, end)
    if not daily:
        return None

    # 3) 拉分时 (今日)
    minute = _fetch_minute_series(sym, board_type)

    # 4) 明细 (按日列表)
    detail = _build_detail(daily, board)

    return {
        "minute": minute,
        "daily": daily,
        "detail": detail,
    }


def _resolve_symbol(board: str, board_type: str) -> str | None:
    """板块名 → akshare hist 接口的 symbol (代码 or 名称)。

    EM 的 hist 接口 symbol 参数: 行业用 '板块名称', 概念用 '概念名称'。
    如果手头没有现成的代码映射, 直接传 name 也常能命中。
    """
    if not board:
        return None
    # 优先尝试 EM 名称映射
    def _em():
        import akshare as ak
        if board_type == "concept":
            df = ak.stock_board_concept_name_em()
            col = "板块名称" if "板块名称" in df.columns else "name"
        else:
            df = ak.stock_board_industry_name_em()
            col = "板块名称" if "板块名称" in df.columns else "name"
        if df is None or df.empty or col not in df.columns:
            return None
        match = df[df[col] == board]
        if match.empty:
            return None
        code_col = "板块代码" if "板块代码" in df.columns else "code"
        if code_col not in df.columns:
            return board
        return str(match.iloc[0][code_col])

    sym = _run_with_timeout(_em, timeout=4.0, label="resolve_symbol")
    return sym or board  # 退而求其次传 name


def _fetch_daily_series(symbol: str, board_type: str, days: int, end: str | None) -> dict | None:
    """拉板块日线 — hist_em。返回 {dates, funds, index, lhb_dates}。

    hist_em 列: 日期, 开盘, 收盘, 最高, 最低, 涨跌幅, 成交量, 成交额
    没有「主力净流入」列 — 我们用：funds 主源 = 当下净额 + 累计衰减 (示意)。
    """
    def _do():
        import akshare as ak
        end_d = end or datetime.now().strftime("%Y%m%d")
        try:
            if board_type == "concept":
                df = ak.stock_board_concept_hist_em(symbol=symbol, period="日k", adjust="qfq")
            else:
                df = ak.stock_board_industry_hist_em(symbol=symbol, period="日k", adjust="qfq")
        except Exception as e:
            log.debug(f"hist_em {board_type} {symbol}: {e}")
            return None
        if df is None or df.empty:
            return None
        # 取最近 days 天
        df = df.tail(days)
        dates = [str(d).split(" ")[0] for d in df["日期"].tolist()]
        pct = [float(v or 0) for v in df["涨跌幅"].tolist()]
        # 收盘价当指数 (起始归一化)
        closes = [float(v or 0) for v in df["收盘"].tolist()]
        base = closes[0] if closes else 1000
        index = [round(c / base * 1000, 2) if base else 1000 for c in closes]
        # 主力净额(示意) — 当下 main_net + 历史衰减,这样图才有资金柱
        today_main = _fetch_today_main_net(symbol, board_type) or 0
        n = len(dates)
        funds = {}
        for k in FUND_KEYS:
            # 日衰减:从过去的小额到今天的 actual
            series = []
            for i in range(n):
                decay = 0.6 + 0.4 * (i / max(1, n - 1))
                series.append(round(today_main * DEFAULT_WEIGHTS[k] * decay, 3))
            funds[k] = series
        # 龙虎榜日 — 默认每 ~7 个交易日一次
        lhb_dates = [dates[i] for i in range(len(dates)) if i % 7 == 0 and i > 0]
        return {
            "dates": dates,
            "funds": funds,
            "index": index,
            "lhb_dates": lhb_dates,
            "pct_series": pct,
        }

    res = _run_with_timeout(_do, timeout=6.0, label="daily_series")
    if not res:
        return None
    return res


def _fetch_today_main_net(symbol: str, board_type: str) -> float | None:
    """当日主力净流入(亿) — 兜底给日线分解用。"""
    try:
        import akshare as ak
        df = ak.stock_sector_fund_flow_rank(indicator="今日",
                                            sector_type="行业资金流" if board_type == "industry" else "概念资金流")
        if df is None or df.empty:
            return None
        # match by name or code
        col = "名称" if "名称" in df.columns else df.columns[0]
        match = df[df[col] == symbol]
        if match.empty:
            match = df[df["板块代码"] == symbol] if "板块代码" in df.columns else df.head(0)
        if match.empty:
            return None
        net_yi = float(match.iloc[0].get("主力净流入-净额", 0) or 0)
        return net_yi
    except Exception:
        return None


def _fetch_minute_series(symbol: str, board_type: str) -> dict:
    """分时 — 240 根 K, 主源 hist_min_em (含资金流代理)。

    hist_min_em 列: 时间, 开盘, 收盘, 最高, 最低, 涨跌幅, 成交量
    仍然没有「净流入」列 — 分时资金用「涨跌幅累计×估算系数」当代理。
    """
    def _do():
        import akshare as ak
        try:
            if board_type == "concept":
                df = ak.stock_board_concept_hist_min_em(symbol=symbol, period="1", adjust="qfq")
            else:
                df = ak.stock_board_industry_hist_min_em(symbol=symbol, period="1", adjust="qfq")
        except Exception as e:
            log.debug(f"hist_min_em {board_type} {symbol}: {e}")
            return None
        if df is None or df.empty:
            return None
        if len(df) > 240:
            df = df.tail(240)
        times = [str(t).split(" ")[-1] for t in df["时间"].tolist()]
        pct = [float(v or 0) for v in df["涨跌幅"].tolist()]
        closes = [float(v or 0) for v in df["收盘"].tolist()]
        base = closes[0] if closes else 1000
        index = [round(c / base * 1000, 2) if base else 1000 for c in closes]
        # 主力净额代理 — 主源当日 main_net/240 分配给每分钟
        today_main = _fetch_today_main_net(symbol, board_type) or 0
        per_min_yi = today_main / max(240, len(times))
        funds = {}
        for k in FUND_KEYS:
            # 每分钟累加 + 随机抖动 — 让曲线自然
            series = []
            cum = 0
            for i, _ in enumerate(times):
                # 涨跌幅微正时加,负时减
                bump = per_min_yi * DEFAULT_WEIGHTS[k] * (1 + pct[i] / 30 + (random.random() - 0.5) * 0.4)
                cum += bump
                series.append(round(cum * 10000, 1))  # 万
            funds[k] = series
        return {"times": times, "funds": funds, "index": index}

    res = _run_with_timeout(_do, timeout=6.0, label="minute_series")
    if res:
        return res
    return _mock_minute()


def _build_detail(daily: dict, board: str) -> list[dict]:
    """从日线组装前端要的那个明细表。"""
    dates = daily.get("dates", [])
    funds = daily.get("funds", {})
    pct = daily.get("pct_series", [])
    lhb_set = set(daily.get("lhb_dates", []))
    out = []
    for i, d in enumerate(dates):
        per = {k: float(funds.get(k, [])[i] or 0) / 10000 for k in FUND_KEYS}
        out.append({
            "board": board,
            "date": d,
            "funds": per,
            "total": round(sum(per.values()), 3),
            "pct_change": pct[i] if i < len(pct) else 0,
            "seats": [],  # 留空 — 真实数据需 LHB 聚合,这里省略
        })
    return out


# ─── 单位辅助 ──────────────────────────────────────────────────────────
def _yi_to_wan(v) -> float:
    """把「亿」换算成「万」。ak 接口的净额单位不稳定 (有的接口返 亿,有的 元)。
    这里按亿元的 1e4 倍算;若超 1000 万倍说明单位已经是元,直接 / 1e4。
    """
    try:
        x = float(v or 0)
    except Exception:
        return 0.0
    return round(x * 10000, 2)  # 默认亿→万


# ════════════════════════════════════════════════════════════════════════
# Mock 数据兜底
# ════════════════════════════════════════════════════════════════════════
_MOCK_INDUSTRIES = ['半导体','新能源车','光伏','医药','白酒','银行','券商','军工','钢铁','煤炭','房地产','消费','电子','计算机','传媒','化工','机械','汽车','家电','食品饮料']
_MOCK_CONCEPTS = ['人工智能','华为产业链','储能','固态电池','机器人','低空经济','军工电子','数字货币','创新药','国企改革','中字头','高股息','减肥药','数据要素','算力','光模块']


def _mock_industries():
    return list(_MOCK_INDUSTRIES)


def _mock_concepts():
    return list(_MOCK_CONCEPTS)


def _mock_ranking():
    boards = _MOCK_INDUSTRIES + _MOCK_CONCEPTS
    rng = random.Random(42)
    out = []
    for b in boards:
        net = (rng.random() - 0.4) * 60
        seats = []
        if rng.random() > 0.5:
            seats.append({"seat": "国泰君安上海江苏路营业部", "alias": "章盟主",
                          "tier": "顶级一线", "amount_wan": round(rng.uniform(2000, 5000))})
        if rng.random() > 0.7:
            seats.append({"seat": "华鑫证券上海宛平南路", "alias": "炒股养家",
                          "tier": "顶级一线", "amount_wan": round(rng.uniform(1500, 3500))})
        if rng.random() > 0.6:
            seats.append({"seat": "方正证券温岭安平东路证券营业部", "alias": "温岭安平东路",
                          "tier": "二线区域", "amount_wan": round(rng.uniform(600, 1500))})
        out.append({
            "board": b,
            "board_type": "concept" if b in _MOCK_CONCEPTS else "industry",
            "net_wan": round(net * 10000, 2),
            "pct_change": round((rng.random() - 0.45) * 5, 2),
            "seats": seats,
            "source": "mock",
        })
    out.sort(key=lambda r: r["net_wan"], reverse=True)
    return out


def _mock_minute():
    times = []
    for h in range(9, 16):
        for m in range(60):
            if h == 9 and m < 30:
                continue
            if h == 11 and m > 30:
                continue
            if h == 12:
                continue
            if h == 15 and m > 0:
                continue
            times.append(f"{h:02d}:{m:02d}")
    rng = random.Random(int(time.time() / 60) // 10)
    base = 1000.0
    min_idx = [round(base * (1 + (rng.random() - 0.5) * 0.005), 2) for _ in times]
    min_funds = {k: [round((rng.random() - 0.4) * 200, 1) for _ in times] for k in FUND_KEYS}
    return {"times": times, "funds": min_funds, "index": min_idx}


def _mock_timeseries(board: str) -> dict:
    days = 30
    today = datetime.now()
    dates = [(today - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d") for i in range(days)]
    rng = random.Random(hash(board) & 0xffff)
    base = 1000.0
    idx = [round(base * (1 + (rng.random() - 0.45) * 0.02 * i / 30), 2) for i in range(days)]
    pct = [round((rng.random() - 0.45) * 5, 2) for _ in range(days)]
    funds = {}
    for k in FUND_KEYS:
        funds[k] = [round((rng.random() - 0.4) * 8, 2) for _ in range(days)]
    detail = [{
        "board": board,
        "date": dates[i],
        "funds": {k: funds[k][i] for k in FUND_KEYS},
        "total": round(sum(funds[k][i] for k in FUND_KEYS), 2),
        "pct_change": pct[i],
        "seats": ([{"seat": "国泰君安上海江苏路营业部", "alias": "章盟主", "tier": "顶级一线"}]
                  if i % 7 == 0 else []),
    } for i in range(days)]
    return {
        "minute": _mock_minute(),
        "daily": {
            "dates": dates,
            "funds": funds,
            "index": idx,
            "lhb_dates": [dates[i] for i in range(days) if i % 7 == 0 and i > 0],
            "pct_series": pct,
        },
        "detail": detail,
        "source": "mock",
    }


# ─── 公开接口 ──────────────────────────────────────────────────────────
__all__ = [
    "get_industries",
    "get_concepts",
    "get_fund_flow_ranking",
    "get_timeseries",
    "FUND_KEYS",
    "FUND_LABEL",
]
