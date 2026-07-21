# 退学 v3 数据源重构 — 前后端测试方案

## 目标：零容忍"接口挂"——每个数据源都必须产生有效数据或明确的降级信号

---

## 一、后端测试方案

### 1.1 数据源健康测试 (`tests/test_data_sources.py`)

| 测试 | 验证点 | 通过标准 |
|------|--------|---------|
| `test_source_circuit_breaker` | 连续 5 次失败后进入冷却 | disabled_until > time.time() |
| `test_source_graduated_cooldown` | 逐级冷却 300→600→1200→2400→3600s | cooldown_level 递增 |
| `test_source_recovery` | 连续 3 次成功后恢复 | disabled_until == 0 |
| `test_tencent_qq_realtime` | 腾讯实时行情 | 返回非空 dict, 含"最新价"且 > 0 |
| `test_tencent_ifzq_realtime` | 腾讯备选实时行情 | 同上 |
| `test_parallel_race` | 多源并行竞速 | 3 个源并发，最快源返回有效数据 |
| `test_stale_while_revalidate` | 陈旧数据 + 后台刷新 | 先返缓存，后台异步刷新 |

### 1.2 API 端点契约测试 (`tests/test_api_contracts.py`)

每个端点逐条验证：

| 测试前缀 | 验证模式 |
|----------|---------|
| `test_*_ok` | 正常返回时，envelope.ok == true, data 含所有预期字段 |
| `test_*_degraded` | 所有源失败时，envelope.ok == true, data._degraded == true, 含降级数据 |
| `test_*_stale` | 缓存未过期但数据源失败时，返回带 _stale_ts 的陈旧数据 |

**端点清单（共 12 个关键端点）：**

| 端点 | 降级行为 | 陈旧数据来源 |
|------|---------|-------------|
| `/api/market/overview` | indices 全零 + `_degraded=probe_failed` | 上次成功值 |
| `/api/dashboard/signal` | a_share.verdict="unknown" + `_degraded=signal_unavailable` | 30 分钟缓存 |
| `/api/dashboard/hot_sectors` | mainline=[] + sentiment={'label':'--'} + `_degraded=sectors_unavailable` | 5 分钟缓存 |
| `/api/stock/{code}/core` | quote 全零 + kline=[] + `_degraded=quote_unavailable` | 10 分钟陈旧缓存 |
| `/api/stock/{code}/full` | 部分字段 null + `_degraded_fields=[...]` | 30 分钟陈旧缓存 |
| `/api/stock/{code}/kline` | kline=[] + `_degraded=kline_unavailable` | 60 分钟陈旧缓存 |
| `/api/stock/{code}/fund_flow` | today=null + `_degraded=fund_unavailable` | 30 分钟陈旧缓存 |
| `/api/stock/{code}/seats` | rows=[] + `_degraded=seats_unavailable` | 24 小时陈旧缓存 |
| `/api/stock/{code}/intraday` | lines=[] + `_degraded=intraday_unavailable` | 60 分钟陈旧缓存 |
| `/api/sectors/realtime` | sectors=[] + `_degraded=sectors_unavailable` | 5 分钟陈旧缓存 |
| `/api/dragons` | dragons=[] + `_degraded=dragons_unavailable` | 30 分钟陈旧缓存 |
| `/api/global/sentiment` | sentiment=neutral + `_degraded=sentiment_unavailable` | 60 分钟陈旧缓存 |

### 1.3 数据质量验证

```python
# 每个端点返回时必须验证：
VALIDATION_RULES = {
    "market/overview": {
        "indices[].price": lambda v: isinstance(v, (int, float)),
        "indices[].change_pct": lambda v: isinstance(v, (int, float)),
        "limit_up": lambda v: isinstance(v, int) and v >= 0,
    },
    "stock/{code}/core": {
        "quote.最新价": lambda v: isinstance(v, (int, float)),
        "quote.涨跌幅": lambda v: isinstance(v, (int, float)) or v is None,
    },
}
```

