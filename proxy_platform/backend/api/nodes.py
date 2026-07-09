from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from typing import List
from backend.database import get_db
from backend.models.models import ProxyNode, NodeStatus, User
from backend.models.schemas import (
    NodeCreate, NodeUpdate, NodeResponse, NodeStatusResponse,
    ProxyUserCreate, ProxyUserResponse
)
from backend.api.auth import get_current_active_user
from backend.services.xray_service import generate_uuid
import random

router = APIRouter(prefix="/nodes", tags=["节点管理"])

@router.get("", response_model=List[NodeResponse])
async def list_nodes(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    query = select(ProxyNode)
    if active_only:
        query = query.filter(ProxyNode.is_active == True)
    result = await db.execute(query.order_by(ProxyNode.created_at.desc()))
    nodes = result.scalars().all()
    
    # 附加状态信息
    responses = []
    for node in nodes:
        status_result = await db.execute(
            select(NodeStatus).filter(NodeStatus.node_id == node.id)
        )
        status = status_result.scalar_one_or_none()
        node_dict = {
            "id": node.id,
            "name": node.name,
            "host": node.host,
            "port": node.port,
            "protocol": node.protocol,
            "uuid": node.uuid,
            "alter_id": node.alter_id,
            "network": node.network,
            "path": node.path,
            "tls": node.tls,
            "speed_limit": node.speed_limit,
            "data_limit": node.data_limit,
            "used_data": node.used_data,
            "is_active": node.is_active,
            "is_free": node.is_free,
            "country": node.country,
            "created_at": node.created_at,
            "uptime": status.uptime if status else 0,
            "cpu": status.cpu if status else 0,
            "memory": status.memory if status else 0,
        }
        responses.append(NodeResponse(**node_dict))
    return responses

@router.post("", response_model=NodeResponse)
async def create_node(
    node: NodeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    db_node = ProxyNode(**node.dict())
    db.add(db_node)
    await db.commit()
    await db.refresh(db_node)
    return db_node

@router.get("/{node_id}", response_model=NodeResponse)
async def get_node(node_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProxyNode).filter(ProxyNode.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node

@router.put("/{node_id}", response_model=NodeResponse)
async def update_node(
    node_id: int,
    node_update: NodeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    result = await db.execute(select(ProxyNode).filter(ProxyNode.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    for key, value in node_update.dict(exclude_unset=True).items():
        setattr(node, key, value)
    await db.commit()
    await db.refresh(node)
    return node

@router.delete("/{node_id}")
async def delete_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    result = await db.execute(select(ProxyNode).filter(ProxyNode.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="节点节点不存在")
    await db.delete(node)
    await db.commit()
    return {"message": "删除成功"}

@router.get("/{node_id}/status", response_model=NodeStatusResponse)
async def get_node_status(node_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(NodeStatus).filter(NodeStatus.node_id == node_id))
    status = result.scalar_one_or_none()
    if not status:
        raise HTTPException(status_code=404, detail="状态不存在")
    return status