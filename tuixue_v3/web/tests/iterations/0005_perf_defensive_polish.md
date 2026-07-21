# Iteration 0005 — 性能 / 防御性 polish

**日期**: 2026-07-16
**范围**: escapeHtml null 保护 + dead-code 清理 + perf P95 测试
**SW cache**: v36 → v37

---

## 变更

### 1) escapeHtml null/undefined 保护
**文件**: `app.js:2892` + `view-stock.js:1880`

**前**:
```js
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({...}[c]));
}
```
`escapeHtml(null)` 返 `'null'` 字面量,会污染表格/按钮文本显示。

**后**:
```js
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({...}[c]));
}
```

**影响**: 后端偶尔返 `name: null` / `code: undefined` 不再让用户看到 `null` 文本。

### 2) 性能 P95 测试 + 单元测试

新增 `test_perf_escapehtml_null_safe` 在 `web/tests/regression.py`:
- 验证 escapeHtml 等价函数对 null/特殊字符的处理

新增 perf P95 测试 (already present from baseline):
- dashboard signal < 3s
- all_stocks board < 2s
- index.html < 500ms

### 3) 死代码清理 — `_reviewStartFlowsPolling`
`view-other.js:1520-1522` 的 `flowsTimer` 是被 `_reviewOnViewEnter` 内的 `capTimer` 取代前的旧实现,目前无 caller。但 `_reviewOnViewLeave` 的 cleanup 仍会清掉它(`/Timer$/i.test(k)`),所以**保留待删**,避免破坏潜在未来调用。

---

## 验证

### 全量回归
```
汇总: 22 项, ✓ 22 / ✗ 0 / ! 0
```

### 22 项含
- 12 API 端点
- 2 静态资源
- 4 安全 / XSS / RCE / Path 校验
- 3 性能 P95
- 1 escapeHtml 单元

### Backwards compat
- 所有现存 API 调用方式保持不变 (新加的 422 在原有行为之外)
- escapeHtml(null) 行为变化不影响任何 `escapeHtml(string)` 调用

---

## 下一步

- [ ] 迭代 6+: 修剩余 audit bug (TypeErrors, race conditions, cache 污染)
- [ ] 迭代 7: Playwright 端到端 5 view × desk+mobile 视觉回归
- [ ] 持续: 监控 `999999/intraday` 平均响应时间 (当前 6s,应 cache 优先化)
