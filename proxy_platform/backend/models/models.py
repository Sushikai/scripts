from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class ProxyNode(Base):
    __tablename__ = "proxy_nodes"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    protocol = Column(String(50), default="vmess")  # vmess, vless, trojan, shadowsocks
    uuid = Column(String(100), nullable=False)
    alter_id = Column(Integer, default=64)
    network = Column(String(20), default="tcp")  # tcp, ws, grpc
    path = Column(String(255), default="/")
    tls = Column(Boolean, default=True)
    cert_file = Column(String(255))
    key_file = Column(String(255))
    speed_limit = Column(Integer, default=0)  # 0 = unlimited, in MB/s
    data_limit = Column(Float, default=0)  # 0 = unlimited, in GB
    used_data = Column(Float, default=0)  # in GB
    is_active = Column(Boolean, default=True)
    is_free = Column(Boolean, default=False)
    country = Column(String(10), default="US")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class ProxyUser(Base):
    __tablename__ = "proxy_users"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    node_id = Column(Integer, nullable=False, index=True)
    protocol = Column(String(50), default="vmess")
    uuid = Column(String(100), nullable=False)
    inlet_port = Column(Integer, nullable=False)  # 本地监听端口
    connection_info = Column(JSON)  # 存储完整的连接配置
    enable = Column(Boolean, default=True)
    flow = Column(String(20), default="")  # for VLESS
    secret_key = Column(String(100))  # for Trojan
    method = Column(String(50))  # for Shadowsocks
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class TrafficLog(Base):
    __tablename__ = "traffic_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    node_id = Column(Integer, index=True)
    upload = Column(Float, default=0)  # bytes
    download = Column(Float, default=0)  # bytes
    session_time = Column(Integer, default=0)  # seconds
    ip_address = Column(String(50))
    user_agent = Column(String(255))
    created_at = Column(DateTime, default=func.now())

class Announce(Base):
    __tablename__ = "announcements"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text)
    is_pinned = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())

class NodeStatus(Base):
    __tablename__ = "node_status"
    
    id = Column(Integer, primary_key=True, index=True)
    node_id = Column(Integer, unique=True, nullable=False, index=True)
    cpu = Column(Float, default=0)
    memory = Column(Float, default=0)
    uptime = Column(Integer, default=0)  # seconds
    online_users = Column(Integer, default=0)
    last_check = Column(DateTime, default=func.now())

class SystemConfig(Base):
    __tablename__ = "system_config"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())