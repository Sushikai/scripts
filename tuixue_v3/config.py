"""
tuixue_v3/config.py
所有阈值常量集中处 —— 调优只动这一个文件。
"""
from __future__ import annotations
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CACHE_DIR = PACKAGE_DIR / "cache"
LOG_DIR = PACKAGE_DIR / "logs"
REPORT_DIR = PACKAGE_DIR / "reports"
LOG_FILE = LOG_DIR / "tuixue_v3.log"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════
# Redis 统一存储 (2026-07-11)
# ═══════════════════════════════════════════════════
import os as _os
REDIS_URL = _os.environ.get("TUIXUE_REDIS_URL", "redis://127.0.0.1:6379/0")
USE_REDIS = _os.environ.get("TUIXUE_USE_REDIS", "1") == "1"

# ═══════════════════════════════════════════
# 数据源（三级热备）
# ═══════════════════════════════════════════
DATA_TIMEOUT_SEC = 5
DATA_RETRY = 3
CACHE_TTL_DAILY = 60 * 60 * 4   # 日线缓存 4h
CACHE_TTL_INTRADAY = 60 * 30    # 分时缓存 30min
CACHE_TTL_FUNDAMENTAL = 60 * 60 * 24  # 基本面 24h

# ═══════════════════════════════════════════
# Layer 1：基础风险初筛
# ═══════════════════════════════════════════
L1_MIN_TURNOVER_YI = 0.8        # 单日成交额 ≥ 8000万 = 0.8 亿
L1_FLOAT_MV_MIN_YI = 50
L1_FLOAT_MV_MAX_YI = 300
L1_LIST_DAYS_MIN = 30           # 上市 ≥ 30 个交易日
L1_MA5_VOL_RATIO = 0.8          # 近 5 日均量 ≥ 20 日均量 × 0.8（放宽）          # 近 5 日均量 ≥ 20 日均量 * 1.0
L1_EXCLUDE_BOARDS = {"创业板", "科创板", "北交所"}  # 排除创业板/科创板/北交所
L1_EXCLUDE_BOARD_PREFIXES = {
    # 创业板 (ChiNext) — 300xxx / 301xxx
    "创业板": ("300", "301"),
    # 科创板 (STAR) — 688xxx / 689xxx
    "科创板": ("688", "689"),
    # 北交所 (BSE) — 8xxxxx / 4xxxxx / 83xxxx / 87xxxx / 92xxxx
    "北交所": ("8", "4", "43", "83", "87", "92"),
}

# ═══════════════════════════════════════════
# Layer 2：情绪周期 + 主线 + 盈亏比
# ═══════════════════════════════════════════
L2_PHASE_ALLOW = {"启动", "确认"}   # 退学体系允许选股的两个阶段
L2_ZT_COUNT_ICE_MAX = 35
L2_ZT_COUNT_LAUNCH_MIN = 50
L2_ZT_COUNT_LAUNCH_MAX = 80
L2_CB_RATE_ICE_MAX = 0.40      # 连板晋级率 < 40%（冰点信号）
L2_MAINLINE_RISE_MIN = 40       # 主线板块上涨家数 ≥ 40
L2_MAINLINE_FUND_FLOW_TOPN = 3  # 资金净流入全市场前 3
L2_MAINLINE_RATIO_MIN = 2.0     # 涨跌比例 ≥ 2:1
L2_RR_RATIO_MIN = 2.5           # 盈亏比 ≥ 2.5:1

# ═══════════════════════════════════════════
# Layer 3：日线形态
# ═══════════════════════════════════════════
L3_MA_FAST = 5
L3_MA_MID = 10
L3_MA_SLOW = 20
L3_MA_LONG = 60
L3_GAIN_20D_MAX_PCT = 80.0      # 20 日累计涨幅 < 80%（退学 35% 在熊市几乎无标的）
L3_TURN_OVER_MIN_PCT = 3.0      # 换手 ≥ 3%（放宽）
L3_TURN_OVER_MAX_PCT = 20.0     # 换手 ≤ 20%（放宽）
L3_PULLBACK_VOL_MAX_RATIO = 0.70  # 回调量 ≤ 拉升均量 70%（放宽）
L3_REQUIRE_MA60 = False         # MA60 是否参与多头排列（False=更宽松）
L3_REQUIRE_BREAKOUT = False     # 是否要求底部箱体突破
L3_BREAKOUT_LOOKBACK = 30       # 突破回看 30 日

# ═══════════════════════════════════════════
# Layer 4：分时承接
# ═══════════════════════════════════════════
L4_INTRADAY_WINDOW = ("09:30", "10:30")
L4_ABOVE_AVG_RATIO = 0.70       # ≥ 70% 时间在均价线上方
L4_LATE_PUMP_CUTOFF = "14:40"   # 14:40 后偷袭剔除
L4_ZT_MIN_SEALED_RATIO = 0.01   # 封单金额 / 流通市值 ≥ 1%
L4_ZT_MIN_TURNOVER_PCT = 5.0    # 打板标的当日换手 ≥ 5%（剔除一字无量）
L4_ZT_EARLY_CUTOFF = "10:30"    # 9:30-10:30 完成换手封板
L4_MAX_BROKEN_LIMIT = 1         # 反复炸板次数上限（>1 即烂板）

# ═══════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════
OUTPUT_MAX = 10

# ═══════════════════════════════════════════
# 推荐池 Prefilter (2026-07 新增)
# 候选池 = 近 N 天涨停多次 ∩ 今日热门板块
# ═══════════════════════════════════════════
RECENT_ZT_DAYS = 3                # 拉近几个交易日的涨停池
RECENT_ZT_MIN_COUNT = 1           # 至少涨停过几次才算「涨停多多」(1=只要近 3 天有过涨停就算)
HOT_SECTOR_TOP_FLOW = 15          # 主力净流入 Top N
HOT_SECTOR_TOP_PCT = 10           # 涨幅 Top N (与流入 union)

# ═══════════════════════════════════════════
# 回测
# ═══════════════════════════════════════════
BACKTEST_HOLD_DAYS = 5
BACKTEST_TOP_N = 3              # 每天买 top N
BACKTEST_INITIAL_CAPITAL = 1_000_000.0
BACKTEST_SLIPPAGE_PCT = 0.002   # 滑点 0.2%
BACKTEST_FEE_PCT = 0.0003       # 佣金万三
BACKTEST_STAMP_TAX_PCT = 0.001  # 印花税千一（卖出）

# ═══════════════════════════════════════════
# 调优（10 次迭代）
# ═══════════════════════════════════════════
OPT_ITERATIONS = 10
OPT_START = "2025-12-01"   # 7 个月窗口
OPT_END = "2026-06-30"
OPT_SAMPLE = 50             # 调优时股票池采样，加快迭代