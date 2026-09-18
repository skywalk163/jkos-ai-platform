"""DSH 审计 - 审计日志服务

业务侧只调用 log()：审计写入失败记 ERROR 但不阻断主流程
（主流程数据一致性由工作流引擎的事务保证，审计是旁路）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from jkos_core.db import AuditRepo, Database

logger = logging.getLogger("dsh.audit")

ACTOR_HUMAN = "HUMAN"
ACTOR_AGENT = "AGENT"
ACTOR_SYSTEM = "SYSTEM"


class AuditLogger:
    """审计日志门面"""

    def __init__(self, db: Database):
        self.repo = AuditRepo(db)

    def log(
        self,
        *,
        tenant_id: str,
        action: str,
        resource_type: str,
        actor_type: str = ACTOR_SYSTEM,
        actor_id: str = "system",
        resource_id: Optional[str] = None,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Optional[str]:
        """追加一条审计事件，返回事件 ID；失败返回 None（不抛出）"""
        try:
            return self.repo.append(
                tenant_id=tenant_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                before=before,
                after=after,
                reason=reason,
                trace_id=trace_id,
            )
        except Exception:
            logger.error(
                "审计写入失败 action=%s resource=%s/%s",
                action, resource_type, resource_id, exc_info=True,
            )
            return None

    def query(
        self,
        tenant_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self.repo.list_events(
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            limit=limit,
        )
