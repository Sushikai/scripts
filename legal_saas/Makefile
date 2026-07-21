.PHONY: help install dev backend frontend test build clean seed

help:
	@echo "Legal SaaS · 本地开发命令"
	@echo "  make install     安装后端 + 前端依赖"
	@echo "  make dev         同时启动后端 + 前端 (推荐开发用)"
	@echo "  make backend     只启动 FastAPI (port 7800)"
	@echo "  make frontend    只启动 Vite (port 5173)"
	@echo "  make seed        初始化 SQLite + 种子模板"
	@echo "  make test        跑 pytest"
	@echo "  make build       构建前端 + 拷到后端静态目录"
	@echo "  make clean       清理缓存"

install:
	cd backend && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
	cd frontend && pnpm install

seed:
	cd backend && source venv/bin/activate && python scripts/init_db.py

backend:
	cd backend && source venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 7800 --reload

frontend:
	cd frontend && pnpm dev

dev:
	@echo "启动后端 (:7800) + 前端 (:5173),用两个终端分别跑 make backend / make frontend"

test:
	cd backend && source venv/bin/activate && pytest tests/ -v

build:
	cd frontend && pnpm build
	rm -rf backend/app/web/static
	mkdir -p backend/app/web/static
	cp -r frontend/dist/* backend/app/web/static/
	@echo "✓ 前端已构建并拷到 backend/app/web/static/"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/data/db.sqlite* backend/app/web/static