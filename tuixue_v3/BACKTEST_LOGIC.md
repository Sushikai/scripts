# 回测 v4 买卖逻辑详解

> **入口**:`web/backtest_screener.py::run_for_frontend`
> **前端调用**:`POST /api/screener/backtest` → 拿到 `run_id` → `GET /api/screener/backtest?run_id=...` 轮询进度 → `status=done` 时 `result` 即为完整结果
> **引擎版本**:v4 (vectorized · top-tier)
> **最近更新**:2026-07-15 — 加 S2 (10:00 决策规则)

---

## 1. 整体流程一览

```
[用户配置]  period_keys × hold_days × top_n × sample
        ↓
[Step A] 拉股票列表 (主板 ~3000, 默认采样 1000)
[Step B] 拉交易日历 (~半年 = 120 交易日)
[Step C] 拉日线 panel (40 worker 并行, ~30s 完成)
[Step D] 向量化算指标 + 打 4 条硬规则 + 算 score
[Step E] 对每个交易日:
           ├─[BUY] 从当日候选里挑 top_n (按 score 降序)
           │       默认要求 pass_all=1 (4 条规则全过)
           │       买入价 = 当日收盘价 (proxy)
           ├─[SELL] 对每笔 trade 跑 10 套 T+1 退场
           │       若 T+1 open ≥ buy × 1.095 → 触发 T+2 续涨停
           └─[HOLD] 对每笔 trade 跑 7 套 N 日持退 (default 3 日)
[Step F] 9 套退场各自胜率 / 累计复利 / 年化 / 月化
[Step G] 月度胜负 / 板块归类 / 退出原因 / 资金曲线
```

---

## 2. 买入逻辑 (BUY)

### 2.1 候选池
- 全 A 股 ~5400 只 → 主板过滤 (排除创业板 300/科创板 688/北交所 8/4 字头)
- 约 3000 只主板 → 按字母序稳定取前 `sample` (默认 1000)
- 为什么不跑全量:东财/akshare 限频,全量 3000+ 只网络会拖到 30+ 分钟

### 2.2 4 条硬规则 (向量化 boolean mask)
| 规则 | 列 | 阈值 | 说明 |
|------|-----|------|------|
| 规则 3 | `change_pct` | `[3.0%, 5.0%]` | 当日涨幅 3-5% — 不追当日涨停,要的是"刚启动" |
| 规则 5 | `vol_ratio` | `≥ 1.0` | 量比 ≥ 1 (今日量/近 5 日均量) |
| 规则 6 | `amount_ratio` | `[0.6, 3.0]` | 成交额/近 60 日均额 — 替代真实换手率(akshare 这列历史空) |
| 规则 4 | `ret_20d_max` | `≥ 9.5%` | 近 20 日内至少 1 次涨停 |

软通 (不计入 fail):
- 规则 6 真身 `mcap_yi ∈ [40, 300]` — 回测无总市值列,留接口
- 规则 8 全天 VWAP 上方 — 历史分时不可得

`pass_all = 1` 当且仅当以上 4 条硬规则全过 (Z3 严格模式,前端默认勾选)。

### 2.3 综合得分 `score`
公式在 `_vectorized_screen` 末尾,逻辑:
- change_pct 居中 ([3,5] 中位 4) → 越接近中位越好
- 量比高 → 越好
- 成交活跃 (amount_ratio 中位 ≈ 1) → 越好
- 近 20 日涨停数 → 加权

每日按 score 降序排,选 **top_n** (默认 1) 作为当日 trade。

### 2.4 买入价
**`buy_price = 当日收盘价`** (proxy,真实场景应为首板次日开盘价 — 但回测只能用收盘近似)。

---

## 3. 卖出逻辑 (SELL) — 10 套 T+1 退场

每笔 trade 在 T+1 当天按 10 种规则计算退场百分比,互不干扰,横评展示。续涨停场景会延后到 T+2 重算。

### 3.1 退场规则明细

