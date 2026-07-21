# 0017 · R57 3 档 trigger + late_high_discount 折算开关

**日期**: 2026-07-18
**触发**: 用户 4 轮反馈 — 翻红概率太低 / 时间段在水上过也算翻红 / 满格数据过于乐观 / 加 late_high_discount 折算开关
**目标**: R57 3 档 trigger (trail_recover / late_recover / water_avg) + late_high_discount UI 控件 (1.0/0.7/0.5)

---

## 用户原始需求 (按时间顺序)

1. **"为什么翻红概率这么低"** — R56 9→6 套后, 只有开盘翻红才算, 实际盘中拉过水上的没算
2. **"只要时间段内价格在水上过就算翻红 而且按照价格在水上过算收益率"** — R57 3 档 trigger 核心
3. **"> 100% (软通, 历史分时不可得) 为啥拿不到"** — VWAP 软通 / 严格开关
4. **加开关 late_high_discount** — R57 满格过于乐观 (cum=8466873%) 用 0.7/0.5 折算

---

## 后端变更 (R57 3 档 trigger)

### 核心铁律 (用户定义)
```
9:30 翻红  (open > buy)                 → 拿 high 的 80% / 50% / 20%  (trail_recover)
9:30 不翻红 但 10:00 前 high ≥ buy      → 按早盘水上价格满格算 (late_recover)
全天没救                                → 统一水下均价 (water_avg)
强制基准: force_10 ≈ T+1 open, force_close = T+1 close
续涨停延后: T+1 开盘涨停 (open ≥ buy×1.095) → 交给 T+2
```

### `_simulate_from_cache_row` 加 late_high_discount (web/backtest_screener.py)
```python
def _simulate_from_cache_row(row_t1, buy_price, actual_10_close=None,
                              late_high=None,
                              late_high_discount: float = 1.0) -> dict | None:
    ...
    elif late:
        # ── R57: 10:00 前拉过 → 按水上价格满格算收益率 (用户铁律) ──
        # R57+ 折算: 实际不能在 9:30-10:00 高点全部卖出, 用 discount 反映可执行性
        ret_late_high = _p(late_high) * late_high_discount
        ret_trail_80 = round(ret_late_high, 3)
        ret_trail_50 = round(ret_late_high, 3)
        ret_trail_20 = round(ret_late_high, 3)
```

### `_simulate_batch` 加 late_high_discount (web/backtest_screener.py)
```python
def _simulate_batch(rows, buy_prices, actual_10_closes=None, late_highs=None,
                    late_high_discount: float = 1.0) -> list[dict | None]:
    ...
    # R57+ 折算: late_high × discount (默认 1.0 满格)
    ret_late_high = np.where(has_lh, (lh / bp - 1.0) * 100.0 * late_high_discount, 0.0)
```

### `run_for_frontend` 透传 (web/backtest_screener.py)
```python
def run_for_frontend(period_keys=None, hold_days=3, top_n=1, sample=1200,
                     ...,
                     late_high_discount: float = 1.0,
                     require_vwap_strict: bool = False,
                     progress_cb=None) -> dict:
    ...
    sim_results = _simulate_batch(rows_batch, buys_batch,
                                  late_highs=late_highs_batch,
                                  late_high_discount=late_high_discount)
```

### server.py Pydantic model (web/server.py)
```python
class _BacktestReq(BaseModel):
    ...
    # 2026-07-18 R57+: late_high 满格收益折算系数 (默认 1.0 = 用户原意满格)
    #   1.0 = 满格 (理想化: 9:30-10:00 拉到水上即满格卖出)
    #   0.7 = 保守 (实际可能错过部分高位, 7 折)
    #   0.5 = 极保守 (水下大幅震荡更现实, 半折)
    late_high_discount: float = 1.0
    # 2026-07-18 R57+: VWAP 严格过滤开关 (默认 False = 软通, 数据不全时跳过)
    require_vwap_strict: bool = False
```

### server.py _bt_run_bg 透传 (web/server.py)
```python
def _bt_run_bg(run_id, period_keys, hold_days, top_n, sample, ..., 
               strategy_id="baseline",
               late_high_discount: float = 1.0,
               require_vwap_strict: bool = False) -> None:
    ...
    r = _bt.run_for_frontend(
        ..., strategy_id=strategy_id,
        late_high_discount=late_high_discount,
        require_vwap_strict=require_vwap_strict,
        progress_cb=_cb,
    )
```

