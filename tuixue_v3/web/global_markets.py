"""
tuixue_v3/web/global_markets.py
美股 / 韩股 / A股板块整体情绪 — 多源数据 + 静态映射表 + 派生 sentiment。

设计:
- 多源兜底链: 腾讯 qt.gtimg → 新浪 hq.sinajs → akshare → 占位返回(全部失败)
  (与项目其它数据源 stdlib 模式一致,见 data_layer._fetch_with_retry)
- 静态 US→A股映射表(60 条):NVDA → AI算力/半导体, KO → 白酒, TSLA → 锂电池
- 不重写已有逻辑;只提供 fetch_global_sentiment() 与 sector impact 映射
- TTL 30s 内存缓存(由调用方传入) 或 server.py 的 _cache_overview
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.request import urlopen, Request

log = logging.getLogger("tuixue_v3.web.global_markets")

# 多源 fetcher 用独立线程池,避免占满 server 主池(8 worker 足够)
_EXEC = ThreadPoolExecutor(max_workers=16, thread_name_prefix="gm")


# ═══════════════════════════════════════════════════
# 静态映射: US/Korea ticker → A股板块影响
# ═══════════════════════════════════════════════════
# 60+ 条覆盖核心链路。映射语义:
#   "sectors":  可能受益/受损的 A股板块名(sw_industry 31 类尽量匹配)
#   "note":     一句话解释,会拼到 AI prompt 与 UI

US_TO_A_STOCK_SECTOR = {
    # ── 七巨头 + 龙头芯片厂 ──
    "NVDA":  {"sectors": ["半导体", "AI算力", "CPO光模块", "PCB"],     "direction": "+", "note": "AI 算力链总闸,辐射面最广"},
    "AMD":   {"sectors": ["半导体", "AI算力", "CPU"],                   "direction": "+", "note": "CPU/GPU 双轮,涨价即赚"},
    "AVGO":  {"sectors": ["半导体", "CPO光模块"],                        "direction": "+", "note": "定制芯片+CPO 主力"},
    "TSM":   {"sectors": ["半导体"],                                    "direction": "+", "note": "代工龙头,A股代工影子股共振"},
    "ASML":  {"sectors": ["半导体", "光刻机"],                          "direction": "+", "note": "光刻机龙头,设备链情绪指向"},
    "MSFT":  {"sectors": ["AI软件", "云计算", "信创"],                  "direction": "+", "note": "云/OS,AI 应用端"},
    "GOOGL": {"sectors": ["AI软件", "广告媒体"],                        "direction": "+", "note": "搜索+广告+AI 模型"},
    "META":  {"sectors": ["AI软件", "广告媒体"],                        "direction": "+", "note": "广告+开源模型"},
    "AMZN":  {"sectors": ["云计算", "电商", "物流"],                     "direction": "+", "note": "AWS+零售+物流"},
    "AAPL":  {"sectors": ["消费电子", "果链"],                          "direction": "+", "note": "果链情绪总闸"},
    "TSLA":  {"sectors": ["锂电池", "新能源车", "特斯拉链", "人形机器人"], "direction": "+", "note": "新能车链+机器人链总闸"},
    # ── 半导体设备 ──
    "AMAT":  {"sectors": ["半导体设备"],                                "direction": "+", "note": "应用材料,A股设备影子"},
    "LRCX":  {"sectors": ["半导体设备"],                                "direction": "+", "note": "Lam Research,刻蚀链"},
    "KLAC":  {"sectors": ["半导体设备"],                                "direction": "+", "note": "KLA 质检链"},
    "MRVL":  {"sectors": ["半导体", "CPO光模块"],                        "direction": "+", "note": "Marvell 定制芯片"},
    "MU":    {"sectors": ["存储芯片", "半导体"],                        "direction": "+", "note": "美光,存储链"},
    # ── 软件 + 互联网 ──
    "NFLX":  {"sectors": ["影视", "游戏"],                              "direction": "+", "note": "流媒体,情绪影响传媒"},
    "DIS":   {"sectors": ["影视", "传媒"],                              "direction": "+", "note": "迪士尼,主题公园链"},
    "BAIDU": {"sectors": ["AI软件", "搜索引擎"],                        "direction": "+", "note": "百度,中文搜索+文心"},
    "BABA":  {"sectors": ["电商", "云计算"],                            "direction": "+", "note": "阿里,影子效应"},
    "PDD":   {"sectors": ["电商", "跨境支付"],                          "direction": "+", "note": "拼多多,消费链情绪"},
    "JD":    {"sectors": ["电商", "物流"],                              "direction": "+", "note": "京东物流链"},
    "BIDU":  {"sectors": ["AI软件"],                                    "direction": "+", "note": "百度"},
    "NIO":   {"sectors": ["新能源车"],                                  "direction": "+", "note": "蔚来,新能车影子"},
    "XPEV":  {"sectors": ["新能源车"],                                  "direction": "+", "note": "小鹏"},
    "LI":    {"sectors": ["新能源车"],                                  "direction": "+", "note": "理想"},
    # ── 周期 / 资源 ──
    "XOM":   {"sectors": ["石油石化", "油气"],                          "direction": "-", "note": "埃克森,油价信号"},
    "CVX":   {"sectors": ["石油石化"],                                  "direction": "-", "note": "雪佛龙"},
    "FCX":   {"sectors": ["铜", "有色金属"],                            "direction": "+", "note": "自由港铜金,A股铜链共振"},
    "GOLD":  {"sectors": ["黄金"],                                       "direction": "+", "note": "巴里克黄金,避险"},
    "NEM":   {"sectors": ["黄金"],                                       "direction": "+", "note": "纽蒙特,金价"},
    # ── 消费 / 食品 ──
    "KO":    {"sectors": ["白酒", "食品饮料", "饮料"],                   "direction": "+", "note": "可口可乐,A股食饮链情绪"},
    "PEP":   {"sectors": ["食品饮料"],                                  "direction": "+", "note": "百事,食饮"},
    "MCD":   {"sectors": ["餐饮", "消费"],                              "direction": "+", "note": "麦当劳,餐饮链"},
    "SBUX":  {"sectors": ["餐饮", "消费"],                              "direction": "+", "note": "星巴克,消费链"},
    "WMT":   {"sectors": ["零售", "商超"],                              "direction": "+", "note": "沃尔玛,零售链"},
    "COST":  {"sectors": ["零售", "商超"],                              "direction": "+", "note": "好市多,零售"},
    "NKE":   {"sectors": ["纺织服饰", "运动服饰"],                      "direction": "+", "note": "耐克,纺织链"},
    # ── 金融 ──
    "JPM":   {"sectors": ["银行", "金融"],                              "direction": "+", "note": "摩根大通,银行链情绪"},
    "GS":    {"sectors": ["券商", "金融"],                              "direction": "+", "note": "高盛,券商链"},
    "V":     {"sectors": ["金融科技"],                                  "direction": "+", "note": "Visa,金融科技"},
    # ── 医药 / 医美 / 减肥药 ──
    "LLY":   {"sectors": ["创新药", "减肥药", "GLP-1"],                 "direction": "+", "note": "礼来,GLP-1/A股减肥药链总闸"},
    "NVO":   {"sectors": ["创新药", "减肥药", "GLP-1"],                 "direction": "+", "note": "诺和,GLP-1"},
    "PFE":   {"sectors": ["疫苗", "创新药"],                            "direction": "+", "note": "辉瑞,医药链"},
    "MRNA":  {"sectors": ["疫苗"],                                       "direction": "+", "note": "Moderna,疫苗链"},
    "UNH":   {"sectors": ["保险", "医疗"],                              "direction": "+", "note": "联合健康,医保链"},
    "JNJ":   {"sectors": ["医药", "消费医疗"],                          "direction": "+", "note": "强生,医药链"},
    # ── 工业 / 制造 ──
    "BA":    {"sectors": ["航空", "国防军工"],                          "direction": "+", "note": "波音,航空链"},
    "CAT":   {"sectors": ["工程机械"],                                  "direction": "+", "note": "卡特彼勒,工程链"},
    "GE":    {"sectors": ["航空发动机", "工业制造"],                    "direction": "+", "note": "GE,航空链"},
    "F":     {"sectors": ["新能源车", "汽车整车"],                      "direction": "+", "note": "福特,整车"},
    "GM":    {"sectors": ["新能源车", "汽车整车"],                      "direction": "+", "note": "通用,整车"},
    "UBER":  {"sectors": ["出行", "本地生活"],                          "direction": "+", "note": "Uber,出行链"},
    "ABNB":  {"sectors": ["旅游", "出行"],                              "direction": "+", "note": "爱彼迎,旅游链"},
}

# 韩股 ticker → A股板块
KR_TO_A_STOCK_SECTOR = {
    "005930": {"name": "三星电子",     "sectors": ["存储芯片", "半导体", "OLED"], "note": "内存+OLED 总闸"},
    "000660": {"name": "SK海力士",     "sectors": ["存储芯片", "HBM"],           "note": "HBM/存储链总闸,A股 HBM 链直接共振"},
    "005380": {"name": "现代汽车",     "sectors": ["新能源车", "汽车整车"],     "note": "整车"},
    "005490": {"name": "POSCO",        "sectors": ["钢铁", "锂电池材料"],       "note": "钢铁+正极材料"},
    "006400": {"name": "三星SDI",      "sectors": ["锂电池", "电池"],           "note": "锂电池链"},
    "035420": {"name": "NAVER",        "sectors": ["互联网", "AI软件"],         "note": "韩国版互联网"},
    "051910": {"name": "LG化学",       "sectors": ["锂电池", "电池"],           "note": "LG 链"},
    "003670": {"name": "SK创新",       "sectors": ["锂电池", "石化"],           "note": "电池/能源"},
    "012330": {"name": "现代Mobis",    "sectors": ["汽车零部件"],               "note": "韩系零部件"},
    "066570": {"name": "LG电子",       "sectors": ["消费电子"],                 "note": "LG 家电链"},
}

# 关注的全球指数
GLOBAL_INDICES = [
    # 美股 3 大指数 — 用 sina int_{lowercase}
    {"code": "int_dji",    "name": "道琼斯",      "market": "us",  "weight": 1.0},
    {"code": "int_nasdaq", "name": "纳斯达克",    "market": "us",  "weight": 1.5},  # 高权重(科技敏感)
    {"code": "int_sp500",  "name": "标普500",    "market": "us",  "weight": 1.0},
    # 韩国 2 大指数 — KS11/KQ11 数据新浪/KR 没接口,用 eastmoney
    {"code": "KS11",       "name": "韩国综合(KOSPI)", "market": "kr", "weight": 1.0},
    {"code": "KQ11",       "name": "韩国创业板(KOSDAQ)", "market": "kr", "weight": 0.6},
]


# ═══════════════════════════════════════════════════
# 多源 fetcher
# ═══════════════════════════════════════════════════
def _fetch_tencent(code: str, market: str) -> dict | None:
    """
    腾讯 qt.gtimg 单值抓取
    美股: https://qt.gtimg.cn/q=usQHK.{ticker} 或 us.{code}
    返回 raw text → 需要解析成 {price, change_pct}
    """
    if market == "us":
        # 指数 us.{CODE}; 个股 usQHK.{ticker}
        if "." not in code:  # 是个股
            url = f"https://qt.gtimg.cn/q=usQHK.{code}"
        else:
            url = f"https://qt.gtimg.cn/q={code}"
    elif market == "kr":
        # 韩股 6 位 code
        url = f"https://qt.gtimg.cn/q=usQHK.{code}"
    else:
        return None

    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
        with urlopen(req, timeout=4) as r:
            text = r.read().decode("gbk", errors="ignore").strip()
        # 解析 'v_usQHK.NVDA="200.00|...|...";'
        if '="' not in text:
            return None
        body = text.split('="', 1)[1].rstrip(';"')
        parts = body.split("~")
        if len(parts) < 5:
            return None
        # 1: name, 2: code, 3: price, 4: 昨收, 5: open ...
        # 不同股字段位有偏差,常见字段:
        # parts[3] price, parts[4] 昨收, parts[5] 涨跌额, parts[6] 涨跌幅
        try:
            price = float(parts[3] or 0)
            prev_close = float(parts[4] or 0)
            chg = float(parts[5] or 0)
            chg_pct = float(parts[6] or 0)
        except (ValueError, IndexError):
            return None
        if prev_close == 0 or abs(price) < 0.001:
            return None
        return {
            "price":     price,
            "prev":      prev_close,
            "change":    chg,
            "change_pct": chg_pct,
            "source":    "tencent",
        }
    except Exception as e:
        log.debug(f"tencent {code} fail: {e}")
        return None


def _fetch_sina(code: str, market: str) -> dict | None:
    """新浪 hq.sinajs
    个股(gb_xxx): name, price, change_pct, datetime, change_amt, open, prev_close, ...
    指数(int_xxx): name, price, change_amt, change_pct
    """
    if market == "us":
        if code.startswith("int_"):
            url = f"https://hq.sinajs.cn/list={code}"   # 指数已含前缀
        else:
            url = f"https://hq.sinajs.cn/list=gb_{code.lower()}"
    elif market == "kr":
        return None
    else:
        return None
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Referer":    "https://finance.sina.com.cn/",
        })
        with urlopen(req, timeout=4) as r:
            text = r.read().decode("gbk", errors="ignore").strip()
        if '="' not in text or '=""' in text:
            return None
        body = text.split('="', 1)[1].rstrip('";').strip()
        if not body or body == "null":
            return None
        parts = body.split(",")
        if len(parts) < 4:
            return None
        try:
            if code.startswith("int_"):
                # 指数: name, price, change_amt, change_pct
                price = float(parts[1] or 0)
                chg   = float(parts[2] or 0)
                pct   = float(parts[3] or 0)
                prev  = price - chg
            else:
                # 个股 gb_xxx: name, price, change_pct, datetime, change_amt, open, prev_close, ...
                price = float(parts[1] or 0)
                pct   = float(parts[2] or 0)
                chg   = float(parts[4] or 0)
                prev  = float(parts[6] or 0)
            if price <= 0 or prev <= 0:
                return None
            return {"price": price, "prev": prev, "change": chg,
                    "change_pct": pct, "source": "sina"}
        except (ValueError, IndexError):
            return None
    except Exception as e:
        log.debug(f"sina {code} fail: {e}")
        return None


def _fetch_eastmoney(code: str, market: str) -> dict | None:
    """东财 push2 美股 (1=us)"""
    if market == "us":
        secid = f"105.{code}"
    elif market == "kr":
        secid = f"106.{code}"
    else:
        return None
    url = (f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}"
           f"&fields=f43,f44,f45,f46,f60,f169,f170")
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
        with urlopen(req, timeout=8) as r:
            j = json.loads(r.read().decode())
        d = j.get("data") or {}
        # f43=现价(÷100), f44=最高, f45=最低, f46=今开, f60=昨收, f169=涨跌额, f170=涨跌幅(%)
        if not d or d.get("f43") is None:
            return None
        try:
            price     = (d.get("f43") or 0) / 100.0
            prev      = (d.get("f60") or 0) / 100.0
            chg       = (d.get("f169") or 0) / 100.0
            chg_pct   = (d.get("f170") or 0) / 100.0
        except Exception:
            return None
        if prev == 0:
            return None
        return {"price": price, "prev": prev, "change": chg,
                "change_pct": round(chg_pct, 2), "source": "eastmoney"}
    except Exception as e:
        log.debug(f"eastmoney {code} fail: {e}")
        return None


def _fetch_yahoo(code: str, market: str) -> dict | None:
    """Yahoo Finance chart API (KR 主源,US 兜底)
    URL: https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d
    响应: meta.{regularMarketPrice, chartPreviousClose, ...}
    """
    # KR 指数 (KOSPI=^KS11, KOSDAQ=^KQ11) / KR 个股 (005930.KS / 000660.KS)
    # US 个股 (NVDA / AAPL) / US 指数 (^DJI / ^IXIC)
    sym_map = {
        # US 指数
        "us.DJI":  "^DJI",   "DJI":  "^DJI",
        "us.IXIC": "^IXIC",  "IXIC": "^IXIC",  "int_nasdaq": "^IXIC",
        "us.INX":  "^INX",   "INX":  "^INX",   "int_sp500":  "^GSPC",
        # KR 指数
        "KS11":    "^KS11",  "KQ11": "^KQ11",
    }
    if market == "kr":
        # 5 位 code 转 yahoo symbol (005930.Samsung → 005930.KS); 指数(KS11/KQ11)走 sym_map
        if code in sym_map:
            sym = sym_map[code]
        elif code.isdigit() and len(code) == 6:
            sym = f"{code}.KS"
        else:
            sym = code
    else:
        sym = sym_map.get(code, code)
        if not sym.startswith("^") and "." not in sym:
            sym = sym  # US ticker as-is
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
    try:
        # 用 Googlebot UA — 沙箱里默认 "Mozilla/5.0" 经常被 Yahoo 429/Rate-Limited,
        # 而 Yahoo 对 Googlebot 身份无限流 (实测 KS11/KQ11 都拿得到)
        # timeout 必须 ≥ 10s: Yahoo SSL 握手在沙箱里有时 5s 内不完
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Accept":     "application/json, text/plain, */*",
        })
        with urlopen(req, timeout=12) as r:
            j = json.loads(r.read().decode())
        meta = (j.get("chart") or {}).get("result", [{}])[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        prev  = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None or prev is None or prev <= 0:
            return None
        chg = price - prev
        pct = chg / prev * 100.0
        return {"price": float(price), "prev": float(prev),
                "change": round(chg, 4), "change_pct": round(pct, 2),
                "source": "yahoo"}
    except Exception as e:
        log.debug(f"yahoo {sym} fail: {e}")
        return None


def _fetch_one(code: str, market: str) -> dict | None:
    """按优先级多源兜底抓一只股票/指数
    US: sina 主 (gb_xxx/int_xxx) → yahoo 兜底
    KR: yahoo 主 (^KS11) → eastmoney 兜底
    """
    if market == "us":
        for fetcher in (_fetch_sina, _fetch_yahoo, _fetch_eastmoney):
            d = fetcher(code, market)
            if d:
                return d
    elif market == "kr":
        for fetcher in (_fetch_yahoo, _fetch_eastmoney):
            d = fetcher(code, market)
            if d:
                return d
    return None


# ═══════════════════════════════════════════════════
# 主入口: fetch_global_sentiment
# ═══════════════════════════════════════════════════
def fetch_global_sentiment(top_n_leaders: int = 8) -> dict:
    """
    并发抓所有美/韩指数 + 七巨头 + 重点韩股。
    返回:
      {
        "indices": [{"code","name","price","change_pct"}, ...],
        "us_leaders": [{"code","name","price","change_pct","sectors","direction","note"}, ...],
        "kr_leaders": [{"code","name","price","change_pct","sectors","note"}, ...],
        "sector_impact": {sector_name: {"change_pct": 加权得分, "drivers": [tickers]}},
        "sentiment": "risk_on" | "risk_off" | "neutral",
        "sentiment_score": -100 ~ +100 整数,
        "ts": epoch,
      }
    单只失败 → 该只跳过;整体静默失败 → 仍返回结构(空数组, sentiment=neutral)
    """
    t0 = time.time()

    # 并发抓所有需要的 code
    tasks = []
    for idx in GLOBAL_INDICES:
        tasks.append(("idx", idx["code"], idx["name"], idx["market"], idx["weight"]))
    us_codes = list(US_TO_A_STOCK_SECTOR.keys())
    for code in us_codes:
        tasks.append(("us_stock", code, code, "us", 0))

    kr_codes = list(KR_TO_A_STOCK_SECTOR.keys())
    for code in kr_codes:
        info = KR_TO_A_STOCK_SECTOR[code]
        tasks.append(("kr_stock", code, info["name"], "kr", 0))

    futures = {}
    for kind, code, _name, market, _w in tasks:
        fut = _EXEC.submit(_fetch_one, code, market)
        futures[fut] = (kind, code, _name, market, _w)

    indices_out: list[dict] = []
    us_out:      list[dict] = []
    kr_out:      list[dict] = []

    for fut, meta in futures.items():
        kind, code, name, market, weight = meta
        try:
            d = fut.result(timeout=4.5)
        except Exception:
            d = None
        if not d:
            continue
        item = {"code": code, "name": name, "price": d["price"],
                "prev": d["prev"], "change_pct": d["change_pct"],
                "source": d.get("source", "")}
        if kind == "idx":
            item["weight"] = weight
            indices_out.append(item)
        elif kind == "us_stock":
            mapping = US_TO_A_STOCK_SECTOR.get(code) or {}
            item.update({
                "sectors":   mapping.get("sectors", []),
                "direction": mapping.get("direction", "+"),
                "note":      mapping.get("note", ""),
            })
            us_out.append(item)
        elif kind == "kr_stock":
            mapping = KR_TO_A_STOCK_SECTOR.get(code) or {}
            item.update({
                "sectors": mapping.get("sectors", []),
                "note":    mapping.get("note", ""),
            })
            kr_out.append(item)

    # ── 派生: sector impact 加权得分 ──
    sector_scores: dict[str, dict] = {}
    for lst in (us_out, kr_out):
        for item in lst:
            pct = item.get("change_pct", 0) or 0
            secs = item.get("sectors") or []
            note = item.get("note", "")
            code = item.get("code", "")
            for sec in secs:
                if sec not in sector_scores:
                    sector_scores[sec] = {"sum": 0.0, "n": 0, "drivers": []}
                e = sector_scores[sec]
                e["sum"] += pct
                e["n"] += 1
                if len(e["drivers"]) < 4:
                    e["drivers"].append({"code": code, "pct": pct, "note": note})
    sector_impact = {}
    for sec, e in sector_scores.items():
        if e["n"] == 0:
            continue
        # 加权平均,用 n 平方根降权(少数票不主导)
        avg = round(e["sum"] / max(e["n"], 1), 2)
        sector_impact[sec] = {
            "change_pct": avg,
            "n_drivers":  e["n"],
            "drivers":    e["drivers"],
        }

    # ── 派生: 整体 sentiment ──
    # 规则: 纳斯达克权重最高(科技敏感),道指标普次之;若科技权重 +2% 以上 risk_on,-2% 以下 risk_off
    idx_score = sum(item["change_pct"] * item.get("weight", 1.0)
                    for item in indices_out)
    weights = sum(item.get("weight", 1.0) for item in indices_out) or 1
    idx_avg = idx_score / weights

    if   idx_avg >= 1.0:  sentiment = "risk_on"
    elif idx_avg <= -1.0: sentiment = "risk_off"
    else:                 sentiment = "neutral"

    return {
        "ts":             time.time(),
        "elapsed_sec":    round(time.time() - t0, 2),
        "indices":        sorted(indices_out, key=lambda x: x["code"]),
        "us_leaders":     sorted(us_out, key=lambda x: -(x["change_pct"]))[:top_n_leaders],
        "us_losers":      sorted(us_out, key=lambda x:  (x["change_pct"]))[:top_n_leaders],
        "kr_leaders":     sorted(kr_out, key=lambda x: -(x["change_pct"]))[:top_n_leaders],
        "sector_impact":  dict(sorted(sector_impact.items(),
                                     key=lambda kv: -abs(kv[1]["change_pct"]))),
        "sentiment":      sentiment,
        "sentiment_score": round(idx_avg, 2),
    }


# ═══════════════════════════════════════════════════
# 给 AI prompt 用的「全局情绪」摘要
# ═══════════════════════════════════════════════════
def render_for_prompt(payload: dict, max_chars: int = 1500) -> str:
    """
    把 fetch_global_sentiment 结果压缩成给 AI 看的文字(<=1500 字符)。
    用在 per-stock / aggregate prompt 的 system 段。
    """
    if not payload:
        return "(global sentiment 暂不可用)"

    parts = []
    parts.append(f"【全球整体情绪】{payload.get('sentiment','neutral').upper()} "
                 f"得分 {payload.get('sentiment_score',0):+.2f}% "
                 f"(权重:纳指×1.5 / 标普×1 / 道指×1 / KOSPI×1 / KOSDAQ×0.6)")
    parts.append("")
    parts.append("【主要指数】")
    for it in payload.get("indices", []):
        parts.append(f"  {it['name']:<14} {it['change_pct']:+6.2f}%  "
                     f"[{it.get('source','?')}]")
    parts.append("")
    parts.append("【美股龙头 (驱动哪些 A股板块)】")
    movers = (payload.get("us_losers", [])[:3] + payload.get("us_leaders", [])[:5])
    seen = set()
    for it in movers:
        if it["code"] in seen:
            continue
        seen.add(it["code"])
        secs = ", ".join(it.get("sectors", [])[:2])
        parts.append(f"  {it['code']:<6} {it['change_pct']:+6.2f}%  "
                     f"→ {secs}")
    parts.append("")
    parts.append("【韩股 (驱动 A股 半导体/锂电池 链)】")
    for it in payload.get("kr_leaders", [])[:5]:
        secs = ", ".join(it.get("sectors", [])[:2])
        parts.append(f"  {it.get('name','?'):<10} {it['change_pct']:+6.2f}%  "
                     f"→ {secs}")
    parts.append("")
    parts.append("【板块联动影响 (按权重排序)】")
    si = payload.get("sector_impact", {})
    for sec, e in list(si.items())[:12]:
        parts.append(f"  {sec:<14} 加权 {e['change_pct']:+5.2f}%  "
                     f"({e['n_drivers']}驱动)")

    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(截断)"
    return text


# ── 快速自测 ─────────────────────────────────
if __name__ == "__main__":
    import sys, json
    out = fetch_global_sentiment()
    print(json.dumps({
        "sentiment": out["sentiment"],
        "sentiment_score": out["sentiment_score"],
        "indices_count": len(out["indices"]),
        "us_count":      len(out["us_leaders"]) + len(out["us_losers"]),
        "kr_count":      len(out["kr_leaders"]),
        "sector_count":  len(out["sector_impact"]),
        "elapsed_sec":   out["elapsed_sec"],
    }, ensure_ascii=False, indent=2))
    if "--full" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=2)[:4000])