### 1.4 场景测试

| 场景 | 测试步骤 | 期望 |
|------|---------|------|
| 腾讯源挂 | mock qt.gtimg.cn 返回 500 | 自动切到东财 push2 → akShare → 返回有效数据 |
| 所有实时源挂 | mock 所有 9 个源返回空 | 返回 30s 内陈旧缓存 + `_degraded=true` |
| 无缓存也无源 | 全新启动 + 所有源挂 | 返回 `envelope(ok=true, data={...}, _degraded="all_sources_down")` |
| Redis 宕机 | 停 Redis + 请求 | 自动走 SQLite fallback，不中断服务 |
| 网络延迟 10s | 所有 API 响应延迟 10s | 端点 5s 内返回降级数据（不强等） |

---

## 二、前端测试方案

### 2.1 降级模式渲染测试 (`tests/frontend/views/*.test.js`)

#### 2.1.1 核心 KPI 组件

```javascript
// Test template for each KPI card:
// 1. Data source returns full data → card shows value
// 2. Data source returns {_degraded: true} → card shows "—" with stale indicator
// 3. Data source returns {_degraded: "stale"} → card shows last value + "N秒前"
// 4. Data source network error → toast + retry button
```

**需要测试的 KPI 组件：**

| 视图 | KPI 组件 | 降级显示 |
|------|---------|---------|
| topbar | 大盘指数 | 0.00% → "— · 数据暂断" |
| 个股 hero | 最新价/涨跌幅/换手率 | "—" + 底色变灰 |
| 个股 hero | 所属板块 | 板块 chip 变灰 + "暂无" |
| dashboard | 情绪信号 (allow/cautious/block) | 保持上次值 + 灰框 + "N秒前更新" |
| dashboard | 热门板块 | 保持上次 + 灰框 |
| dashboard | 涨停数 | "—" + 灰框 |
| dashboard | 全球市场 | 保持上次 + 灰框 |
| 全A风向 | 表格数据 | 保持上次 + 表头灰底 + "N秒前" |

#### 2.1.2 误差率横幅

- error_rate_banner 在 >50% 错误时显示
- 展示具体失败的端点
- 展示建议操作（刷新/检查网络）

### 2.2 交互测试

| 测试 | 操作 | 期望 |
|------|------|------|
| 点击重试按钮 | tap retry on degraded card | 重新发送 API 请求 |
| 下拉刷新 | pull-to-refresh on degraded view | 重新加载该视图 |
| 自动重试 | degraded card 5s 后自动重试 | 重试间隔 ≥5s，不刷屏 |
| 跨页导航 | degraded → 切换 view → 回来 | 保持降级状态，不重新触发所有请求 |

### 2.3 视觉回归（Playwright 48 截图）

在 `audit_views.py` 基础上增加降级模式的 48 截图：

```
screenshots/degraded/
  dash-iphone-degraded.png
  dash-pixel-degraded.png
  stock-iphone-degraded.png
  stock-pixel-degraded.png
  ...
```

---

## 三、端到端验收标准

### 3.1 必须通过（硬性）

1. 每个 API 端点返回 `envelope` 结构完整，不会 crash
2. 每个数据源失败时，端点返回 `_degraded` 标志
3. 前端每个数据卡片能正常渲染降级状态
4. 陈旧数据机制：缓存 TTL ×2 范围内的数据可复用
5. 并行竞速：Top 3 源同时请求，最快有效源胜出

### 3.2 期望通过（软性）

6. 数据源冷却从 300s 固定 → 300/600/1200/2400/3600s 逐级递增
7. 所有前端 fetch 调用有 catch 或全局兜底
8. 降级模式 UI 美观，不突兀

### 3.3 循环优化条件

任一条件不满足则重新进入优化循环：
- 48 张截图中有 >3 张显示异常
- 任何端点 `_degraded` 缺失
- 前端存在无 catch 的 fetch 调用
- 控制台显示未捕获的错误
