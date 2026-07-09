from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import random
from backend.database import get_db
from backend.models.models import ProxyNode, ProxyUser, User
from backend.models.schemas import ProxyUserCreate, ProxyUserResponse, NodeResponse
from backend.api.auth import get_current_active_user
from backend.services.xray_service import generate_uuid, generate_vmess_config, generate_vless_config, generate_trojan_config, generate_shadowsocks_config
from backend.config import settings
import json

router = APIRouter(prefix="/users", tags=["用户代理"])

def get_available_port() -> int:
    """获取可用端口"""
    return random.randint(settings.DEFAULT_PORT_RANGE_START, settings.DEFAULT_PORT_RANGE_END)

@router.get("/proxies", response_model=List[ProxyUserResponse])
async def list_my_proxies(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    result = await db.execute(
        select(ProxyUser).filter(ProxyUser.user_id == current_user.id)
    )
    return result.scalars().all()

@router.post("/proxies", response_model=ProxyUserResponse)
async def create_proxy(
    proxy_data: ProxyUserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    # 验证节点存在且可用
    node_result = await db.execute(
        select(ProxyNode).filter(ProxyNode.id == proxy_data.node_id, ProxyNode.is_active == True)
    )
    node = node_result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="节点不可用")
    
    # 检查配额
    if node.data_limit > 0 and node.used_data >= node.data_limit:
        raise HTTPException(status_code=403, detail="节点流量已用尽")
    
    # 生成代理
    uuid_str = generate_uuid()
    inlet_port = get_available_port()
    
    # 根据协议生成连接信息
    proxy_user_data = {
        "user_id": current_user.id,
        "node_id": proxy_data.node_id,
        "protocol": proxy_data.protocol,
        "uuid": uuid_str,
        "inlet_port": inlet_port,
        "enable": True,
    }
    
    if proxy_data.protocol == "vless":
        proxy_user_data["flow"] = "xtls-rprx-vision"
    elif proxy_data.protocol == "trojan":
        proxy_user_data["secret_key"] = uuid_str[:32]
    elif proxy_data.protocol == "shadowsocks":
        proxy_user_data["method"] = "aes-256-gcm"
    
    db_proxy = ProxyUser(**proxy_user_data)
    db.add(db_proxy)
    await db.commit()
    await db.refresh(db_proxy)
    return db_proxy

@router.delete("/proxies/{proxy_id}")
async def delete_proxy(
    proxy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    result = await db.execute(
        select(ProxyUser).filter(ProxyUser.id == proxy_id, ProxyUser.user_id == current_user.id)
    )
    proxy = result.scalar_one_or_none()
    if not proxy:
        raise HTTPException(status_code=404, detail="代理不存在")
    await db.delete(proxy)
    await db.commit()
    return {"message": "删除成功"}

@router.get("/proxies/{proxy_id}/info")
async def get_proxy_info(
    proxy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    result = await db.execute(
        select(ProxyUser).filter(ProxyUser.id == proxy_id, ProxyUser.user_id == current_user.id)
    )
    proxy = result.scalar_one_or_none()
    if not proxy:
        raise HTTPException(status_code=404, detail="代理不存在")
    
    node_result = await db.execute(select(ProxyNode).filter(ProxyNode.id == proxy.node_id))
    node = node_result.scalar_one_or_none()
    
    # 生成分享链接
    if proxy.protocol == "vmess":
        config = generate_vmess_config(proxy, node)
        link = f"vmess://{json.dumps(config, ensure_ascii=False)}"
    elif proxy.protocol == "vless":
        config = generate_vless_config(proxy, node)
        link = f"vless://{proxy.uuid}@{node.host}:{node.port}?flow={proxy.flow}&encryption=none&fp=chrome&security=tls&type={node.network}&path={node.path}#{node.name}"
    elif proxy.protocol == "trojan":
        link = f"trojan://{proxy.secret_key or proxy.uuid}@{node.host}:{node.port}?security=tls&fp=chrome&type={node.network}&path={node.path}#{node.name}"
    elif proxy.protocol == "shadowsocks":
        link = f"ss://{proxy.method}@{node.host}:{node.port}?password={proxy.uuid}#{node.name}"
    else:
        link = ""
    
    return {
        "id": proxy.id,
        "protocol": proxy.protocol,
        "node_name": node.name if node else "",
        "inlet_port": proxy.inlet_port,
        "share_link": link,
        "connection_info": {
            "host": node.host if node else "",
            "port": node.port if node else "",
            "uuid": proxy.uuid,
            "alter_id": node.alter_id if node else 64,
            "network": node.network if node else "tcp",
            "path": node.path if node else "/",
            "tls": node.tls if node else True,
        }
    }

@router.get("/subscribe")
async def get_subscription(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """获取订阅内容，返回 base64 编码的配置文件"""
    import base64
    result = await db.execute(
        select(ProxyUser).filter(ProxyUser.user_id == current_user.id, ProxyUser.enable == True)
    )
    proxies = result.scalars().all()
    
    lines = []
    for proxy in proxies:
        node_result = await db.execute(select(ProxyNode).filter(ProxyNode.id == proxy.node_id))
        node = node_result.scalar_one_or_none()
        if not node:
            continue
        
        if proxy.protocol == "vmess":
            config = generate_vmess_config(proxy, node)
            lines.append(f"vmess://{base64.b64encode(json.dumps(config, ensure_ascii=False).encode()).decode()}")
        elif proxy.protocol == "vless":
            link = f"vless://{proxy.uuid}@{node.host}:{node.port}?flow={proxy.flow}&encryption=none&fp=chrome&security=tls&type={node.network}&path={node.path}#{node.name}"
            lines.append(link)
        elif proxy.protocol == "trojan":
            link = f"trojan://{proxy.secret_key or proxy.uuid}@{node.host}:{node.port}?security=tls&fp=chrome&type={node.network}&path={node.path}#{node.name}"
            lines.append(link)
        elif proxy.protocol == "shadowsocks":
            link = f"ss://{proxy.method}@{node.host}:{node.port}?password={proxy.uuid}#{node.name}"
            lines.append(link)
    
    content = "\n".join(lines)
    return {
        "content": base64.b64encode(content.encode()).decode(),
        "raw": content
    }