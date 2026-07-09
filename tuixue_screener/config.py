#!/usr/bin/env python3
"""
tuixue_screener/config.py
所有可调阈值集中在头部常量（用户要求）。
"""

# ════════════════════════════════════════════════════════════
# 一、底层数据层 / 容灾
# ════════════════════════════════════════════════════════════
HTTP_TIMEOUT = 5                   # 单接口超时（秒）
HTTP_RETRIES = 3                    # 单接口重试次数
HTTP_BACKOFF_BASE = 1.5             # 指数退避基数

# 行情数据源（按优先级顺序，可热插拔）
QUOTE_SOURCES = [
    "eastmoney_push2",              # 主：东方财富 push2（最稳）
    "eastmoney_datacenter",         # 一级备：东方财富 datacenter
    "akshare",                      # 二级备：akshare
]
NEWS_SOURCES = [
    "eastmoney_ann",                # 主：东财公告
    "akshare_news",                 # 备：akshare 新闻
]

# 缓存策略
CACHE_DIR_NAME = "cache"
INTRADAY_CACHE_TTL_SEC = 1800       # 当日分时缓存 30 分钟
DAILY_CACHE_TTL_DAYS = 1            # 日线缓存 1 天
HISTORY_CACHE_TTL_DAYS = 7          # 历史缓存 7 天

# 故障流转
BLOCK_NEW_TRADES_IF_QUOTE_DOWN = True   # 行情全瘫时禁止开新仓

# ════════════════════════════════════════════════════════════
# 二、程序交互
# ════════════════════════════════════════════════════════════
TRIGGER_FUNCTION = "run_stock_screen"   # 手动触发入口
MAX_OUTPUT = 10                          # 最多输出 10 只
KEEP_EMPTY_IF_NO_PASS = True             # 无达标返回 []，绝不降阈值

# ════════════════════════════════════════════════════════════
# 三、第一层：全局基础风险初筛
# ════════════════════════════════════════════════════════════
EXCLUDE_BOARDS = ["创业板", "科创板", "北交所"]
EXCLUDE_KEYWORDS = ["ST", "*ST", "退", "停"]
LISTING_MIN_DAYS = 30              # 次新股过滤：上市未满 30 日

MIN_TURNOVER_YUAN = 8_0000_0000    # 单日成交额 ≥ 8000 万
MIN_FLOAT_MKT_CAP_YI = 50          # 流通市值 ≥ 50 亿
MAX_FLOAT_MKT_CAP_YI = 300         # 流通市值 ≤ 300 亿
MIN_TRAILING_PROFIT = True         # 近一年扣非净利润不为负（需基本面数据）

# 量能趋势：5日均量 > 20日均量
MA5_VOL_OVER_MA20_VOL = True

# 黑名单文件（永久拉黑池）
BLACKLIST_FILE = "blacklist.json"

# ════════════════════════════════════════════════════════════
# 四、第二层：情绪周期 + 主线题材
# ════════════════════════════════════════════════════════════
# 周期判定阈值
CYCLE_ALLOW = {
    "冰点修复": {
        "zt_count_max": 35,
        "ban晋级率_max": 0.40,
        "隔日溢价_min": -1.0,
    },
    "启动确认": {
        "zt_count_min": 50,
        "zt_count_max": 80,
        "mainline_up_ratio_min": 2.0,   # 主线涨跌比 ≥ 2:1
    },
}
CYCLE_FORBID = ["情绪高潮", "市场退潮"]

# 主线题材硬性标准
MAINLINE_SECTOR_UP_COUNT_MIN = 40       # 板块上涨 ≥ 40 只
MAINLINE_FUND_FLOW_TOP_N = 3            # 板块资金净流入前 3
MAINLINE_REQUIRE_CATALYST = True        # 舆情核验
MAINLINE_REQUIRE_LADDER = True          # 梯队完整

# 盈亏比前置
MIN_RR_RATIO = 2.5                      # 候选股理论盈亏比 ≥ 2.5:1

