"""DSH 数据层 - SQLite (WAL) + 版本化迁移 + 仓储（M0 任务 0.1）

选型依据（开发计划 ADR-1）：MVP 用 SQLite WAL，零运维；
PostgreSQL 迁移条件：写入 >50/s 或需要多机写。

设计文档映射：§2.6 数据模型（ULID 主键）、§8.3.4 数据一致性。
"""
from dsh_core.db.connection import ConnectionPool, Database, DatabaseConfig, utc_now
from dsh_core.db.repos import (
    APPROVAL_MODES,
    INSTANCE_CANCELLED,
    INSTANCE_COMPLETED,
    INSTANCE_FAILED,
    INSTANCE_PENDING,
    INSTANCE_RUNNING,
    INSTANCE_SUSPENDED,
    INSTANCE_TERMINAL,
    INSTANCE_WAITING_APPROVAL,
    MODE_ALL,
    MODE_ANY,
    MODE_SERIAL,
    RISKS,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    STEP_COMPENSATED,
    STEP_FAILED,
    STEP_PENDING,
    STEP_RUNNING,
    STEP_SKIPPED,
    STEP_SUCCEEDED,
    STEP_WAITING,
    TASK_APPROVED,
    TASK_CANCELLED,
    TASK_PENDING,
    TASK_REJECTED,
    ApprovalTaskRepo,
    AuditRepo,
    LlmUsageRepo,
    TenantRepo,
    WorkflowRepo,
)
from dsh_core.db.ulid import is_ulid, new_ulid
from dsh_core.db.schema import MIGRATIONS

__all__ = [
    "ConnectionPool",
    "Database",
    "DatabaseConfig",
    "utc_now",
    "new_ulid",
    "is_ulid",
    "TenantRepo",
    "WorkflowRepo",
    "AuditRepo",
    "LlmUsageRepo",
    "ApprovalTaskRepo",
    # 审批任务常量（M1 1.4，§8.2）
    "TASK_PENDING",
    "TASK_APPROVED",
    "TASK_REJECTED",
    "TASK_CANCELLED",
    "RISK_LOW",
    "RISK_MEDIUM",
    "RISK_HIGH",
    "RISKS",
    "MODE_SERIAL",
    "MODE_ALL",
    "MODE_ANY",
    "APPROVAL_MODES",
    # 版本化迁移（M1：V1 基础表 + V2 审批任务表）
    "MIGRATIONS",
    # 工作流状态机常量（0.2 引擎使用）
    "INSTANCE_PENDING",
    "INSTANCE_RUNNING",
    "INSTANCE_WAITING_APPROVAL",
    "INSTANCE_SUSPENDED",
    "INSTANCE_COMPLETED",
    "INSTANCE_FAILED",
    "INSTANCE_CANCELLED",
    "INSTANCE_TERMINAL",
    "STEP_PENDING",
    "STEP_RUNNING",
    "STEP_WAITING",
    "STEP_SUCCEEDED",
    "STEP_FAILED",
    "STEP_SKIPPED",
    "STEP_COMPENSATED",
]
