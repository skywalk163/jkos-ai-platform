"""DSH 数据模型（jkos_core.models）覆盖测试（M19 覆盖率缺口回收）

模块此前 0% 覆盖。本文件覆盖 3 个枚举 + 5 个 dataclass：
- 枚举：ResourceCategory / ResourceStatus / AgentType（str-Enum 值、构造、比较）
- 数据类：Session / Message / MultimodalResource / AgentRegistration / AuditLog
  （默认值、default_factory 可变性、字段覆盖、asdict 序列化）
"""
from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from datetime import datetime

from jkos_core.models import (
    AgentRegistration,
    AgentType,
    AuditLog,
    Message,
    MultimodalResource,
    ResourceCategory,
    ResourceStatus,
    Session,
)


# ─── M19 枚举 ───

def test_models_resource_category_enum():
    """ResourceCategory：str-Enum 值与字符串互转"""
    assert ResourceCategory.TEXT == "text"
    assert ResourceCategory.AUDIO == "audio"
    assert ResourceCategory.IMAGE == "image"
    assert ResourceCategory.VIDEO == "video"
    assert ResourceCategory("image") is ResourceCategory.IMAGE
    assert str(ResourceCategory.VIDEO) == "ResourceCategory.VIDEO"


def test_models_resource_status_enum():
    """ResourceStatus 全状态枚举"""
    assert ResourceStatus.UPLOADING.value == "uploading"
    assert ResourceStatus.PROCESSING.value == "processing"
    assert ResourceStatus.PROCESSED.value == "processed"
    assert ResourceStatus.FAILED.value == "failed"
    assert ResourceStatus("failed") is ResourceStatus.FAILED


def test_models_agent_type_enum():
    """AgentType：mcp / a2a"""
    assert AgentType.MCP == "mcp"
    assert AgentType.A2A == "a2a"
    assert AgentType("a2a") is AgentType.A2A


# ─── M19 数据类 ───

def test_models_session_dataclass():
    """Session：默认值、时间戳、元数据可变性"""
    assert is_dataclass(Session)
    assert {f.name for f in fields(Session)} == {
        "id", "user_id", "tenant_id", "title", "metadata", "created_at", "updated_at",
    }
    s = Session(id="s1")
    assert s.user_id is None
    assert s.metadata == {}
    assert isinstance(s.created_at, datetime)
    assert isinstance(s.updated_at, datetime)
    s.metadata["k"] = "v"
    assert s.metadata["k"] == "v"
    full = Session(id="s2", user_id="u", tenant_id="t", title="标题",
                   metadata={"a": 1}, created_at=datetime(2026, 1, 1),
                   updated_at=datetime(2026, 1, 1))
    d = asdict(full)
    assert d["title"] == "标题" and d["metadata"] == {"a": 1}


def test_models_message_dataclass():
    """Message：角色字段与默认元数据"""
    assert is_dataclass(Message)
    m = Message(id="m1", session_id="s1", role="user", content="你好")
    assert m.role == "user" and m.content == "你好"
    assert m.metadata == {} and isinstance(m.created_at, datetime)
    m.role = "assistant"
    assert m.role == "assistant"
    m.metadata["x"] = 1
    assert m.metadata["x"] == 1


def test_models_multimodal_resource_dataclass():
    """MultimodalResource：分类/状态默认值、路径与大小"""
    assert is_dataclass(MultimodalResource)
    r = MultimodalResource(id="r1", category=ResourceCategory.IMAGE,
                           file_name="a.png")
    assert r.storage_path == ""
    assert r.status is ResourceStatus.PROCESSED
    assert isinstance(r.created_at, datetime)
    r2 = MultimodalResource(id="r2", category=ResourceCategory.AUDIO,
                            file_name="b.wav", tenant_id="t", user_id="u",
                            file_size=1024, mime_type="audio/wav",
                            storage_path="s3://b", status=ResourceStatus.UPLOADING)
    assert r2.file_size == 1024
    assert r2.mime_type == "audio/wav"
    assert r2.status is ResourceStatus.UPLOADING


def test_models_agent_registration_dataclass():
    """AgentRegistration：能力清单与认证配置默认可变"""
    assert is_dataclass(AgentRegistration)
    a = AgentRegistration(id="a1", name="代码审查", agent_type=AgentType.MCP,
                          endpoint="mcp://localhost:9999")
    assert a.capabilities == [] and a.auth_config == {}
    assert isinstance(a.registered_at, datetime)
    a.capabilities.append("code_review")
    a.auth_config["type"] = "api_key"
    assert a.capabilities == ["code_review"]
    assert a.auth_config["type"] == "api_key"


def test_models_audit_log_dataclass():
    """AuditLog：动作与时间戳"""
    assert is_dataclass(AuditLog)
    ts = datetime(2026, 9, 20, 12, 0, 0)
    log = AuditLog(id="l1", action="approve", timestamp=ts, user_id="u",
                   tenant_id="t", resource_type="order", resource_id="o1",
                   request_id="req-1", metadata={"ok": True})
    assert log.action == "approve"
    assert log.timestamp == ts
    assert log.resource_id == "o1"
    assert asdict(log)["metadata"] == {"ok": True}
    log.metadata = {}
    assert log.metadata == {}