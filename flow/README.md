# flow — 视频生产一体化平台

把 `/Users/kaikai/scripts/` 下散落的视频生产脚本统一包装,提供 Web UI、进度可视化、一键启动、跨工具日志聚合。

## 4 个 MVP 工具

| 工具 ID | 名称 | 主要步骤 |
|---------|------|---------|
| `info_gap` | 信息差流水线 | 7 步:研究→脚本→配音→素材→合成→风格对比→上传 |
| `fengge` | 峰哥切片 | 5 步:B站推荐→下载→80%裁剪→LLM简介→上传+评论引流 |
| `tiktok_story` | TikTok 故事 | 6 步:TikTok/YouTube→下载→字幕→裁剪→B站+抖音上传 |
| `material_collector` | 素材采集库 | 4 步:多源爬取→ADB→处理(去重+打标)→导出 |

## 端口

- **flow**:8810(与 tuixue_v3:7799 完全错开)
- **外网**:cloudflared 临时隧道(`./run_tunnel.sh`)

## 启动

```bash
# 安装依赖
pip install -r requirements.txt

# 直接跑
python3 -m backend.main

# 或用 run.sh(默认前台,可改后台)
./run.sh

# 启动 cloudflared 临时隧道(可选)
./run_tunnel.sh
# 启动后查看 URL:
cat ../tunnel_url.txt
```

## 守护进程

```bash
cp launchd/com.kaikai.flow.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.kaikai.flow.plist
launchctl start com.kaikai.flow
```

## 测试

```bash
pytest tests/ -x -q            # 全量
pytest tests/ -m 'not slow'    # 跳过 e2e + visual
pytest tests/test_perf_api.py  # 仅性能基准
```

## 架构

```
flow/
├── backend/                # FastAPI + 异步 JobRunner
│   ├── main.py            # lifespan + 中间件栈
│   ├── envelope.py        # {ok, data, error, trace_id} 协议
│   ├── middleware/        # trace → access_log → timeout → rate_limit
│   ├── routers/           # projects/jobs/tools/dashboard/assets/...
│   ├── wrappers/          # 4 个 ToolWrapper(包装原脚本)
│   ├── services/          # job_runner + thumb_cache
│   ├── db/repo.py         # SQLite WAL + safe_write
│   ├── cache/store.py     # L2 SQLite(预留 L1 Redis)
│   └── ai/client.py       # 统一 AI 客户端(注入防御/Schema/熔断)
├── frontend/              # 纯静态 SPA
│   ├── css/tokens.css     # 粉紫品牌设计令牌
│   ├── css/views.css      # 8 view 组件样式
│   ├── js/core.js         # api/sse/router/toast
│   └── js/view-*.js       # 8 个 view
└── tests/                 # 246 测试,~30s 全跑完
```

## Wrapper 协议

```python
from backend.wrappers.registry import ToolWrapper, register

class MyWrapper(ToolWrapper):
    tool_id = "my_tool"
    name = "我的工具"
    description = "..."
    steps = ["step1", "step2"]

    async def run_step(self, step, params, *, progress_cb, log_cb, is_cancelled):
        log_cb("starting step1")
        progress_cb(0.5, "halfway")
        if is_cancelled():
            raise RuntimeError("cancelled")
        progress_cb(1.0, "done")
        return {"output": "/path/to/output.mp4"}

register("my_tool", MyWrapper())
```

## 故障排查

| 症状 | 解决 |
|------|------|
| `/api/health` 返 5xx | 看 `access.log` JSON 末几行 |
| 端口冲突 | `lsof -ti:8810 \| xargs kill -9`(或换 FLOW_PORT) |
| 限频撞墙 | 提高 `FLOW_RATE_LIMIT_DEFAULT` |
| 工具跑失败 | 检查 `outputs/` 目录 + wrapper 内部 log |
| 隧道不通 | 重启 `./run_tunnel.sh`,DNS 偶发劫持(参见 scripts repo memory) |

## 进度

- ✅ Batch 1 基础设施(50 轮)
- ✅ Batch 2 4 个 wrapper(200 轮)
- ✅ Batch 3 前端视图(200 轮)
- ✅ Batch 4 性能 + 实时(200 轮)
- 🚧 Batch 5 视觉 + 移动端 + 运维(200 轮)

1000 轮迭代进行中。