| Key | 中文 | 触发逻辑 | 公式 |
|-----|------|---------|------|
| `open` | 开盘卖 | 不分青红皂白,T+1 开盘价就跑 | `(open / buy - 1) × 100` |
| `S1` | S1 翻红卖 | 翻红→开盘;没翻红→收盘 | `open > buy ? ret_open : ret_close` |
| **`S2`** | **S2 10:00 决策** | **10:00 前必卖:9:30 翻红→开盘;否则→水下均价** | **见 3.2 详解** |
| `avg_up` | 翻红均价 | T+1 当日翻红后,在 `(open+high)/2` 卖 | `(open+high)/2 > buy ? ret_avg_up : ret_close` |
| `max95` | 最佳 × 95% | 当日 `high ≥ buy × 1.001` → `high × 0.95`;否则 close | 模拟"30 分钟能触及的最高位 × 95%" |
| `tp2` | 止盈 2% | 当日 `high ≥ buy × 1.02` → 锁 2%;否则 close | 目标 2% 截 |
| `twap` | 时间加权 | `(open + close) / 2` | 机构执行基准 |
| `half` | 半仓 | `0.5 × open + 0.5 × avg_up` | 拆单 |
| `close` | 收盘 | T+1 收盘价 | 全天观察的 reference |
| `low` | 日内最差 | T+1 日内最低价 | 风控下限 (理论最坏) |

### 3.2 S2 (10:00 决策规则) 详解 — 用户铁律

**核心原则**:10:00 前必须卖出,考虑所有情况;涨停可能是唯一例外。

```python
if open >= buy * 1.095:
    return None   # → caller 触发 T+2 续涨停路径
if open > buy:
    ret = ret_open              # 9:30 翻红 → 开盘卖
else:
    underwater = (open + low) / 2 if low < buy else open
    ret = (underwater / buy - 1) * 100  # 没翻红 → 水下均价
```

| 情况 | 卖出价 | 实战含义 |
|------|--------|----------|
| T+1 open 触及涨停 ≥9.5% | T+2 续 (沿用同一规则) | 一字板,买不到,直接拿 |
| 9:30 open > buy | open | 翻红 → 早盘直接卖,落袋为安 |
| 9:30 open ≤ buy,low < buy | `(open+low)/2` | 没翻红且日内下探 → 9:30-10:00 水下均价 (回测用日线代理) |
| 9:30 open ≤ buy,low ≥ buy | open | 没翻红但全天扛住 → 开盘价卖 |

**回测代理说明**:历史 T+1 没分时数据,水下均价只能用 `(open+low)/2` 近似 9:30-10:00 的水下均价。实盘要用真分时:`data_layer.fetch_intraday(code, sell_date)` (但 ak.stock_zh_a_hist_min_em 只返当日,历史 T+1 取不到 — 这是已知的精度上限)。

### 3.3 续涨停 (T+1 → T+2) 触发条件
`T+1 open ≥ buy × 1.095` (考虑 ST 是 5%、科创/创业是 20%,这里主板 10% 用 9.5% 留 0.5% 容差)

→ 主循环检测到 `r9 is None`,自动查 T+2 日线再算一次 `_simulate_from_cache_row`,加 `hold_extended=True` 标记。

### 3.4 7 套持退 (N 日持仓,默认 3 日)
与 T+1 退场独立,模拟"如果不止损能拿多久"。

| Key | 触发逻辑 |
|-----|----------|
| `best` | N 日内最高价 (理论上限) |
| `trail_3pct` | 触及 +3% 后,回撤 1.5% 出 |
| `trail_5pct` | 触及 +5% 后,回撤 2% 出 |
| `trail_8pct` | 触及 +8% 后,回撤 3% 出 |
| `stop_3pct` | 跌破 -3% 即止损,不主动止盈 |
| `close` | N 日后收盘平 |
| `rule_pri` | = S1 规则,N 日内反复套用 |

---

## 4. 统计口径 (用户核心要求)

### 4.1 每套退场的 12 个指标
对每套退场 (open / S1 / S2 / ...) 的所有 N 笔 % 收益算:

| 指标 | 公式 | 用途 |
|------|------|------|
| `n` | 笔数 | 样本量 |
| `win_rate_pct` | 收益 > 0 的笔数 / 总笔数 × 100 | 胜率 |
| `avg_pct` | 平均每笔 %  | 单笔期望 |
| `median_pct` | 中位数 % | 抗极端值 |
| `stddev_pct` | 标准差 | 波动 |
| `cum_return_pct` | `(1 + r/100) 累乘 - 1` × 100 | 累计复利 |
| **`annualized_pct`** | `cum ^ (250/n_days) - 1` | **年化** |
| **`monthly_pct`** | `cum ^ (21/n_days) - 1` | **月化** |
| `expectancy_pct` | `win_rate × avg_win - loss_rate × avg_loss` | 期望值 |
| `profit_factor` | `Σwin / |Σloss|` (∞ 当 loss=0) | 盈亏比 |
| `p25 / p75` | 25/75 分位 | 分布形态 |
| `best_pct / worst_pct` | 最大/最小单笔 | 极值 |

