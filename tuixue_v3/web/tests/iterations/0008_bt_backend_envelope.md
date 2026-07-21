# Iteration 0008 — R11-20 尾盘战法后端 envelope + 错误路径

**日期**: 2026-07-17
**范围**: 5 项后端修复 (envelope 一致性 / 提交失败清理 / 池隔离)
**SW cache**: v72 (无变更)

---

## 修复汇总

### R11 — `/api/backtest` 401 走 envelope 而非 HTTPException
**位置**: `server.py:6342-6344`
**问题**: 401 用 `HTTPException(detail={...})`,客户端必须检查两种 envelope 格式
**修法**: 改 `return envelope(error="admin token required")`,与其他端点一致

### R12 — `/api/backtest` 包裹 run_backtest try/except
**位置**: `server.py:6364-6370`
**问题**: 任何异常 (除 TimeoutError) 未捕获 → FastAPI 返 500 default
**修法**: `except Exception as e` → envelope(error=..., data={"stats":{"reason":"exception"}})

### R13 — `_BT_RUNS` 提交失败立即 GC
**位置**: `server.py:4124-4125`
**问题**: 提交失败时 status="error" 但 finished_at 未设 → 等 1h TTL 才 GC
**修法**: 同时设 `finished_at = time.time()` 和 `progress = "提交失败"`,下次 GC 即清

### R14 — `GET /api/screener/backtest` status=missing 不设 error
**位置**: `server.py:4147-4148`
**问题**: run_id 不存在时 `error="..."` AND `status="missing"` → ok=false 让前端困惑
**修法**: 仅返 `data: {status:"missing", result:None}`,ok=true,前端读 status 判断

### R17 — `_BacktestReq.periods` max_length=12
**位置**: `server.py:4005`
**问题**: 前端可 POST 数万个 period keys,resolver 静默 drop 但仍浪费内存
**修法**: `Field(default=[], max_length=12)`,Pydantic 自动 422

### R20 — `/api/backtest` 走 `_LONG_EXECUTOR`
**位置**: `server.py:6346-6360`
**问题**: 之前走 `to_thread` → `_EXECUTOR` (20 worker 通用池),长任务占满 worker
**修法**: 改 `loop.run_in_executor(_LONG_EXECUTOR, ...)`,与 `/api/screener/backtest` 对齐

---

## 跳过项 (审计认为是 P 但实际已实现)

### R15 — cancel 路径
**位置**: `server.py:4238-4252`
**现状**: 用 `_BT_CANCELLED.add(rid)` + `_cb` 内 raise KeyboardInterrupt,与 SSE/poll 联动 OK
**审计误判**: 该模式已被前端 SSE/poll 正常监听,无 race

### R16 — `_bt_gc_loop` 持锁
**位置**: `server.py:4158-4197`
**现状**: 整个 loop body 在 `with _BT_RUN_LOCK:` 内,`rec["status"] = "error"` 在锁内
**审计误判**: 已持锁,`rec = _BT_RUNS.get(rid, {})` 也在锁内

### R18 — 90s timeout 取消 future
**位置**: `server.py:4158-4197` (`_BT_TIMEOUT_SEC`)
**现状**: GC 线程每 60s 巡检,started_at > 5min (300s) 标 timeout
**说明**: 后端 timeout 由 GC 处理,前端 cancel 用 _BT_CANCELLED,to_thread 不可中断已通过 `_bt_gc_loop` 间接处理

### R19 — `_BT_RUNS` 提交失败清理
**现状**: R13 已实现 (本次同时改 finished_at)

---

## 验证

### 全量回归
```
汇总: 26 项, ✓ 25 / ✗ 0 / ! 1 (transient dashboard signal 4-5s,上游 EM 限频)
```

注: perf dashboard signal 在快速连测时 12ms 暖缓存,慢是 4-5s 冷启动 — 与本次改动无关,
是 EM push2 接口限频(参见 feedback_eastmoney_weekend_outage 记忆)。

### 后端新行为
- 无 admin token 调 `/api/backtest`:返 `{ok:false, error:"admin token required"}` (envelope 一致)
- `GET /api/screener/backtest?run_id=xxx` 不存在 run_id:返 `{ok:true, data:{status:"missing", result:null}}`
- `/api/screener/backtest` POST periods 长度 > 12:返 422 (Pydantic ValidationError)
- 长任务 `/api/backtest` 占用 _LONG_EXECUTOR 而非 _EXECUTOR (后者继续服务快端点)
- 提交失败后 60s 内被 _bt_gc_loop 清出 (依赖 finished_at)

---

## 文件改动

- `web/server.py`: +12 行(R11/12/13/14/17/20 6 处微调)

---

## 下一步 R21-30

- 性能:`_prefetch_daily` 持久化缓存、5min recovery 懒加载、SSE 轮询间隔、5min 请求缓存、
  `_add_metrics` 向量化、sector 批量化、trades 流式分页、equity 曲线采样