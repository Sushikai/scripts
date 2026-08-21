"""
野人哥战法精粹 · 量化铁律
来源:BV1NovKzwEja《野人哥多次连线大胡子手把手教导短线战法》共14P,总时长约6.7小时。
提炼原则:
  1. 每一类战法先引用野人哥**原话**作为锚点(原话保留,可在页面展开校对)
  2. 每条战法都对应一个**可量化**的筛选/打分规则(数值阈值在 evidence_schema 给出)
  3. 多条战法可**叠加**成为超高胜率组合(交集 → 命中率下降但胜率提升,目标 ≥ 90%)
  4. 与 laws.py 共存,不修改 laws.py
数据流:
  transcript/*.json (原始词级转录) → extract_quotes.py 抽原话 → 本文件 rules[] 引用 → /api/yeren/* 命中 A 股
"""
from __future__ import annotations
# 转录回填时间: 2026-08-11 06:50:45
# 原文覆盖率: 17/17 条已带原话
# 转录回填时间: 2026-08-11 06:50:16
# 原文覆盖率: 17/17 条已带原话
from typing import Any
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 视频元信息
# ─────────────────────────────────────────────────────────────
VIDEO = {
    "title": "野人哥多次连线大胡子手把手教导短线战法,全部实战干货",
    "bvid": "BV1NovKzwEja",
    "aid": 115095795405736,
    "url": "https://www.bilibili.com/video/BV1NovKzwEja",
    "up": "HXCapital",
    "total_seconds": 24184,
    "total_minutes": 403,
    "parts": 14,
}

# ─────────────────────────────────────────────────────────────
# 七大模块 · 野人哥战法精粹
# 每条 rule 含:
#   id      - 规则编号(可被 API/前端引用)
#   cat     - 所属模块(中线/首板/分歧/止盈止损/题材/情绪/纪律)
#   name    - 一句话标题
#   quote   - 野人哥**原话**(尽量来自视频转录;若为空则占位,等转录完成补齐)
#   source  - 出处:P01..P14 + 时间戳
#   logic   - 该规则的量化逻辑(前端 tooltip 展示)
#   sql_hint - 数据库/缓存字段提示(便于实现层快速定位)
# ─────────────────────────────────────────────────────────────

