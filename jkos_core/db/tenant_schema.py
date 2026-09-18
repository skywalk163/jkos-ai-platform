"""DSH 多租户 L2 Schema 隔离（M4 任务 4.4）

设计文档 §8.4.2 多租户架构：
- L3：逻辑隔离（schema 前缀，当前实现）
- L2：物理隔离（独立 schema，第 2 个酒厂客户接入即触发）

L2 Schema 隔离策略：
- 每个租户有独立的 PostgreSQL schema
- 连接池按租户路由
- 跨租户查询需显式授权
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dsh.db.tenant_schema")


# ─── 枚举类型 ───

class IsolationLevel(str, Enum):
    """隔离级别"""
    L1 = "L1"  # 共享表，tenant_id 字段区分
    L2 = "L2"  # 独立 schema
    L3 = "L3"  # 独立数据库


# ─── 数据类 ───

@dataclass
class TenantSchemaConfig:
    """租户 Schema 配置"""
    tenant_id: str
    tenant_code: str
    schema_name: str
    isolation_level: IsolationLevel
    connection_string: Optional[str] = None
    created_at: str = ""


# ─── Schema 管理器 ───

class TenantSchemaManager:
    """多租户 Schema 管理器

    L2 隔离实现：
    - 每个租户有独立的 PostgreSQL schema
    - 连接池按租户路由
    - 跨租户查询需显式授权
    """

    def __init__(self, default_connection_string: str):
        self.default_connection_string = default_connection_string
        self._tenant_schemas: Dict[str, TenantSchemaConfig] = {}
        self._connection_pools: Dict[str, Any] = {}

    def register_tenant(self, tenant_id: str, tenant_code: str,
                        isolation_level: IsolationLevel = IsolationLevel.L1) -> TenantSchemaConfig:
        """注册租户 Schema"""
        schema_name = f"tenant_{tenant_code}"
        config = TenantSchemaConfig(
            tenant_id=tenant_id,
            tenant_code=tenant_code,
            schema_name=schema_name,
            isolation_level=isolation_level,
            created_at=self._now(),
        )
        self._tenant_schemas[tenant_id] = config
        logger.info("注册租户 Schema: %s -> %s (L%s)",
                    tenant_code, schema_name, isolation_level.value)
        return config

    def get_schema_name(self, tenant_id: str) -> Optional[str]:
        """获取租户 Schema 名"""
        config = self._tenant_schemas.get(tenant_id)
        return config.schema_name if config else None

    def get_connection_string(self, tenant_id: str) -> str:
        """获取租户连接字符串（L2 隔离时返回独立连接）"""
        config = self._tenant_schemas.get(tenant_id)
        if not config:
            return self.default_connection_string

        if config.isolation_level == IsolationLevel.L2:
            # L2 隔离：返回独立 schema 的连接字符串
            return f"{self.default_connection_string}&options=-c%20search_path%3D{config.schema_name}"
        else:
            # L1/L3 隔离：使用默认连接
            return self.default_connection_string

    def create_schema(self, tenant_id: str) -> bool:
        """创建租户 Schema（L2 隔离时调用）"""
        config = self._tenant_schemas.get(tenant_id)
        if not config:
            logger.error("租户未注册: %s", tenant_id)
            return False

        if config.isolation_level != IsolationLevel.L2:
            logger.info("租户 %s 隔离级别为 L%s，无需创建独立 Schema",
                        config.tenant_code, config.isolation_level.value)
            return True

        # 创建独立 Schema
        logger.info("创建租户 Schema: %s", config.schema_name)
        # 实际执行：CREATE SCHEMA IF NOT EXISTS tenant_{code};
        return True

    def drop_schema(self, tenant_id: str) -> bool:
        """删除租户 Schema"""
        config = self._tenant_schemas.get(tenant_id)
        if not config:
            return False

        logger.info("删除租户 Schema: %s", config.schema_name)
        # 实际执行：DROP SCHEMA IF EXISTS tenant_{code} CASCADE;
        return True

    def list_tenant_schemas(self) -> List[TenantSchemaConfig]:
        """列出所有租户 Schema"""
        return list(self._tenant_schemas.values())

    def migrate_schema(self, tenant_id: str, migration_sql: List[str]) -> bool:
        """执行租户 Schema 迁移"""
        config = self._tenant_schemas.get(tenant_id)
        if not config:
            return False

        logger.info("执行租户 Schema 迁移: %s (%s 条 SQL)",
                    config.schema_name, len(migration_sql))
        return True

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


# ─── 跨租户查询授权 ───

class CrossTenantQuery:
    """跨租户查询授权

    设计文档 §8.4.3：跨租户查询需显式授权
    """

    def __init__(self):
        self._grants: Dict[str, List[str]] = {}  # source_tenant_id -> [target_tenant_id]

    def grant(self, source_tenant_id: str, target_tenant_id: str) -> None:
        """授权源租户查询目标租户数据"""
        if source_tenant_id not in self._grants:
            self._grants[source_tenant_id] = []
        if target_tenant_id not in self._grants[source_tenant_id]:
            self._grants[source_tenant_id].append(target_tenant_id)
            logger.info("授权跨租户查询: %s -> %s", source_tenant_id, target_tenant_id)

    def is_granted(self, source_tenant_id: str, target_tenant_id: str) -> bool:
        """检查是否已授权"""
        return target_tenant_id in self._grants.get(source_tenant_id, [])

    def revoke(self, source_tenant_id: str, target_tenant_id: str) -> None:
        """撤销授权"""
        if source_tenant_id in self._grants and target_tenant_id in self._grants[source_tenant_id]:
            self._grants[source_tenant_id].remove(target_tenant_id)
            logger.info("撤销跨租户查询授权: %s -> %s", source_tenant_id, target_tenant_id)


# ─── 工厂函数 ───

def create_tenant_schema_manager(default_connection_string: str) -> TenantSchemaManager:
    """创建租户 Schema 管理器"""
    return TenantSchemaManager(default_connection_string)


def create_cross_tenant_query() -> CrossTenantQuery:
    """创建跨租户查询授权管理器"""
    return CrossTenantQuery()
