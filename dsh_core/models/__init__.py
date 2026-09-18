"""DSH 数据模型"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ResourceCategory(str, Enum):
    """多模态资源分类"""
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"


class ResourceStatus(str, Enum):
    """资源状态"""
    UPLOADING = "uploading"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class AgentType(str, Enum):
    """Agent 类型"""
    MCP = "mcp"
    A2A = "a2a"


@dataclass
class Session:
    """对话会话"""
    id: str
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    title: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Message:
    """消息"""
    id: str
    session_id: str
    role: str  # user/assistant/system
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class MultimodalResource:
    """多模态资源"""
    id: str
    category: ResourceCategory
    file_name: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    storage_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: ResourceStatus = ResourceStatus.PROCESSED
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class AgentRegistration:
    """Agent 注册信息"""
    id: str
    name: str
    agent_type: AgentType
    endpoint: str
    capabilities: List[str] = field(default_factory=list)
    auth_config: Dict[str, Any] = field(default_factory=dict)
    registered_at: datetime = field(default_factory=datetime.now)


@dataclass
class AuditLog:
    """审计日志"""
    id: str
    action: str
    timestamp: datetime = field(default_factory=datetime.now)
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
