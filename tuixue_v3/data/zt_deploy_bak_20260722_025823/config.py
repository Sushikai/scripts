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

# ═══════════════════════════════════════════════════
# iTick 免费 WS+REST (2026-07-16 新增)
# 注册地址: https://itick.org → 注册后控制台拿 token
# 免费层: REST 60次/分钟,WS 同时订阅 50 标的 tick 推送
# 不配 token 时模块整体禁用, lib_common._REALTIME_SOURCES 自动跳过
# ═══════════════════════════════════════════════════
ITICK_TOKEN = _os.environ.get("TUIXUE_ITICK_TOKEN", "")
ITICK_REST_BASE = "https://api.itick.org/sws/v1/quote"
ITICK_WS_URL = "wss://api.itick.org/sws/v1/quote"
ITICK_ENABLED = bool(ITICK_TOKEN)
# WS 标的固定为持仓 + 自选股(最多 50 个,免费层上限),由 _realtime_poller 启动时注入
ITICK_REST_TIMEOUT = 5
ITICK_WS_RECONNECT_DELAY = 3  # WS 断线 3s 重连
ITICK_TICK_TTL = 10           # tick 缓存 10s(WS 推送频繁,过期即视为停推)

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

# ═══════════════════════════════════════════
# 板块资金轮动 (2026-07-12 新增)
# ═══════════════════════════════════════════
ROTATION_WINDOW_DAYS = 5               # N 日轮动窗口(主连日线)
ROTATION_INTRADAY_PERIODS = (1, 5)     # 分时周期(min)
ROTATION_DAILY_PERIODS = (1, 3, 5)     # 日线周期
ROTATION_FUND_FETCH_TIMEOUT = 6        # akshare 单次拉取硬超时(秒)
ROTATION_LHB_FETCH_TIMEOUT = 8
ROTATION_CACHE_TTL = 60                # 板块轮动内存缓存 60s

# 资金动量 / 轮动阈值
ROTATION_MOMENTUM_MIN_YI = 0.5         # 板块日净流入 ≥ 0.5 亿才计入
ROTATION_OUTFLOW_MIN_YI = 1.0          # 旧热点资金流出 ≥ 1 亿才视为"退潮"
ROTATION_INFLOW_MIN_YI = 1.0           # 新板块资金流入 ≥ 1 亿才视为"承接"
ROTATION_STRENGTH_WEAK = 30.0          # 强度分
ROTATION_STRENGTH_MID = 60.0
ROTATION_STRENGTH_STRONG = 80.0
ROTATION_PULSE_MAX_DAYS = 1            # 脉冲=持续期 ≤ 1 天
ROTATION_PERSISTENCE_DAYS = 3          # 主线持续性 ≥ 3 天

# 2026-07-14: HOTSPOT_* 配置已删 (随 /api/hotspot + web/rotation.py 一起删除)

# 资金类型 → 配色(供前端用,这里只导出常量)
ROTATION_TYPE_COLORS = {
    "institution":   "#165DFF",
    "northbound":    "#F5319D",
    "quant":         "#868686",
    "hot_tier1":     "#F53F3F",
    "hot_tier2":     "#FF7D00",
    "retail_lhasa":  "#C9CDD4",
}

# 轮动类型 → 配色
ROTATION_LINE_COLORS = {
    "main_switch":   "#F53F3F",   # 主线切换(红粗)
    "themed_in":     "#F0C075",   # 题材内(黄细)
    "pulse":         "#8A8A8A",   # 脉冲(灰)
}