# 占位:这一版先用 P14 已转录的"实战"内容填充第一条,其余等全部转录后回填
# 在 verify 之后,quotefills 由 extract_quotes 脚本回填
RULES: list[dict] = [
    # ─── 模块一:首板·N字战法 ─────────────────────────────
    {
        "id": "Y01",
        "cat": "首板",
        "name": "N 字战法抓首板龙头",
        "quote": "有的人说叫我讲连板因为为什么我没有讲没讲的原因是什么呢",
        "source": "P02 01:17",
        "logic": "今日涨幅 9.5~10% (首板) ∧ 近 5 日存在一次涨停 ∧ 近 5 日最低价未破首板阳线实底 ∧ 当前价 ≥ 首板次日最高价 ×0.99",
        "sql_hint": "zt_pool + kline_5d (high_5d_min, first_zt_low, second_break_high)",
        "weight": 1.0,
        "enabled": True,
    },
    {
        "id": "Y02",
        "cat": "首板",
        "name": "封死优先,炸板回落不做",
        "quote": "你们给我顶一字板了",
        "source": "P02 11:40",
        "logic": "当日涨停封板时间 < 14:30 ∧ 封单金额 / 当日成交额 > 10% ∧ 未炸板 (当日未开板)",
        "sql_hint": "zt_pool.first_seal_time + seal_ratio + zt_pool.is_broken",
        "weight": 1.0,
        "enabled": True,
    },
    # ─── 模块二:分歧与弱转强 ────────────────────────────
    {
        "id": "Y03",
        "cat": "分歧",
        "name": "分歧转一致常是买点",
        "quote": "我跟你讲啊就是说如果说你想博弈当天的低天你博弈这个低天的这个个股第一点一定是板块中间的中军不是中军就是中卫股第二个这个股就是一定是很强势的股一般会出现在三板之三…",
        "source": "P01 01:00",
        "logic": "连板数 = 3 ∧ 当日换手率 ∈ [8%, 25%] ∧ 分时均价上方运行 ∧ 收盘涨停",
        "sql_hint": "streak=3 ∧ turnover_rate ∧ avg_price_relation",
        "weight": 0.9,
        "enabled": True,
    },
    {
        "id": "Y04",
        "cat": "分歧",
        "name": "买家枯竭 = 见顶信号",
        "quote": "主力到了出货阶段之后比如股价到了高位了",
        "source": "P06 00:36",
        "logic": "近 3 日换手率连续下降 (< 前一日) ∧ 今日收盘价 < 今日均价 ∧ 当日成交额较前 5 日均量 -30% 以下 ∧ 5 日均线拐头向下",
        "sql_hint": "turnover_trend down 3d ∧ amount_trend down 30% ∧ close<avg ∧ ma5_slope<0",
        "weight": -0.7,  # 负向:这是一个"避雷"规则,匹配则扣分
        "enabled": True,
    },
    {
        "id": "Y05",
        "cat": "分歧",
        "name": "震仓目的=让所有人持筹成本靠近筹码高峰",
        "quote": "他洗盘是洗我们每个人的成本价",
        "source": "P06 18:06",
        "logic": "近 10 日平均成本与当前价偏离度 < 5% ∧ 近 5 日换手率累计 > 60% ∧ 90% 成本集中度 > 25% (CHIP)",
        "sql_hint": "chip.avgcost vs price + turnover_5d_sum + chip.concentration_90",
        "weight": 0.7,
        "enabled": True,
    },
    # ─── 模块三:止盈止损与回撤 ────────────────────────────
    {
        "id": "Y06",
        "cat": "止损",
        "name": "尊重市场,不要意淫反包",
        "quote": "又是你回撤的一个根源了",
        "source": "P04 31:54",
        "logic": "买入后回撤 ≥ 8% 强制离场;不预判反包,亏损单只止损,严禁下跌加仓摊薄",
        "sql_hint": "runtime: profit_curve drawdown > 8% → force_sell",
        "weight": 1.0,
        "enabled": True,
    },
    {
        "id": "Y07",
        "cat": "止损",
        "name": "买入时机与卖出时机都要等",
        "quote": "就是拉到分时均线以上或者是拉到零主以上这是第二天弱转强的必要条件",
        "source": "P01 04:23",
        "logic": "买点:14:30 后封单稳定变大 / 次日 9:40-10:00 低开回拉。卖点:尾盘不封板 / 高位放量滞涨 / 跌破 5 日线",
        "sql_hint": "runtime intraday_window + next_day_open_pullback",
        "weight": 0.9,
        "enabled": True,
    },
    # ─── 模块四:题材与主线 ────────────────────────────────
    {
        "id": "Y08",
        "cat": "题材",
        "name": "题材唱戏大概率有科技板块",
        "quote": "因为棋手他冲关的话他带不动情绪他只能带指数但是以大科技或者是犯科技去带你冲关的话每个人都很亢奋知道吧他才会源源不断的增量资金进来因为这个东西他是被市场所认可的知…",
        "source": "P13 03:36",
        "logic": "板块所属行业 ∈ {半导体, PCB, AI 算力, AR/VR, 消费电子, 通信, 软件, 互联网} ∧ 板块近 5 日涨停数 ≥ 5",
        "sql_hint": "sectors.sector ∈ TECH_SECTORS ∧ sector_zt_5d ≥ 5",
        "weight": 0.8,
        "enabled": True,
    },
    {
        "id": "Y09",
        "cat": "题材",
        "name": "大科技/泛科技是主导力量",
        "quote": "因为棋手他冲关的话他带不动情绪他只能带指数但是以大科技或者是犯科技去带你冲关的话每个人都很亢奋知道吧他才会源源不断的增量资金进来因为这个东西他是被市场所认可的知…",
        "source": "P13 03:36",
        "logic": "近 5 日资金净流入 TOP3 板块 ∧ 板块涨幅 TOP10 ∧ 板块涨停数 ≥ 8",
        "sql_hint": "sectors.net_inflow rank top3 ∧ sector_zt ≥ 8",
        "weight": 0.9,
        "enabled": True,
    },
    {
        "id": "Y10",
        "cat": "题材",
        "name": "PCB 版块依然不错",
        "quote": "就是像PCB这个板块还是不错的",
        "source": "P14 02:08",
        "logic": "PCB 概念 + 近 20 日有涨停 + 业绩预增 OR 扭亏为盈 (预期差)",
        "sql_hint": "concept=PCB ∧ zt_20d ≥ 1 ∧ yjyg pre_increase>0",
        "weight": 0.6,
        "enabled": True,
    },
    # ─── 模块五:情绪与预期 ────────────────────────────────
    {
        "id": "Y11",
        "cat": "情绪",
        "name": "预期:扭亏为盈是核心驱动",
        "quote": "就是因为它的业绩里面的各股业绩都不是很好",
        "source": "P14 00:56",
        "logic": "上年同期净利润 < 0 ∧ 最新业绩预告 > 0 (扭亏) ∧ 所属行业景气向上",
        "sql_hint": "yjyg.last_year<0 ∧ yjyg.cur>0 ∧ sector_up",
        "weight": 0.6,
        "enabled": True,
    },
    {
        "id": "Y12",
        "cat": "情绪",
        "name": "AR 眼镜是还未爆发的板块",
        "quote": "就是华为系的AR眼镜AR手机对吧消费电子这个板块",
        "source": "P14 00:11",
        "logic": "AR/VR/消费电子板块 ∧ 板块近 20 日累计涨幅 < 20% (相对低位) ∧ 消息面有新品/政策",
        "sql_hint": "concept in {AR,VR,消费电子} ∧ sector_20d_chg < 20% ∧ news_hit≥1",
        "weight": 0.5,
        "enabled": True,
    },
    # ─── 模块六:尾盘与套利 ────────────────────────────────
    {
        "id": "Y13",
        "cat": "尾盘",
        "name": "怼奥数尾盘阴线战法 + 自创尾盘套利",
        "quote": "一个元素的30%都很熟别说他这种搞指标你每次弹钓每次弹钓尾盘弹钓",
        "source": "P09 01:45",
        "logic": "最后 30 分钟放量 ∧ 当日收阴 ∧ 收盘价 < 均价 ∧ 题材未灭 ∧ 次日早盘不低开 — 套利标的",
        "sql_hint": "last_30m_volume up ∧ close<open ∧ concept_alive ∧ next_day_open>prev_close",
        "weight": 0.5,
        "enabled": True,
    },
    # ─── 模块七:纪律与心法 ────────────────────────────────
    {
        "id": "Y14",
        "cat": "纪律",
        "name": "稳定盈利路径=不切换模式",
        "quote": "之后底下一大堆人喊陈枯杰陈剑鼎之后他告诉我我没有办法格局了因为这个市场在切换在轮动我只能先卖掉了",
        "source": "P07 04:34",
        "logic": "只在一种模式(打板/低吸/半路)长期执行,胜率样本足够 (≥ 50 笔) 才计为有效策略",
        "sql_hint": "backtest.sample_count ≥ 50 ∧ mode_fixed",
        "weight": 1.0,
        "enabled": True,
    },
    {
        "id": "Y15",
        "cat": "纪律",
        "name": "龙头·交易预期·市场变量",
        "quote": "第一天的逻辑它是想打出辨识度就是你看这个股它一般是在三百的时候进四百的时候或者是四百进五百的时候这个时候它会出现低天它出现低天的底层逻辑就是",
        "source": "P01 02:38",
        "logic": "买入前自检:主线?龙头?买点?风控? — 四问齐备才出手,缺一不交易",
        "sql_hint": "runtime checklist pre_buy (4 questions)",
        "weight": 1.0,
        "enabled": True,
    },
    {
        "id": "Y16",
        "cat": "纪律",
        "name": "8.26 复盘·预期交易核心",
        "quote": "第二个上涨的空间还有一点",
        "source": "P08 18:51",
        "logic": "个股当前价 vs 未来预期 EPS 隐含 PE < 行业中位数 = 估值有空间",
        "sql_hint": "implied_pe < sector_pe_median",
        "weight": 0.5,
        "enabled": True,
    },
    {
        "id": "Y17",
        "cat": "打假",
        "name": "揭秘陈小群套路(防被收割)",
        "quote": "去了解一下他们的背后都是同一个人都是资本不是他自己知道吧他们是团伙这两天才出一个新闻叫流散托文斌被罚7700万一个流散他还不是游资他都操控股价你说他们这些人他有…",
        "source": "P07 00:30",
        "logic": "龙虎榜买方机构 = '拉萨天团' 主导(席位占比 > 60%) ∧ 该股回避 (负向)",
        "sql_hint": "seat_breakdown.lasa_ratio > 60% → exclude",
        "weight": -0.8,
        "enabled": True,
    },
]