# ════════════════════════════════════════════════════════════
# 五、第三层：日线趋势形态
# ════════════════════════════════════════════════════════════
MA_BULL = "MA5>MA10>MA20>MA60"           # 均线多头
PRICE_ABOVE_MA5 = True
PHASE_GAIN_MAX = 35.0                   # 近 20 日累计涨幅 < 35%
VOL_PULLBACK_FACTOR = 0.50              # 回调量 < 拉升均量的 50%
TURNOVER_RATE_MIN = 5.0
TURNOVER_RATE_MAX = 15.0                # 18% 也在高分位强制剔除；这里取 15%
TURNOVER_RATE_HARD_MAX = 18.0
BREAKOUT_BOX = True                     # 底部箱体突破
NO_OVERHEAD_CLUSTER = True              # 上方无密集套牢

# ════════════════════════════════════════════════════════════
# 六、第四层：分时资金承接
# ════════════════════════════════════════════════════════════
INTRADAY_TIME_START = "09:30"
INTRADAY_TIME_END = "10:30"
INTRADAY_ABOVE_AVG_PCT_MIN = 70.0       # 70% 时间价格在均价线上
LATE_PUMP_CUTOFF = "14:40"              # 14:40 后尾盘拉升剔除
LIMITUP_TIME_START = "09:30"
LIMITUP_TIME_END = "10:30"              # 仅早盘 9:30-10:30 换手封板
MIN_SEAL_PCT_OF_FLOAT = 1.0            # 封单/流通市值 ≥ 1%
EXCLUDE_REPEATED_BAD_BAN = True         # 高位反复炸板剔除
ONLY_BENIGN_RESEAL = True               # 仅保留良性回封

# ════════════════════════════════════════════════════════════
# 七、排序与输出
# ════════════════════════════════════════════════════════════
RANK_PRIORITY = [
    "主线中军龙头",
    "主线换手二板龙头",
    "主线低位首板",
]

# ════════════════════════════════════════════════════════════
# 八、风控体系（仅在 backtest 中实际生效）
# ════════════════════════════════════════════════════════════
# 注：以下参数已根据 2025-07 → 2026-06 一年期 500 票回测优化（240 组配置）
SINGLE_POSITION_CAP = 0.30              # 单票 ≤ 30%（优化后 30% 收益最大化）
START_CYCLE_TOTAL_POS = 0.80            # 启动期总仓 8 成
HIGH_CYCLE_TOTAL_POS = 0.30             # 高潮期总仓 3 成
WAVE_DOWN_TOTAL_POS = 0.00              # 退潮期 0

# 优化后的止盈止损
TARGET_PCT_OPTIMAL = 0.10               # 目标止盈 +10%
STOP_PCT_OPTIMAL = 0.05                 # 硬止损 -5%
HOLD_DAYS_OPTIMAL = 7                   # 持仓 7 天
TOP_N_OPTIMAL = 10                      # 每日最多选 10 只

TRAILING_ACTIVATION_PCT = 6.0           # 浮盈 ≥ 6% 启动移动止盈
TRAILING_PULLBACK_PCT = 3.0             # 自最高点回落 ≥ 3% 全仓卖出
STOP_LOSS_MA5_BREAK_PCT = 0.50          # 收盘破 5 日线减半
STOP_LOSS_MA10_BREAK_PCT = 1.00         # 收盘破 10 日线全清
LIMITUP_STOP_PCT = 0.02                 # 打板后回撤 ≥ 2% 即止损

# 题材离场
SECTOR_DEATH_UP_COUNT = 30              # 主线上涨股 < 30 只清仓
ZAMANG_PENALTY = True                   # 杂毛反弹全平仓

# 严格规避补仓
NO_AVERAGING_DOWN = True                # 永不补仓摊薄成本

# MA 严格度（优化：MA60 太严格，关闭后效果更好）
REQUIRE_MA60 = False                    # True=MA5>MA10>MA20>MA60, False=MA5>MA10>MA20
MIN_RR_RATIO_OPTIMAL = 2.0              # 优化用 2.0（可严到 2.5）