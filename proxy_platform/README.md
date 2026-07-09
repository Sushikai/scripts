# ProxyPlatform 代理管理平台

> 前后端分离的专业代理节点管理平台，支持 VMess / VLESS / Trojan / Shadowsocks 等多种协议。

![ProxyPlatform](https://img.shields.io/badge/ProxyPlatform-v1.0.0-0ea5e9?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.9+-blue?style=for-the-badge)
![React](https://img.shields.io/badge/React-18-61dafb?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

---

## 功能特性

- 🔐 **用户系统** - 注册/登录/JWT认证
- 🖥️ **节点管理** - 添加/编辑/删除代理节点
- 🔑 **多协议支持** - VMess / VLESS / Trojan / Shadowsocks
- 📊 **流量监控** - 节点流量使用统计
- 📱 **订阅功能** - 一键订阅所有节点
- 🎨 **现代化UI** - 深色主题，玻璃拟态设计
- 🔄 **实时同步** - 节点状态自动更新

---

## 项目结构

```
proxy_platform/
├── backend/                 # 后端 (FastAPI)
│   ├── api/                # API路由
│   │   ├── auth.py         # 认证
│   │   ├── nodes.py        # 节点管理
│   │   └── users.py        # 用户代理
│   ├── models/             # 数据模型
│   │   ├── models.py       # SQLAlchemy模型
│   │   └── schemas.py      # Pydantic模式
│   ├── services/           # 业务逻辑
│   │   └── xray_service.py # Xray配置生成
│   ├── config.py           # 配置
│   └── database.py         # 数据库
├── frontend/               # 前端 (React + Vite)
│   └── src/
│       ├── components/     # 组件
│       ├── pages/          # 页面
│       └── store/          # 状态管理
├── main.py                 # 后端入口
└── requirements.txt        # Python依赖
```

---

## 快速部署

### 后端

```bash
cd /Users/kaikai/scripts/proxy_platform
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 20210 --reload
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:20211`

---

## 初始化管理员

首次部署后，通过API创建管理员用户：

```bash
curl -X POST http://localhost:20210/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "email": "admin@example.com", "password": "your-password"}'
```

然后手动修改数据库将 `is_admin` 设为 `1`。

---

## API 文档

启动后访问：`http://localhost:20210/docs` (Swagger UI)

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI + Uvicorn |
| 数据库 | SQLite (SQLAlchemy) |
| 认证 | JWT (python-jose) |
| 前端框架 | React 18 + Vite |
| 样式 | Tailwind CSS |
| 状态管理 | Zustand |
| 图标 | Lucide React |

---

## 注意事项

⚠️ 本项目仅供学习和合法使用，请遵守当地法律法规。

如需在生产环境部署，请：
1. 修改 `SECRET_KEY` 环境变量
2. 配置 CORS 白名单
3. 使用 Nginx 反向代理
4. 配置 HTTPS
