# 0023 · 3 阶段管线重构 + 前端修缮

**日期**: 2026-07-19

---

## 概述

三大改动:
1. **回测管线 3 阶段化**: 下载 → REG → 索引, 不再交错执行
2. **前端修缮**: 控制区分组、面板自动展开、涨跌天数、三方案对比
3. **增量下载**: 后续回测只补充缺失数据

---

## 后端改动 (`backtest_screener.py`)

### 3 阶段管线

**Phase 1: 预下载** (增量)
- 现有 `_prefetch_daily` 通过 data_layer Redis/SQLite 自动增量
- 所有 progress_cb 加 `[1/3 下载]` 前缀

**Phase 2: REG 工程化**
- 新增 `_validate_data_quality()` — 检查:
  - 数据覆盖率 (命中数 / 总数)
  - 空值比例
  - 日期区间跨度
- 覆盖率 < 50% 或空值 > 30% 时 log warning

**Phase 3: 构建索引**
- 新增 `_build_data_index()` — 抽离为独立函数:
  - `panel_idx`: `{date: {code: row_dict}}`
  - `cache_by_code_date`: `{code: {date: {OHLC}}}`
  - `sec_avg_by_date`: `{date: {sector: avg_change_pct}}`

### 函数清单

```python
_validate_data_quality(daily_cache, codes, name) -> dict
_build_data_index(panel, daily_cache, codes, names) -> (panel_idx, cache_by_code_date, sec_avg_by_date)
```

---

## 前端改动 (`index.html`)

### 控制区重构
- 原来 1 行 flex-wrap → 3 行 `.bt-ctrl-row`:
  - Row 1: 周期 + 持仓 + 每日取 + 采样
  - Row 2: 硬底 + 软线 + 热门Top + 资金流入
  - Row 3: 异动 + 尾盘过滤器 + Actions
- 添加 `.bt-select` / `.bt-toggle` CSS class

### 面板自动展开
- LS 恢复结果后自动 `classList.remove('collapsed')`
- 回测完成自动展开 (btFinishRun)

### 涨跌天数
- `btRenderKPIs(s, tradesArr)` 接受 trades 参数
- 从 `trail_80 > 0` / `< 0` 计算 涨/跌 天数
- 显示在 KPI "笔数/分布" 组

### 三方案对比卡
- 新增 `_btRenderThreeWay()` — 统一对比基线 + WR1000 + V2
- 在 btFinishRun、tab 切换、LS 恢复时自动渲染
- 每策略显示: 累计/胜率/平均/涨跌天/回撤/笔数

### top_n 扩展
- `#bt-topn` 新增 4 只 / 5 只 选项

### SW cache v108

---

## 验证

```
回归测试: 24/24 (2 跳过的为外部 API 超时, 与本次改动无关)
```

### 前端检查
- 3 行 bt-ctrl-row ✓
- 7 bt-select ✓ (hold/topn/sample/breadth/breadth-soft/sector-hot/sector-inflow)
- 5 bt-toggle ✓ (surge/index-late/sector-late/vwap/tail-vol)
- top_n 含 4/5 ✓
- 面板自动展开 ✓
- 三方案对比卡 ✓
- 涨跌天数 ✓