### server.py api_screener_backtest POST (web/server.py)
```python
_BT_RUN_EXECUTOR.submit(
    _bt_run_bg, run_id, period_keys, ..., req.strategy_id,
    float(req.late_high_discount if 0.0 < req.late_high_discount <= 1.0 else 1.0),
    req.require_vwap_strict,
)
```

---

## 前端变更 (web/static/index.html)

### 1. UI 控件 (3 档按钮)
```html
<span style="...">late_high 折算:</span>
<button class="bt-lhd" data-v="1.0" style="...">1.0 满格</button>
<button class="bt-lhd" data-v="0.7" style="...">0.7 保守</button>
<button class="bt-lhd" data-v="0.5" style="...">0.5 极保守</button>
```

### 2. 切换逻辑 + 高亮
```javascript
document.querySelectorAll('.bt-lhd').forEach((btn) => {
  btn.addEventListener('click', () => {
    const v = parseFloat(btn.dataset.v);
    window._BT_LATE_HIGH_DISCOUNT = v;
    // 视觉高亮当前选中
    document.querySelectorAll('.bt-lhd').forEach((b) => {
      const active = parseFloat(b.dataset.v) === v;
      b.style.background = active ? '#1f3a2c' : '#2a241c';
      b.style.color      = active ? '#84f4a8' : '#e8e1d4';
      b.style.borderColor = active ? '#2a5a3e' : '#3a3024';
    });
    toast(`late_high 折算 = ${v}`, 1800);
  });
});
window._BT_LATE_HIGH_DISCOUNT = 1.0;  // 默认 1.0 满格高亮
```

### 3. btStart 透传
```javascript
const late_high_discount = window._BT_LATE_HIGH_DISCOUNT ?? 1.0;
const require_vwap_strict = !!window._BT_REQUIRE_VWAP_STRICT;
window._BT_LAST_BODY = { ..., late_high_discount, require_vwap_strict };
// POST body 加这俩字段
body: JSON.stringify({ ..., strategy_id, late_high_discount, require_vwap_strict }),
```

### 4. 退场模型解释区块 (R57 · 3 列)
```
退场      | ① 开盘翻红 (T+1 open > 买入价) | ② 早盘拉过 (10:00 前 high ≥ 买入价) | ③ 全天没救
追涨 80%  | 卖在当日最高价的 80% 位置       | 按早盘水上价格满格算收益             | 水下均价止损
追涨 50%  | 卖在当日最高价的 50% 位置       | 按早盘水上价格满格算收益             | 水下均价止损
追涨 20%  | 卖在当日最高价的 20% 位置       | 按早盘水上价格满格算收益             | 水下均价止损
水下均价  | 不管翻不翻红 统一 (open+low)/2  | —                                   | —
强制 10:00| 10:00 前无条件卖出 ≈ T+1 open   | —                                   | —
强制尾盘  | 扛到 T+1 尾盘收盘价卖出         | —                                   | —
```
底部说明: **R57 关键改进**: 原来 ②类一律按水下均价止损, 现在按水上价格满格算 (满格假设能在 9:30-10:00 高点卖出)。实测约 60-70% 的票属 ②类, 是模型最大失血点。**追涨 80%** 仍为默认主推退场。

### 5. SW cache bump v88 → v89/v90
```javascript
// 2026-07-18: bump 到 v88 — R57+ late_high_discount 折算开关 + require_vwap_strict 透传
// 2026-07-18: bump 到 v89 — 40 轮流畅度优化
// 2026-07-18: bump 到 v90 — 删 LAN 扫码直进
const CACHE = 'tuixue-v3-shell-v90';
```

---

## 验证 (Python 直接调 + API + Playwright)

### A) 直接调 run_for_frontend 验证 3 档 discount (sample=60)
| late_high_discount | trades | WR     | avg      | cum         | trail_80 avg | cum         | 备注                |
|--------------------|--------|--------|----------|-------------|--------------|-------------|---------------------|
| 1.0 满格            | 53     | 98.11% | 25.67%   | 8,317,914%  | 25.67%       | 8,317,914%  | 复利爆炸            |
| 0.7 保守            | 53     | 98.11% | 18.16%   | 449,795%    | 18.16%       | 449,795%    | 满格 0.7x           |
| 0.5 极保守          | 53     | 98.11% | 13.15%   | 54,916%     | 13.15%       | 54,916%     | 满格 0.5x           |

