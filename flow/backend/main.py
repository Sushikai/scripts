"""flow FastAPI 入口。

启动:`python3 -m backend.main` 或 `uvicorn backend.main:app --host 0.0.0.0 --port 8810`
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# 让 `python3 backend/main.py` 也能 import
if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import _constants as C
from .cache.store import init_cache, stats as cache_stats
from .db.repo import init_db
from .envelope import Code, ok, with_trace
from .middleware.access_log import AccessLogMiddleware
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.timeout import TimeoutMiddleware
from .middleware.trace import TraceIdMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 DB + cache + JobRunner,关闭时清理。"""
    Path(C.DB_PATH()).parent.mkdir(parents=True, exist_ok=True)
    init_db()
    init_cache()
    from .services.job_runner import start_runner_once, stop_runner_once
    from .wrappers.registry import register as reg_wrapper
    from .wrappers.builtin import register_builtin

    await start_runner_once()
    register_builtin(reg_wrapper)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("flow").info("flow started on :%s db=%s", C.PORT(), C.DB_PATH())
    yield
    await stop_runner_once()
    logging.getLogger("flow").info("flow shutting down; cache_stats=%s", cache_stats())


app = FastAPI(title="flow", version="0.1.0", lifespan=lifespan)

# 中间件栈(顺序:后注册的最先执行,所以顺序反过来写)
app.add_middleware(AccessLogMiddleware)  # 最后记录(包外层)
app.add_middleware(TimeoutMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TraceIdMiddleware)  # 最先注入
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本地工具,后面收紧
    allow_methods=["*"],
    allow_headers=["*"],
)


# === 路由挂载 ===
from .routers import jobs as jobs_router
from .routers import projects as projects_router
from .routers import tools_meta as tools_router
from .routers import dashboard as dashboard_router
from .routers import assets as assets_router
from .routers import accounts as accounts_router
from .routers import uploads as uploads_router
from .routers import logs as logs_router
from .routers import comments as comments_router

app.include_router(jobs_router.router)
app.include_router(projects_router.router)
app.include_router(tools_router.router)
app.include_router(dashboard_router.router)
app.include_router(assets_router.router)
app.include_router(accounts_router.router)
app.include_router(uploads_router.router)
app.include_router(logs_router.router)
app.include_router(comments_router.router)


# === 异常统一 envelope ===
@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    logging.getLogger("flow").exception("unhandled %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "data": None,
            "error": {"code": Code.INTERNAL, "message": str(exc)[:200]},
            "code": 1,
            "trace_id": getattr(request.state, "trace_id", None),
            "ts": int(time.time() * 1000),
        },
    )


# === 健康检查 ===
@app.get("/health")
def health(request: Request):
    return with_trace(request, {"status": "ok", "ts": int(time.time() * 1000), "version": "0.1.0"})


@app.get("/api/health")
def api_health(request: Request):
    return with_trace(request, {"status": "ok", "ts": int(time.time() * 1000), "version": "0.1.0", "cache": cache_stats()})


# === 静态资源(前端 SPA)===
if Path(C.FRONTEND_DIR()).exists():
    app.mount("/static", StaticFiles(directory=C.FRONTEND_DIR()), name="static")

    @app.get("/")
    def root_index():
        idx = Path(C.FRONTEND_DIR()) / "index.html"
        if idx.exists():
            return FileResponse(str(idx))
        return JSONResponse({"ok": False, "error": {"code": "NO_FRONTEND", "message": "frontend/index.html missing"}}, status_code=404)


def main() -> None:
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=C.HOST(),
        port=C.PORT(),
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()