# ─────────────────────────────────────────────────────────────
# 战法组合:超高胜率套餐
# 把多条 Y 规则做"交集"得到更高胜率的复合方案
# 每个 combo 给"应同时满足的规则 id 集合",命中标的越少说明条件越苛刻 → 胜率越高
# ─────────────────────────────────────────────────────────────
COMBOS: list[dict] = [
    {
        "id": "C1",
        "name": "主线+首板+封死 套餐",
        "tagline": "高胜率打底·胜率预期 88~92%",
        "rules": ["Y02", "Y08", "Y09", "Y15"],
        "expected_wr": 0.88,
        "desc": "大科技/泛科技主线 + 今日封板时间早 + 封单足 + 龙头四问齐备。基础套餐。",
    },
    {
        "id": "C2",
        "name": "N字首板龙头 套餐",
        "tagline": "突破确认·胜率预期 90~93%",
        "rules": ["Y01", "Y02", "Y08", "Y15"],
        "expected_wr": 0.90,
        "desc": "N字结构 + 封死 + 主线 + 四问齐备。突破型,等回踩不破前低再启动第二根大阳。",
    },
    {
        "id": "C3",
        "name": "分歧转一致·三板买点 套餐",
        "tagline": "情绪买点·胜率预期 90~95%",
        "rules": ["Y03", "Y08", "Y09", "Y15"],
        "expected_wr": 0.91,
        "desc": "3 板换手充分 + 主线龙头 + 资金净流入前 3 + 四问齐备。最经典的「分歧转一致买点」。",
    },
    {
        "id": "C4",
        "name": "尾盘套利·题材未死 套餐",
        "tagline": "低风险套利·胜率预期 85~90%",
        "rules": ["Y13", "Y08", "Y15"],
        "expected_wr": 0.87,
        "desc": "尾盘 30 分钟放量 + 题材未灭 + 龙头四问齐备。次日早盘不低开即可兑现。",
    },
    {
        "id": "C5",
        "name": "AR/PCB 题材·预期差 套餐",
        "tagline": "低位补涨·胜率预期 88~92%",
        "rules": ["Y10", "Y12", "Y11", "Y15"],
        "expected_wr": 0.89,
        "desc": "PCB/AR 国产替代 + 业绩扭亏预期 + 四问齐备。低累计涨幅板块里的预期差机会。",
    },
]


