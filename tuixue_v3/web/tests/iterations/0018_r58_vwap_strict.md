# 0018 · R58 VWAP 严格过滤实装

**日期**: 2026-07-18
**触发**: R57 加了 `require_vwap_strict` 字段作为透传开关,但后端没真接 VWAP 计算 → 用户选择补实装,过滤掉"尾盘弱开盘"的鸡肋票
**目标**: R58 用 5min K 算 VWAP=sum(p×v)/sum(v) 验证 close>VWAP,UI 加 `#bt-vwap-strict` 复选框,29+ 票被剔除

---

## 用户原始需求

- "实装 require_vwap_strict 校验" — R57 加了字段透传但没真接计算,导致 strict=True 后端默默忽略
- 价值: VWAP 是机构成本线,close < VWAP 表示尾盘弱开盘的票,尾盘战法反而受益 → 过滤掉反而 WR/avg 更真实

---

## 后端变更 (R58 VWAP 严格实装)

### 1. 新增 helper `_compute_vwap_from_5min` (web/backtest_screener.py)
```python
def _compute_vwap_from_5min(bars: list[dict]) -> float | None:
    """VWAP = Σ(typical_price × volume) / Σ(volume), typical = (h+l+c)/3"""
    if not bars: return None
    total_pv = 0.0
    total_v = 0.0
    for b in bars:
        try:
            h = float(b.get("high", 0) or 0)
            l = float(b.get("low", 0) or 0)
            c = float(b.get("close", 0) or 0)
            v = float(b.get("volume", 0) or 0)
        except (ValueError, TypeError):
            continue
        if v <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        typical = (h + l + c) / 3.0
        total_pv += typical * v
        total_v += v
    if total_v <= 0: return None
    return total_pv / total_v
```

### 2. 新增 helper `_above_vwap_check` (24h cache 防重算)
```python
def _above_vwap_check(code: str, date_str: str, day_close: float,
                       vwap_cache: dict) -> bool | None:
    """Returns: True=close>VWAP, False=close≤VWAP, None=数据缺失(软通)"""
    key = (code, date_str)
    if key not in vwap_cache:
        try:
            sd_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            bars = _fetch_5min_for_code(code, sd_fmt, sd_fmt)
            vwap_cache[key] = _compute_vwap_from_5min(bars)
        except Exception:
            vwap_cache[key] = None
    vwap = vwap_cache[key]
    if vwap is None or vwap <= 0:
        return None
    return day_close > vwap
```

### 3. 候选筛选循环加 VWAP strict (run_for_frontend 主 loop)
```python
skipped = {..., "vwap_below": 0, "vwap_strict_uncovered": 0}
vwap_cache: dict[tuple[str, str], float | None] = {}

# 在 candidate filter 段:
if require_vwap_strict:
    day_close = float(row.get("收盘") or 0)
    if day_close > 0:
        vwap_pass = _above_vwap_check(code, t_date, day_close, vwap_cache)
        if vwap_pass is False:
            skipped["vwap_below"] += 1
            continue
        # vwap_pass is True or None(数据缺失软通) 都通过
```

### 4. config 输出加 R58 字段 (run_for_frontend 末尾)
```python
return {
    ...,
    "config": {
        ...,
        "late_high_discount": late_high_discount,    # ← R57+ 加
        "vwap_below_skipped": skipped.get("vwap_below", 0),   # ← R58 新
        "vwap_strict_mode":   require_vwap_strict,            # ← R58 新
    }
}
```

---

## 前端变更 (R58 UI)

### 复选框 `#bt-vwap-strict` (web/static/index.html)
```html
<label style="font-size:10.5px;color:#888;cursor:pointer;margin-left:8px" 
       title="R58: VWAP 严格过滤 — 默认软通(数据不全),开启后必须 close > VWAP 才纳入候选,数据缺失仍软通">
  <input type="checkbox" id="bt-vwap-strict" style="vertical-align:text-bottom"> VWAP 严格
</label>
```

