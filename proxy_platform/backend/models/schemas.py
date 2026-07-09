from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
from enum import Enum

# ─── Auth Schemas ─────────────────────────────────────────────
class UserBase(BaseModel):
    username: str
    email: Optional[EmailStr] = None

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None

class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None

# ─── Node Schemas ─────────────────────────────────────────────
class ProtocolType(str, Enum):
    VMESS = "vmess"
    VLESS = "vless"
    TROJAN = "trojan"
    SHADOWSOCKS = "shadowsocks"

class NetworkType(str, Enum):
    TCP = "tcp"
    WS = "ws"
    GRPC = "grpc"

class NodeBase(BaseModel):
    name: str
    host: str
    port: int
    protocol: ProtocolType = ProtocolType.VMESS
    uuid: str
    alter_id: int = 64
    network: NetworkType = NetworkType.TCP
    path: str = "/"
    tls: bool = True
    speed_limit: int = 0
    data_limit: float = 0
    country: str = "US"
    is_free: bool = False

class NodeCreate(NodeBase):
    pass

class NodeUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    protocol: Optional[ProtocolType] = None
    alter_id: Optional[int] = None
    network: Optional[NetworkType] = None
    path: Optional[str] = None
    tls: Optional[bool] = None
    speed_limit: Optional[int] = None
    data_limit: Optional[float] = None
    is_active: Optional[bool] = None
    is_free: Optional[bool] = None
    country: Optional[str] = None

class NodeResponse(NodeBase):
    id: int
    used_data: float
    is_active: bool
    created_at: datetime
    uptime: Optional[int] = 0
    cpu: Optional[float] = 0
    memory: Optional[float] = 0
    
    class Config:
        from_attributes = True

class NodeStatusResponse(BaseModel):
    node_id: int
    cpu: float
    memory: float
    uptime: int
    online_users: int
    last_check: datetime

# ─── Proxy User Schemas ───────────────────────────────────────
class ProxyUserBase(BaseModel):
    protocol: ProtocolType = ProtocolType.VMESS
    node_id: int

class ProxyUserCreate(ProxyUserBase):
    pass

class ProxyUserResponse(ProxyUserBase):
    id: int
    user_id: int
    uuid: str
    inlet_port: int
    connection_info: dict
    enable: bool
    created_at: datetime
    
    class Config:
        from_attributes = True