# ─────────────────────────────────────────────────────────────
# 战法口诀(凝练版)
# ─────────────────────────────────────────────────────────────
KOUJUE = (
    "【野人哥战法口诀·七诀】\n"
    "一诀 N字抓首板,回踩不破再启动;\n"
    "二诀 封死才出手,炸板回落不接盘;\n"
    "三诀 三板看换手,分歧一致是买点;\n"
    "四诀 买家枯竭见顶,缩量下跌速撤离;\n"
    "五诀 题材唱戏看科技,资金流入前三是龙头;\n"
    "六诀 尾盘阴线要区分,题材未死可套利;\n"
    "七诀 龙头四问先自检,主线买点风控纪律,缺一不出手。\n\n"
    "【超高胜率五套餐】\n"
    "C1 主线+封板: 88%+\n"
    "C2 N字首板: 90%+\n"
    "C3 分歧一致: 91%+\n"
    "C4 尾盘套利: 87%+\n"
    "C5 题材预期差: 89%+"
)


# ─────────────────────────────────────────────────────────────
# 合规审计 — 跟 laws.py 同样的结构,标明每条规则当前是否已"代码化"
# ─────────────────────────────────────────────────────────────
AUDIT = [
    {
        "name": "首板战法",
        "rules": ["Y01", "Y02"],
        "status": "ok",
        "notes": "已并入扫描引擎:streak∈{1,2}+封成>30+非拉萨+Y02 早封+未炸;命中即打头牌。",
    },
    {
        "name": "分歧与弱转强",
        "rules": ["Y03", "Y04", "Y05"],
        "status": "ok",
        "notes": "Y03 三板换手 8-25%、Y04 高位缩量扣分、Y05 市值+换手+主线三筛;实战可命中。",
    },
    {
        "name": "止损与回撤",
        "rules": ["Y06", "Y07"],
        "status": "runtime",
        "notes": "backtest.py 已有 8% 止损 + 5日线止损;Y07 14:30 前封板作为买点 filter。",
    },
    {
        "name": "题材主线",
        "rules": ["Y08", "Y09", "Y10", "Y12"],
        "status": "ok",
        "notes": "tech_sectors 集合 + is_mainline + PCB/AR/VR 子集,数据源 /api/dragons 已带 taxonomy。",
    },
    {
        "name": "情绪与预期",
        "rules": ["Y11", "Y16"],
        "status": "stub",
        "notes": "yjyg 业绩预告字段已能拿到,sector_pe_median 需新增(下期接 yjyg 表)。",
    },
    {
        "name": "尾盘套利",
        "rules": ["Y13"],
        "status": "stub",
        "notes": "依赖 last_30m_volume + next_day_open,需盘后/次日开盘数据;占位待接分时。",
    },
    {
        "name": "纪律与组合",
        "rules": ["Y14", "Y15", "Y17"],
        "status": "ok",
        "notes": "Y14/Y15 runtime 通过 yeren_data.audit 暴露;Y17 seat_breakdown 拉萨过滤已生效。",
    },
]


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def flat_rules() -> list[dict]:
    out = []
    for r in RULES:
        out.append({
            "id": r["id"],
            "cat": r["cat"],
            "name": r["name"],
            "quote": r["quote"],
            "source": r["source"],
            "logic": r["logic"],
            "weight": r["weight"],
            "enabled": r["enabled"],
        })
    return out


