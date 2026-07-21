# Iteration 0001 — 回归基线

**日期**: 2026-07-16
**范围**: 16 项回归测试 (12 API + 2 静态资源 + 2 安全)
**结果**: 12 PASS / 1 FAIL / 3 ERROR

## 真实 Bug

| ID | 严重度 | 位置 | 症状 |
|---|---|---|---|
| B-01 | P2 | `/api/stock/{code}/intraday` (server.py:3034) | 不存在代码 (如 999999) 返回 `ticks:[{...empty...}]` 而非 `ticks:[]`;前端可能误以为是 1 笔交易 |
| B-02 | P3 | `/api/review/positions` 不存在 | 只有 DELETE `/api/review/positions/{code}`,没有 GET list (positions 在 portfolio 响应里) |

## 测试本身 Bug (非服务器)

| ID | 描述 | 修法 |
|---|---|---|
| T-01 | POST 非法入参期望 error,实际 FastAPI 返 422 (Pydantic 默认) | 接受 422 算通过 |
| T-02 | XSS 测试期望 error,实际不存在代码返 404 | 接受 404 算通过 |

## 下一步

1. 修 B-01 (代码不存在应清空 ticks, 或返 404 error)
2. 扩展 Playwright 端到端 (5 view × desktop + mobile)
3. 加性能 baseline (LCP / 长任务 / 内存)
4. 修前端 XSS esc 缺失
5. 加前端错误处理 + loading 态