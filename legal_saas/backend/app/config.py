"""
legal_saas/backend/app/config.py
所有阈值常量集中处 —— 调优只动这一个文件。
"""
from __future__ import annotations
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGE_DIR.parent.parent
PROJECT_DIR = BACKEND_DIR.parent

DATA_DIR = Path(os.environ.get("LEGAL_DATA_DIR", str(BACKEND_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("LEGAL_DB_PATH", str(DATA_DIR / "db.sqlite")))
VECTOR_STORE_PATH = Path(os.environ.get("LEGAL_VECTOR_STORE_PATH", str(DATA_DIR / "vector_store")))
EXPORT_DIR = Path(os.environ.get("LEGAL_EXPORT_DIR", str(DATA_DIR / "exports")))
SESSIONS_DIR = DATA_DIR / "sessions"

for _p in (DATA_DIR, VECTOR_STORE_PATH, EXPORT_DIR, SESSIONS_DIR):
    _p.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════
# Server
# ═══════════════════════════════════════════════════
HOST = os.environ.get("LEGAL_HOST", "0.0.0.0")
PORT = int(os.environ.get("LEGAL_PORT", "7800"))
DEBUG = os.environ.get("LEGAL_DEBUG", "false").lower() == "true"


# ═══════════════════════════════════════════════════
# MiniMax API
# ═══════════════════════════════════════════════════
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.MiniMax.io/v1")
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
MINIMAX_TIMEOUT = 60
MINIMAX_MAX_RETRIES = 3


# ═══════════════════════════════════════════════════
# Embedding
# ═══════════════════════════════════════════════════
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "minimax")  # minimax | local
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_LOCAL_MODEL = os.environ.get("EMBEDDING_LOCAL_MODEL", "BAAI/bge-small-zh-v1.5")
EMBEDDING_DIM = 1536  # MiniMax text-embedding-3-small


# ═══════════════════════════════════════════════════
# 检索参数
# ═══════════════════════════════════════════════════
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "50"))
TOP_K = int(os.environ.get("TOP_K", "20"))
HYBRID_VECTOR_WEIGHT = float(os.environ.get("HYBRID_VECTOR_WEIGHT", "0.7"))


# ═══════════════════════════════════════════════════
# 任务队列
# ═══════════════════════════════════════════════════
TASK_WORKERS = int(os.environ.get("TASK_WORKERS", "2"))
TASK_MAX_CONCURRENT = int(os.environ.get("TASK_MAX_CONCURRENT", "4"))


# ═══════════════════════════════════════════════════
# LLM 角色预设
# ═══════════════════════════════════════════════════
ROLE_PRESETS = {
    "legal_expert": "你是一位资深的法律专家,精通中国法律体系,擅长分析法律问题、提供专业法律意见。回答需准确、有理有据,并引用相关法律条文。",
    "litigator": "你是一位经验丰富的诉讼律师,擅长起草各类诉讼文书(起诉状、答辩状、代理词、上诉状)。注重诉讼策略、事实论证、法条适用。",
    "corp_counsel": "你是一位专业的企业法务,擅长处理公司治理、合同审核、合规风控、股权架构、知识产权等事务。回答需兼顾商业可行性与法律风险。",
    "contract_specialist": "你是一位严谨的合同专员,擅长起草、审核各类商务合同。注重条款完备性、风险防控、权责对等。",
}


# ═══════════════════════════════════════════════════
# 文书类型 → 推荐结构
# ═══════════════════════════════════════════════════
DOC_TYPE_STRUCTURES = {
    "起诉状": ["当事人信息", "诉讼请求", "事实与理由", "证据清单", "此致法院"],
    "答辩状": ["答辩人信息", "答辩请求", "事实与理由", "证据清单", "此致法院"],
    "代理词": ["代理人信息", "代理意见", "事实部分", "法律部分", "结论"],
    "上诉状": ["上诉人信息", "上诉请求", "上诉理由", "此致法院"],
    "合同": ["当事人信息", "鉴于条款", "定义", "权利义务", "违约责任", "争议解决", "生效条款"],
    "律师函": ["致函对象", "事实陈述", "法律意见", "要求事项", "最后通牒"],
    "裁定书": ["申请人信息", "申请事项", "事实与理由", "裁定结果"],
}


# ═══════════════════════════════════════════════════
# 法条匹配预设分类
# ═══════════════════════════════════════════════════
STATUTE_CATEGORIES = [
    "宪法", "民法典", "刑法", "行政法", "诉讼法",
    "公司法", "合同法", "知识产权法", "劳动法", "婚姻家庭法",
    "物权法", "侵权责任法", "商法", "国际法", "其他",
]


# ═══════════════════════════════════════════════════
# 用户系统(预埋 · 未开放)
# ═══════════════════════════════════════════════════
JWT_SECRET = os.environ.get("LEGAL_JWT_SECRET", "")  # 空则首次启动自动生成,写入 data/.jwt_secret
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TTL_SECONDS = 60 * 60 * 24          # 24h
JWT_REFRESH_TTL_SECONDS = 60 * 60 * 24 * 30   # 30d
PASSWORD_BCRYPT_ROUNDS = 12
MAX_LOGIN_ATTEMPTS_PER_HOUR = 10                # 单 IP 单用户名阈值
SINGLE_USER_MODE = os.environ.get("LEGAL_SINGLE_USER", "true").lower() == "true"
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"             # 首次启动自动建,提示用户立即改