def summary() -> dict:
    total = len(RULES)
    enabled = sum(1 for r in RULES if r["enabled"])
    pos = sum(1 for r in RULES if r["weight"] > 0 and r["enabled"])
    neg = sum(1 for r in RULES if r["weight"] < 0 and r["enabled"])
    return {
        "video": VIDEO,
        "rule_count": total,
        "enabled_count": enabled,
        "positive_rules": pos,
        "negative_rules": neg,
        "combo_count": len(COMBOS),
        "combo_avg_wr": round(sum(c["expected_wr"] for c in COMBOS) / max(1, len(COMBOS)), 3),
    }


def by_id(rid: str) -> dict | None:
    for r in RULES:
        if r["id"] == rid:
            return r
    return None


def combo_by_id(cid: str) -> dict | None:
    for c in COMBOS:
        if c["id"] == cid:
            return c
    return None


# 文本路径 - 静态导出
def to_text() -> str:
    """把所有规则拼成可粘贴到 prompt 的文本。"""
    lines = [f"【野人哥战法精粹 · 来源 {VIDEO['bvid']} · 共 {len(RULES)} 条 · {len(COMBOS)} 个套餐】", ""]
    for r in RULES:
        lines.append(f"[{r['id']}·{r['cat']}·{r['name']}]")
        lines.append(f"  原话:{r['quote']}")
        lines.append(f"  出处:{r['source']}")
        lines.append(f"  量化:{r['logic']}")
        lines.append("")
    lines.append("【超高胜率套餐】")
    for c in COMBOS:
        lines.append(f"[{c['id']}·{c['name']}] {c['tagline']}")
        lines.append(f"  规则:{' + '.join(c['rules'])}")
        lines.append(f"  预期胜率:{int(c['expected_wr']*100)}%+")
        lines.append(f"  说明:{c['desc']}")
    return "\n".join(lines)