exit_breakdown (相同 53 笔, trigger 分布不变):
- late_recover: 42 笔 (79.25%)
- trail_recover: 10 笔 (18.87%)
- water_avg: 1 笔 (1.89%)

discount 只影响 late_recover 档的 trail_80/50/20, 不影响 trail_recover 和 water_avg。

### B) Playwright 视觉验证 (sample=60, baseline + discount=1.0)
11 张截图 → `web/tests/artifacts/r57_discount/`:
- `01_discount_buttons.png` - 3 档按钮 (UI)
- `02_kpis_full.png` - 收益三件套 + 仓位换算 + 风控 (满格)
- `03_exits_full.png` - 6 套退场胜率对比 (trail_80/50/20 + water_avg + force_10/close)
- `04_monthly_full.png` - 月度表 12 列
- `05_exit_model_doc.png` - 📖 退场模型解释 (R57 · 6 套并行 + 3 档 trigger)
- `06_fullpage_baseline_full.png` - 1440 全页
- `discount_summary.json` - 3 档结构化数据 (累计复利差异 6000x → 500x)

### C) exit_breakdown 数据对比 (75 笔 sample)
```
discount_1.0:
  - late_recover: 56 笔 (77.78%) — 早盘拉过水上
  - trail_recover: 15 笔 (20.83%) — 开盘翻红
  - water_avg: 1 笔 (1.39%) — 全天没救
→ 大多数情况 ②类早盘拉过水上, 之前被误判成 water_avg 是最大失血点
```

### D) R70 _annualized_monthly 修复 (R56 重构后)
- `_annualized_monthly` 简单年化 (非复利) — 避免 1 周/2 周窗口爆炸到 988M%
- 软上限 9999% — UI 显示上限
- 月度表 mobile 加 overflow-x wrap

### E) Pydantic 校验
- `late_high_discount` 默认 1.0, 范围 [0, 1], 越界自动 fallback 1.0
- `require_vwap_strict` 默认 False (历史 5min 数据不全, 软通合理)

---

## 核心数据洞察

1. **late_recover 占比 77-80%** — 大多数情况属于 ②类早盘拉过水上, R56 之前误判 water_avg 止损, 是模型最大失血点
2. **trail_recover 占比 18-20%** — 开盘翻红, 走 high×0.8/0.5/0.2
3. **water_avg 占比 1-2%** — 全天没救, 走水下均价 (open+low)/2
4. **discount 0.7 → cum 减 16x** — 满格 8.3M% → 0.45M%, 仍高估
5. **discount 0.5 → cum 减 132x** — 满格 8.3M% → 0.055M%, 更现实

**Why**: 用户要求"按水上价格满格算收益率" 是 R57 核心铁律, 但满格 1.0 cum=8.3M% 不现实, 加 discount 开关让用户自行调整。
**How to apply**: 改退场模型必须确认 late_recover 档逻辑不破; 加新 trigger 档要同步 _simulate_batch + _simulate_from_cache_row + late_high_discount 折算位置 + 退场解释区块 4 处。

---

## 后续可优化 (队列)

- [ ] cross-cycle R1 推荐 strategy 自动锁定 (用户点 force_10 后, 下次默认跑 force_10)
- [ ] late_high_discount 滑块 (替代按钮) — 更平滑
- [ ] require_vwap_strict 实装 (需要 5min 历史数据 + VWAP 计算)
- [ ] 月度表 sparkline (单月多策略曲线)
- [ ] 退场解释区块折叠 (长屏占用)
- [ ] 移动端月度表 12 列横滚优化
- [ ] baseline↔WR1000 对比支持多窗口

---

## 教训

- 改核心模块后必须重启 server, 立即 curl API 看新字段, 不能只看磁盘文件 (旧 server 还在写)
- 端口冲突坑: pkill -9 -f "tuixue_v3" 后, 还要 lsof -ti:7799 | xargs kill -9 (旧 worker 持有 LISTEN 套接字)
- 路径切换坑: Bash CWD 经常被 reset, 调长脚本要用绝对路径, 不能依赖 cd
- Pydantic Field 校验: 越界值应静默 fallback (不要 422 中断用户流程)