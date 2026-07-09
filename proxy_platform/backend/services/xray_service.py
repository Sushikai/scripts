import uuid
import json
import hashlib
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.context import CryptContext
from backend.models.models import ProxyNode, ProxyUser, TrafficLog, User, NodeStatus
import random

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def generate_uuid() -> str:
    return str(uuid.uuid4())

def generate_vmess_config(user: ProxyUser, node: ProxyNode) -> Dict[str, Any]:
    """生成 VMess 配置文件"""
    config = {
        "add": node.host,
        "port": node.port,
        "aid": str(user.alter_id if hasattr(user, 'alter_id') else node.alter_id),
        "net": node.network,
        "path": node.path,
        "ps": f"{node.name} - {user.user_id}",
        "tls": "tls" if node.tls else "",
        "v": "2",
        "type": "none",
        "host": "",
        "peer": "",
        "mux": 1,
    }
    # 生成 ID (取UUID的md5前16字节hex)
    config["id"] = hashlib.md5(user.uuid.encode()).hexdigest()[:32]
    return config

def generate_vless_config(user: ProxyUser, node: ProxyNode) -> Dict[str, Any]:
    """生成 VLESS 配置文件"""
    return {
        "v": "2",
        "ps": f"{node.name} - {user.user_id}",
        "add": node.host,
        "port": node.port,
        "id": user.uuid,
        "flow": user.flow or "xtls-rprx-vision",
        "net": node.network,
        "path": node.path,
        "type": "none",
        "host": "",
        "peer": "",
        "tls": "tls",
    }

def generate_trojan_config(user: ProxyUser, node: ProxyNode) -> Dict[str, Any]:
    """生成 Trojan 配置文件"""
    return {
        "run_type": "client",
        "local_addr": "127.0.0.1",
        "local_port": user.inlet_port,
        "remote_addr": node.host,
        "remote_port": node.port,
        "password": user.secret_key or user.uuid,
        "ssl": {
            "verify": node.tls,
            "cert": node.cert_file,
            "key": node.key_file,
        }
    }

def generate_shadowsocks_config(user: ProxyUser, node: ProxyNode) -> Dict[str, Any]:
    """生成 Shadowsocks 配置"""
    return {
        "server": node.host,
        "server_port": node.port,
        "password": user.uuid,
        "method": user.method or "aes-256-gcm",
        "local_address": "127.0.0.1",
        "local_port": user.inlet_port,
        "timeout": 300,
    }

def get_subscription_config(user_id: int, db: AsyncSession) -> str:
    """生成分发订阅内容 (base64)"""
    # 返回空，后续实现
    return ""

def calculate_expiry_time(days: int = 30) -> datetime:
    from datetime import timedelta
    return datetime.utcnow() + timedelta(days=days)