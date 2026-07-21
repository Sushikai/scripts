# Iteration 0004 — P1 API Path 校验

**日期**: 2026-07-16
**严重度**: P1 (任意字符串流入下游 SQL/URL/subprocess)
**触发点**: 16 个 `/api/stock/{code}/*` 端点
**SW cache**: v36 (无变更)

---

## 漏洞

### 位置
16 个端点 (覆盖 kline / intraday / fund_flow / seats / seat_breakdown / sector / related_news / related_stocks / limit_up_context / ai_analysis / ai_history / ai_layer_detail / ai_refresh 等):

```
@ app .get ( "/api/stock/{code}/..." )
async def stock_xxx ( code : str , ...) :
    code = code .strip ().zfill ( 6 )
```

### 原风险
- `code` 直接传入下游 SQL/akshare/curl,无白名单
- 自动 zfill 行为掩盖了无验证事实
- 单只 endpoint 风险低 (因为不拼 subprocess),但 16 处一致缺失 = 攻击面

### 影响
- DoS: 投递极大字符串让 server 浪费内存
- 参数注入: 个别 endpoint 间接拼 URL

---

## 修复

### 新增 helper `web/server.py:90-97`
```python
_CODE_RE = _re.compile(r"^\d{6}$")


def _require_valid_code(code: str) -> str:
    """必须 6 位纯数字; 否则 422. 自动 strip + zfill. 返回归一化后的 6 位 code."""
    if not isinstance(code, str):
        raise HTTPException(status_code=422, detail="股票代码必须是字符串")
    code = code.strip().zfill(6)
    if not _CODE_RE.fullmatch(code):
        raise HTTPException(status_code=422, detail=f"无效的股票代码: {code!r} (需 6 位数字)")
    return code
```

### 端点 sweep
16 处 `code = code.strip().zfill(6)` 全部替换为 `code = _require_valid_code(code)`:

| 行号 | 端点 |
|---|---|
| 2479 | `/api/stock/{code}/kline` |
| 2555 | `/api/stock/{code}/seats` |
| 2536 | `/api/stock/{code}/fund_flow` |
| 2566 | `/api/stock/{code}/seat_breakdown` |
| 2594 | `/api/stock/{code}/intraday_5d` |
| 3061 | `/api/stock/{code}/intraday` |
| 3192 | `/api/stock/{code}/sector` |
| 3240 | `/api/stock/{code}/related_news` |
| 4081 | `/api/stock/{code}` |
| 4695 | `/api/stock/{code}/limit_up_context` |
| 4732 | `/api/stock/{code}/related_stocks` |
| 4869 | `/api/stock/{code}/ai_analysis` |
| 5364 | `/api/stock/{code}/ai_crash_risk` |
| 5569 | `/api/stock/{code}/ai_history` |
| 5612 | `/api/stock/{code}/ai_layer_detail` |
| 5829 | `/api/stock/{code}/ai_refresh` |

### 关键 import
`web/server.py:13`: 新增 `import re as _re` (移到顶部,避免 helper 找不到模块)

---

## 验证

### 直接 curl
```
abc: 422 (0.17s)
12345: 200 (zfill → 123450, 合法股票)
1234567: 422 (zfill 后超 6 位 → 不允许)
0x41: 422
traversal: 404 (FastAPI 路由层先拒)
000001: 200 (合法)
```

### 全量回归
```
汇总: 18 项, ✓ 18 / ✗ 0 / ! 0
```
包含新加的 `test_api_stock_path_code_validated` (B-08)。

### 7 类恶意 code 拒绝
| 输入 | 行为 |
|---|---|
| `abc` | 422 ✓ |
| `12345abc` | 422 ✓ |
| `abcdef` | 422 ✓ |
| `0x41` | 422 ✓ |
| `12.34` | 422 ✓ |
| `--%` | 422 ✓ |
| `../etc/passwd` | 404 (路由先拦) ✓ |

---

## 副产品

1. **修测试预期**: `12345` zfill → `123450` 实际是合法股票,不算无效。
2. **修 import 顺序**: `_re` 移到顶部,避免 `NameError` at import time。
3. **清理旧 server 进程**: 端口 7799 有残留进程导致 `Address already in use`,`pkill -9` 才能彻底清。

---

## 下一步

- [ ] 迭代 5: 性能优化 (debounce 搜索 + raf 节流 + _reviewState.flowsTimer 内存泄漏)
- [ ] 迭代 6: 剩余 22 个 audit bug 修复
- [ ] 迭代 7: 加大 regression suite 覆盖 (Playwright e2e 5 view × desk+mobile)