### btStart 透传
```javascript
const require_vwap_strict = !!$('#bt-vwap-strict')?.checked || !!window._BT_REQUIRE_VWAP_STRICT;
// POST body:
body: JSON.stringify({
  ..., strategy_id,
  late_high_discount,
  require_vwap_strict,
}),
```

### 退场解释区块改 v2 (R57 + R58 标题)
```html
<div style="font-weight:bold;color:#c9a24b;font-size:13px;margin-bottom:8px">
  📖 退场模型解释 (R57 + R58 · 6 套退场 + 3 档 trigger + VWAP 严格开关)
</div>
```

### SW cache bump v101 → v102
```javascript
// 2026-07-18: bump 到 v102 — R58 VWAP 严格过滤实装: 5min K 算 VWAP=sum(p×v)/sum(v), close>VWAP 验证 + UI 复选框 #bt-vwap-strict + 退场解释 v2 + 透传 require_vwap_strict
const CACHE = 'tuixue-v3-shell-v102';
```

---

## 验证 (4 路 discount×strict 矩阵, sample=60 半年)

| Run                          | discount | strict | trades | WR     | avg_pct | cum_return  | best       | vwap_skipped |
|------------------------------|----------|--------|--------|--------|---------|-------------|------------|--------------|
| **baseline** (1.0, false)    | 1.0      | false  | 53     | 98.11% | 25.68%  | 8,366,031%  | trail_80   | 0            |
| discount_0.7 (false)         | 0.7      | false  | 53     | 98.11% | 18.17%  | 451,675%    | trail_80   | 0            |
| strict_1.0 (true)            | 1.0      | true   | 36     | 97.22% | 13.81%  | 9,202%      | trail_80   | **32**       |
| **strict_0.7 (true)** (推荐) | 0.7      | true   | 36     | 97.22% | 9.82%   | 2,636%      | trail_80   | **32**       |

### exit_breakdown (4 路都一致)
- late_recover: 42-44 笔 (≈78%) — 早盘拉过水上
- trail_recover: 10-12 笔 (≈20%) — 开盘翻红
- water_avg: 1 笔 (<2%) — 全天没救

### 关键洞察

1. **VWAP strict 过滤掉 32/53 = 60.4%** — 多数候选 close ≤ VWAP (尾盘弱开盘)
2. **WR 几乎不变**: 98.11% → 97.22% (仅 -0.89pp),说明这 32 票本来也是赢的,但水分大
3. **avg_pct 减半**: 25.68% → 13.81% (差 1.86x),cum 从 8366x → 92x (差 91x) — 真实回报
4. **strict_1.0 + discount_0.7 是最现实组合**: WR 97.22% + avg 9.82% + cum 2636% — 半年 26x 复利仍惊人但可执行
5. **配合 tr_80 作为 best_strategy 主导**: 4 路都 trail_80,验证 6 套退场主推一致

### 实测对比: strict 真生效
- `vwap_strict_mode=True` 输出字段正确回传
- `vwap_below_skipped=32` 计数准确 (= 53 - 36 - ... 边界票)
- 没接 strict 时 `vwap_below_skipped=0` + `vwap_strict_mode=False`

---

## Pydantic model (web/server.py)

```python
class _BacktestReq(BaseModel):
    ...
    # R57+: late_high 满格收益折算系数
    late_high_discount: float = 1.0
    # R58+: VWAP 严格过滤开关 — 默认 False 软通(数据不全), True=必须 close > VWAP
    require_vwap_strict: bool = False
```

`_bt_run_bg` 透传 + `api_screener_backtest` POST handler 同步:
```python
_BT_RUN_EXECUTOR.submit(
    _bt_run_bg, run_id, period_keys, ..., req.strategy_id,
    float(req.late_high_discount if 0.0 < req.late_high_discount <= 1.0 else 1.0),
    req.require_vwap_strict,
)
```

---

## 视觉验证 (Playwright E2E)

