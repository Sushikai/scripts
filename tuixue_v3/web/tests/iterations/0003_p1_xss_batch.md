# Iteration 0003 — P1 XSS Batch Fix

**日期**: 2026-07-16
**严重度**: P1 (前端存储型 / 反射型 XSS)
**触发点**: 4 个内联 innerHTML 注入点 + 1 个 race condition
**SW cache**: v35 → v36

---

## 漏洞汇总

| # | 位置 | 类型 | 严重度 | 触发路径 |
|---|---|---|---|---|
| B-04 | `app.js:1552-1556` | Stored XSS | P1 | 用户搜索股票 → 返回的 `s.name` / `s.code` 含 HTML 注入 |
| B-05 | `app.js:1733+` | Race condition | P1 | 用户快速点击 A→B,A 的 fetch 完成时会用过期数据覆盖 B |
| B-06 | `view-other.js:2671` | Stored XSS | P1 | 复盘录入股票名搜索下拉,`item.code` 未 escape |
| B-07 | `view-other.js:2898` | Stored XSS | P1 | AI 复盘返回的 `main_mistake` 字段半 escape 漏 `>` `'` `&` |

---

## 修复详情

### B-04: `app.js:1552-1556` (股票搜索)
**前**:
```js
box.innerHTML = results.map(s =>
  `<button class="result-pill" data-code="${s.code}" data-name="${s.name}">
    <span class="rp-code">${s.code}</span>
    <span class="rp-name">${s.name}</span>
  </button>`).join('');
```

**后**:
```js
box.innerHTML = results.map(s =>
  `<button class="result-pill" data-code="${escapeHtml(s.code)}" data-name="${escapeHtml(s.name)}">
    <span class="rp-code">${escapeHtml(s.code)}</span>
    <span class="rp-name">${escapeHtml(s.name)}</span>
  </button>`).join('');
```

**威胁**: 攻击者控制 `s.name = '"><img src=x onerror=alert(1)>'` 即可在用户点击搜索结果时执行任意 JS。 配合 cookie / localStorage 直接盗走凭证 / 自选股。

### B-05: `app.js:1733` race condition
**前**:
```js
async function loadStockDetail(code, date) {
  code = code.trim().padStart(6, '0');
  currentStockCode = code;
  _stopStockPoll();
  await _setQuickbarEnabled(code);          // <── await 1 (yield)
  const dateInput = $('#stock-date');
  let dateParam = date || dateInput?.value || '';
  const qs = dateParam ? `?_fresh=1&date=...` : '?_fresh=1';
  const cached = _stockCacheLoad(code, dateParam);
  if (cached) {
    try { renderStockDetail(code, cached); }  // <── await 2 (yield)
  }
  try {
    const data = await api(`/api/stock/${code}${qs}`);  // <── await 3 (大 yield)
    // BUG: 用户切股后,A 仍继续完成旧回调,把过期数据画到 B 上
    _stockCacheSave(code, dateParam, data);
    renderStockDetail(code, data);
    _startStockPoll(code);  // ← 更严重: 启动了 A 的轮询, 跟 B 抢数据
  }
}
```

**后**: 每个 `await` 之后立即 guard:
```js
  const cached = _stockCacheLoad(code, dateParam);
  if (cached) {
    if (currentStockCode !== code) return;
    try { renderStockDetail(code, cached); }
  }
  try {
    const data = await api(`/api/stock/${code}${qs}`);
    if (currentStockCode !== code) return;     // ← 关键
    _stockCacheSave(code, dateParam, data);
    ...
```

**效果**: 切股后旧 fetch 即便后续到达也直接 return,不污染 view 也不启动旧轮询。

### B-06: `view-other.js:2671` (复盘录入股票联想)
**前**: `${item.code}` 未 escape
**后**: `${escapeHtml(item.code)}`

### B-07: `view-other.js:2898` (主错误 pill)
**前**: 手写部分 escape `mm.replace(/</g,'&lt;').replace(/"/g,'&quot;')` — 漏 `>` `'` `&`
**后**: 全用 `escapeHtml(mm)` — 全字符 escape

---

## 验证

### 静态对比
```bash
curl /static/app.js | grep -c 'escapeHtml(s.name)'   # 5 处(本改+ 4 旧)
curl /static/view-other.js | grep -c 'escapeHtml(item.code)'  # 1
```

### 全量回归
```
汇总: 17 项, ✓ 17 / ✗ 0 / ! 0
```
无退化 + RCE fix 仍生效。

### 手动 XSS 模拟 (待 playwright e2e 加,先静态校验)
1. 搜索 API 返回 `name = '<img src=x onerror=window.__pwned=1>'`
2. 渲染按钮后 `window.__pwned` 应为 undefined (escapeHtml 把 `<` 转成 `&lt;`)
3. `data-name` 属性值同样被 escape

---

## 副产品

1. SW cache bump v35 → v36 (含 app.js / view-other.js / sw.js)
2. race condition 修复范围:`_stockCacheLoad`, `api()` await, error catch — 共 3 处 guard
3. `escapeHtml` 函数已全局可用 (app.js:2892),view-other.js / view-stock.js / view-all-stocks.js 全部共享

---

## 下一步

- [ ] 迭代 4: 加 Pydantic Field 入参校验到所有 GET 端点 (codes / page_size / period 等)
- [ ] 迭代 5: 性能优化 (debounce 搜索 + raf 节流 + _reviewState.flowsTimer 内存泄漏修)
- [ ] 迭代 6+: 23 个 audit bug 剩余
