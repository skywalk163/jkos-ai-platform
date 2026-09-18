"""DSH 审计 - append-only 审计日志（M0 任务 0.4，设计文档 §8.2.4）

审计事件由数据库触发器强制不可 UPDATE/DELETE（见 jkos_core/db/schema.py）。
覆盖三类主体：HUMAN（人工干预）/ AGENT（智能体动作）/ SYSTEM（系统自动）。
"""
from jkos_core.audit.logger import ACTOR_AGENT, ACTOR_HUMAN, ACTOR_SYSTEM, AuditLogger

__all__ = ["AuditLogger", "ACTOR_HUMAN", "ACTOR_AGENT", "ACTOR_SYSTEM"]