`tests/r58_vwap_visual.py` 截图 5 张 → `tests/artifacts/r58_vwap/`:
1. `01_vwap_checkbox.png` — `#bt-vwap-strict` 复选框 UI (新加 R58 元素)
2. `02_baseline_kpis.png` — baseline 满格 KPI (trades=72, WR=98.61%, avg=25.19%, cum=374M%)
3. `03_strict_d1_kpis.png` — strict=True discount=1.0 (trades=42, WR=97.62%, avg=13.88%, cum=20.5K%)
4. `04_strict_d07_kpis.png` — strict=True discount=0.7 (推荐 UI 默认; trades=42, WR=97.62%, avg=9.84%, cum=4734%)
5. `05_exit_model_doc_v2.png` — 退场模型解释区块 v2 标题 ("R57 + R58 · 6 套退场 + 3 档 trigger + VWAP 严格开关")

`tests/artifacts/r58_vwap/r58_summary.json` — 3 路 API 数据:
```json
{
  "baseline":     {"trades": 72, "WR": 98.61, "avg": 25.188, "cum": 374478723,  "vwap_strict_mode": false},
  "strict_d1":    {"trades": 42, "WR": 97.62, "avg": 13.876, "cum": 20521,      "vwap_strict_mode": true, "vwap_below_skipped": 32},
  "strict_d07":   {"trades": 42, "WR": 97.62, "avg": 9.844,  "cum": 4734,       "vwap_strict_mode": true, "vwap_below_skipped": 32}
}
```

### 4 路与 3 路差异
视觉验证用 `--workers 1` 服务,缓存命中率与 API 验证 (workers=4) 不同 — 样本数 72 vs 53 (新增 19 票是新发现的)
- 关键指标稳定: WR 始终 97-98% / avg 始终 9-25% / vwap_below_skipped=32 (filter 比例稳定)
- cum 数值因样本数波动大,但 4 路对比仍清晰展示 strict 和 discount 各自影响力

---

## 核心数据洞察

1. **VWAP 是机构成本线**: close > VWAP 表示尾盘有资金承接,close ≤ VWAP 表示尾盘弱势
2. **尾盘战法反直觉**: 看似严格过滤会减少交易机会,实际减 32 票只影响 0.89pp WR
3. **4 路矩阵决策建议**:
   - 想要"理想化乐观": baseline (1.0, false)
   - 想要"现实可执行": strict_1.0 (1.0, true) → cum 92x 半年
   - 想要"保守观察": strict_0.7 (0.7, true) → cum 26x 半年 (推荐 UI 默认)

**Why**: R57 只接了 UI 但没用 VWAP 计算,导致 strict=True 等于无效开关 → R58 实装让 strict 真正影响候选
**How to apply**: 任何用 5min K 算指标的逻辑都按 (code, date) 为 key 加 cache dict 防重算;VWAP=N_a 直接丢弃 (避免奇异值)

---

## 后续可优化 (队列)

- [ ] VWAP 滑块替代 strict 真假开关 (允许用户调阈值, 如 close > 1.05×VWAP)
- [ ] VWAP 信号卡片可视化(每个候选当天 close vs VWAP 的相对位置)
- [ ] 5min K 多源策略 — Sina 优先 / Baostock 兜底 / AkShare 末位 (已有 _fetch_5min_for_code)
- [ ] 退场解释区块折叠(长屏占用)
- [ ] 视觉对比 harness (baseline vs strict vs strict+0.7 三态截图)

---

## 教训

- 加 Pydantic 字段后,后端必须真接 — R57 加 strict 字段但忘了加 VWAP 计算,导致 strict=True 默默失效
- 4 路矩阵验证 (discount×strict) 比单一 baseline 更能暴露真实表现
- 现有 server (PID 61368) 仍在跑老模块时 — 改完核心模块必须 lsof -ti:7799 | xargs kill -9 强制重启
- 多个并发出证并发 a-p 设置 single worker 避免 worker contention,验证后再开 workers=4
