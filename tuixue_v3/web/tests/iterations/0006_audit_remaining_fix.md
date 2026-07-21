# Iteration 0006 — 17 audit bug 全部修复

**日期**: 2026-07-17
**范围**: 3 P0 XSS + 1 P1 RCE + 3 P1 cache leak + 4 P1 TypeError + 1 P1 race + 1 P2 timer dup + 4 P2/P3
**SW cache**: v45 → v48 (含 linter 同步变更)

---

## Bug 汇总

### P0 XSS (3)
| ID | 位置 | 修法 |
|---|---|---|
| B-09 | `view-other.js:99-110` laws render | `escapeHtml(c.num/c.name/c.sub/items)` + `Array.isArray(c.items)` |
| B-10 | `view-other.js:200-204` Top10 AI footer | 全部字段 `escapeHtml` + `String(s.score_total ?? 0)` 数字化 |
| B-11 | `view-other.js:2192` `file.name` innerHTML | `escapeHtml(file.name)` |

### P1 RCE (1)
| ID | 位置 | 修法 |
|---|---|---|
| B-12 | `server.py:8203` `f"source {_env_sh}"` 进 bash -c | 改 `["bash", "-c", "source \"$1\" && env -0", "_", str(_env_sh)]` argv 形式 |

### P1 Memory leak (3)
| ID | 位置 | 修法 |
|---|---|---|
| B-13 | `ai_chat.py:27` `_CACHE` 无上限 | `_CACHE_MAX=1024`, LRU 写入时先清过期再按插入顺序淘汰 |
| B-14 | `app.js:313` `_ztChainCache` Map 无上限 | `_ZT_CHAIN_CACHE_MAX=500`, LRU helper `_ztChainCacheSet()` |
| B-15 | `app.js + view-stock.js` `intraDayCache` Map | `INTRADAY_CACHE_MAX=200`, helper `_intraDayCacheSet()` |

### P1 TypeError (3)
| ID | 位置 | 修法 |
|---|---|---|
| B-16 | `app.js:8458-8474` renderRows 数字字段 | `Number(...)` 显式转, `Array.isArray(r.domain)` 保护 |
| B-17 | `app.js:2566/2573/2601/3927` 多处 array spread | `Array.isArray(d.tags) ? d.tags : []` 防御模式 |
| B-18 | `app.js:7843/7961` verdict 字符串拼接 | `String(verdict).slice(0, 8)` + `Number(conviction)` |

### P1 Race (1)
| ID | 位置 | 修法 |
|---|---|---|
| B-19 | `app.js:1578` `_stockPollTimer` race | 已由 `code !== currentStockCode` 守卫覆盖,标记为 covered |

### P2 Timer dup (1)
| ID | 位置 | 修法 |
|---|---|---|
| B-20 | `view-other.js:1522` flowsTimer 与 capTimer 双跑 | `_reviewStartFlowsPolling` 改 no-op,只 clear 旧 interval |

### P2 TypeError (2)
| ID | 位置 | 修法 |
|---|---|---|
| B-22 | `view-other.js:1972-1977` `_reviewLoadStats` | 加 `safeNum()` helper,所有数字字段 null 保护 |
| B-23 | `view-stock.js:3192` `n.score.toFixed(1)` | `n.score != null ? n.score.toFixed(1) : '—'` |

### P2 LRU (1, audit 误报)
| ID | 位置 | 修法 |
|---|---|---|
| B-21 | `server.py:858-863` `_path_tier_hits` | **已有 LRU** (`if len(...) >= _IP_WINDOW_MAX * 2:`),audit 没看到 |

### P3 (2)
| ID | 位置 | 修法 |
|---|---|---|
| B-24 | 无 issue | — |
| B-25 | `_lastTradeDate` 首屏 race | 已有 view enter promise 阻塞,不动 |

---

## 验证

### 全量回归
```
汇总: 26 项, ✓ 26 / ✗ 0 / ! 0
```

新增 4 项覆盖本次修复:
- `static app.js 含全部修复` (B-04/14/15/16/17)
- `static view-other.js 含全部修复` (B-06/07/09/10/11/22)
- `server env.sh argv 安全` (B-12 + 全局 f-string 进 -c 检查)
- `server ai_chat cache LRU` (B-13)

### 22 → 26 项增量
- 12 API 端点
- 2 静态资源
- 4 安全 (XSS / RCE / Path / Argv)
- 2 RCE / cache LRU
- 3 性能 P95
- 1 escapeHtml 单元
- 2 static XSS 修复覆盖

---

## 副产品

1. SW cache v45 → v48 (linter 同步 bump)
2. 端口清理脚本:`pkill -9 -f "tuixue_v3.web.server"` + `pkill -9 -f "hypercorn"` 才能彻底清
3. linter 改 sw.js 时, edit 时机冲突 (state changed) — 一次 read-then-edit 解决

---

## 下一步

- [ ] Playwright 5 view × desk+mobile 视觉回归
- [ ] `/api/stock/999999/intraday` 12s 慢问题深查 (B-01 已修空 tick,但 endpoint 整体仍慢)
- [ ] 加 CSP nonce 进一步硬化 (已有 CSP header)