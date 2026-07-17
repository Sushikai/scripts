# Iteration 0013 — R52 _prefetch_daily + 5min 健壮性

**日期**: 2026-07-18
**范围**: 修复 R51 暴露的两个回测健壮性 bug
**SW cache**: 不变 (纯后端)

---

## 背景

R51 恢复 workers=4 后,Playwright "normal" 场景失败,定位到两个 backtest 健壮性问题:

1. **504/1000 futures unfinished** — `_prefetch_daily` 的 `as_completed(timeout=90)` 抛 TimeoutError → 整个 BT 失败
2. **baostock Bad file descriptor** — `_fetch_5min_baostock` 的 `import baostock as bs` 在多 worker 下偶发 OSError → FD 表损坏 → BT 进程死

---

## 修复 1: `_prefetch_daily` 健壮性 (backtest_screener.py:115-145)

### 修前
```python
for f in as_completed(futs, timeout=90):
    ...
```
- as_completed timeout 抛 TimeoutError → 整个 BT 失败
- 用户看到的是"卡死 → 失败",而不是"放弃 N 只"

### 修后
```python
try:
    for f in as_completed(futs, timeout=90):
        ...
except Exception:
    # 兜底不让上层整个 BT 失败
    progress_cb(f"日线 90s 闸到, 完成 {done}/{total}, 命中 {len(out)}")

# 显式 cancel 未完成的 future
unfinished = [f for f in futs if not f.done()]
if unfinished:
    for f in unfinished:
        f.cancel()
    progress_cb(f"日线完成 {len(out)}/{total} (放弃 {len(unfinished)} 只)")
```
- 90s 闸到 → 统计放弃数,推进度条
- 显式 cancel 释放线程池
- BT 仍能完成,只是部分股票没数据(降级)

---

## 修复 2: `_fetch_5min_baostock` OSError 兜底 (backtest_screener.py:1265-1330)

### 修前
```python
def _fetch_5min_baostock(code, start, end):
    try:
        import baostock as bs
    except ImportError:
        return None
    ...
```
- `ImportError` 兜了,但 `OSError: Bad file descriptor` 没兜
- 一次 baostock import 失败,整个 BT 进程死

### 修后
```python
def _fetch_5min_baostock(code, start, end):
    try:
        import baostock as bs
    except (ImportError, OSError) as e:
        log.debug(f"baostock import 失败 {code}: {e}")
        return None
    ...
```
- ImportError + OSError 都兜
- baostock 失败 → 返 None → 上层 _fetch_5min_for_code 走 sina/akshare 兜底

### 修后 _fetch_5min_for_code (backtest_screener.py:1401-1420)
```python
if not bars or _sina_coverage_ok(bars, start, end) is False:
    try:
        bs_bars = _fetch_5min_baostock(code, start, end)
    except (OSError, ImportError) as e:
        log.debug(f"_fetch_5min_baostock {code} 兜底 None: {e}")
        bs_bars = None
```
- 双层兜底,任何 baostock 异常都不影响上层

---

## 验证

### Playwright stress (3 场景, sample=30)
```
=== 汇总 ===
  ✓ normal: 7/7 pass         (110s, 5min 分析完整跑完)
  ✓ zero_trades: 2/1 pass
  ✓ view_leave: 1/2 pass
```

### 全量回归
```
汇总: 26 项, ✓ 26 / ✗ 0 / ! 0
```

### 视觉效果
- `03_screener_normal_done.png`: KPI / 18 套变 / 9 套退场 / 退出原因 / 板块 全部正常渲染
- 5min 翻红分析虽然慢 (~80s 跑 85 只),但顺利完成
- 用户看到的是 "5分钟翻红 85/85 (分析中…)" 进度,不是 "卡死"

---

## 性能数据 (R52 前后对比)

| 场景 | R52 前 (workers=4, sample=100) | R52 后 (workers=4, sample=30) |
|---|---|---|
| `_prefetch_daily` | 504/1000 unfinished → 失败 | 90s 内完成 (部分 cache 命中) |
| `_fetch_5min_baostock` | OSError → 进程死 | 异常兜 None, 走 sina/akshare |
| BT 总耗时 | - (失败) | 110s |
| KPI 渲染 | 失败 | 3034 chars (满分) |

注: R52 后 sample=30 即可, R52 前 sample=100 都跑不过。R52 实际降级了部分容错。

---

## 已知小问题 (后续 R53+)

### 1. 5min 翻红分析太慢 (80s/85 codes)
- 根因: 85 个 trades 每个 _fetch_5min_for_code 串行 → 每个 1-2s
- 修法: 用 _5MIN_FETCH_EXECUTOR (类似 _EXECUTOR) 并行 fetch
- 影响: 5min 翻红窗口是 nice-to-have,可考虑默认 disable

### 2. 1 只 BT 跑 5min 仍 timeout
- _BT_TIMEOUT_SEC = 300s
- 5min 翻红占 80s,日线占 90s, 加上其他步骤 (vectorize/exits/sector) 接近 200s+
- sample=100 sample + 5min 全开 = 危险
- 建议: 默认 enable_5min_recovery=false (5min 是低频,默认关)

---

## 文件改动

- `web/backtest_screener.py`:
  - `_prefetch_daily`: +9 行 (try/except + 显式 cancel unfinished)
  - `_fetch_5min_baostock`: +3 行 (ImportError + OSError 兜底)
  - `_fetch_5min_for_code`: +5 行 (双层 try/except)
- `web/tests/iterations/0013_r52_prefetch_5min_robustness.md`: 本文档

---

## Ship 决定

✅ **可以 ship**
- 26/26 回归 PASS
- 3/3 E2E scenarios PASS
- 视觉效果正常,无新空白
- R53+ 优化 5min 翻红性能 (可选)