`n_days` = `max(buy_date) - min(buy_date)` 的交易日数 (从 `_period_days` 计算)。

### 4.2 `_best_strategy` 自动推荐
10 套里 `avg_pct` 最高的胜出,前端表格用 ★1 金标。

### 4.3 资金曲线 (Equity Curve)
按时间顺序累加 S1 主退场的单笔收益,作为"如果真按 S1 规则做"的净值曲线。前端用 ECharts 画。

### 4.4 月度胜负
按 buy_date 月份分组,统计该月所有 trade 的 S1 累计复利 → 12 行表 (近一年) / 6 行 (近半年) / 等。

### 4.5 板块归类
对每笔 trade 用 `sector_classify` 查该股所属板块;同板块聚合胜率 + 累计复利。
**网络超时兜底**:`ThreadPoolExecutor` + 5s deadline,超时的回退到 `_board_fallback_sector(code)` 返回主板/创业板/科创板/北交所前缀。
**为什么加这兜底**:sector_classify 调东财接口,116 只 × 网络限频能拖死整个回测 (实测能卡 99s+ 不动)。

---

## 5. 关键修复与权衡 (踩过的坑)

| 问题 | 修复 | 教训 |
|------|------|------|
| 板块归类卡 99s | ThreadPool 5s deadline + 主板兜底 | 任何聚合端点必须显式超时,不能静默等 |
| `module 'backtest_screener' has no attribute 'WINDOWS'` | 加模块级常量 | 别把 helper 常量藏在函数里 |
| KPI 卡片窄成 36px (文字竖排) | `#bt-kpis` class 从 `kpi-grid` 改 `bt-kpis` + 显式 `grid-template-columns: 1.4fr 1fr 1fr` | 顶层容器用 class hook,别继承别人的 grid |
| 年化月化全 0 | server 跑的是旧 PID,忘重启 → 拿到旧代码 | 大改后必须重启 + 验证 PID |
| SW cache 凝固旧 app.js (按钮没反应) | `CACHE` 名 +1 (v8 → v9 → v11) | SW 升级频率 = 改前端 bug 频率 |

---

## 6. 一图流 (时序)

```
T 日 (买入)
  │
  ├─ score 排序 → top 1 (默认 1000 只里) → 候选
  │
  ├─ buy_price = 当日 close
  │
T+1 日 (卖出 — 10 套并行算)
  │
  ├─ open >= buy × 1.095 ?
  │    ├─ YES → 跳过 (触发 T+2)
  │    └─ NO  → 跑 open / S1 / S2 / avg_up / max95 / tp2 / twap / half / close / low
  │
  └─ T+2 日 (仅在续涨停时)
       └─ 重算一次,如果还涨停就拿 (hold_extended=True)
```

---

## 7. 调用示例 (前端)

```js
const r = await api('/api/screener/backtest', {
  method: 'POST',
  body: JSON.stringify({
    periods: ['半年'],          // 1周 / 2周 / 1月 / 2月 / 半年 / 1年 多选
    hold_days: 3,               // 7 套持退的持仓天数
    top_n: 1,                   // 每日取几只
    sample: 1000,               // 主板采样数 (网络限流)
  }),
});
// r.data.run_id → 拿去轮询
// 轮询: GET /api/screener/backtest?run_id=<run_id>
// status=done 时 d.result 即为完整结构
```

结果示例 (2026-07-15 实跑,半年窗口,120 笔):
- `summary.cum_return_pct` = -38.89% (S1 主退,半年复利)
- `scenarios.S1.cum` = -38.89%, `ann` = -61.21%, `mon` = -7.65%
- `scenarios.S2.cum` = -88.16%, `ann` = -98.35%, `mon` = -29.15%
- `scenarios._best_strategy` = `avg_up` (+168.45% cum,+530.17% ann,+16.72% mon)
- `windows[0].win_rate_S1` = 55.0% (S1 在 120 笔的胜率)