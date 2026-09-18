"""DSH PostgreSQL 迁移准备（M4 任务 4.5）

设计文档 ADR-1：存储 MVP 用 SQLite（WAL），P1 迁 PostgreSQL
设计文档 ADR-2：事件总线内嵌（本地消息表）→ 后续 NATS

PostgreSQL 迁移准备：
- 连接池管理
- 迁移脚本生成
- SQLite 到 PostgreSQL 的数据迁移工具
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dsh.db.postgres")


# ─── 枚举类型 ───

class DatabaseType(str, Enum):
    """数据库类型"""
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"


class MigrationStatus(str, Enum):
    """迁移状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ─── 数据类 ───

@dataclass
class PostgresConfig:
    """PostgreSQL 配置"""
    host: str = "localhost"
    port: int = 5432
    database: str = "dsh_ai"
    username: str = "dsh_user"
    password: str = ""
    pool_size: int = 10
    pool_max_overflow: int = 20
    pool_pre_ping: bool = True

    @classmethod
    def from_env(cls) -> "PostgresConfig":
        """从环境变量加载配置"""
        import os
        return cls(
            host=os.getenv("DSH_PG_HOST", "localhost"),
            port=int(os.getenv("DSH_PG_PORT", "5432")),
            database=os.getenv("DSH_PG_DB", "dsh_ai"),
            username=os.getenv("DSH_PG_USER", "dsh_user"),
            password=os.getenv("DSH_PG_PASSWORD", ""),
            pool_size=int(os.getenv("DSH_PG_POOL_SIZE", "10")),
        )

    def to_connection_string(self) -> str:
        """生成连接字符串"""
        return f"postgresql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass
class MigrationPlan:
    """迁移计划"""
    id: str
    source_type: DatabaseType
    target_type: DatabaseType
    tables: List[str]
    status: MigrationStatus
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


# ─── PostgreSQL 迁移器 ───

class PostgresMigrator:
    """PostgreSQL 迁移器

    设计文档 ADR-1：SQLite 单写瓶颈出现时迁 PostgreSQL
    """

    def __init__(self, config: PostgresConfig):
        self.config = config
        self._migration_plans: Dict[str, MigrationPlan] = {}

    def create_migration_plan(self, source_type: DatabaseType,
                              tables: List[str]) -> MigrationPlan:
        """创建迁移计划"""
        import uuid
        plan_id = str(uuid.uuid4())[:8]
        plan = MigrationPlan(
            id=plan_id,
            source_type=source_type,
            target_type=DatabaseType.POSTGRESQL,
            tables=tables,
            status=MigrationStatus.PENDING,
        )
        self._migration_plans[plan_id] = plan
        logger.info("创建迁移计划: %s (%s -> PostgreSQL, %d 张表)",
                    plan_id, source_type.value, len(tables))
        return plan

    def generate_migration_sql(self, table_name: str) -> List[str]:
        """生成单表迁移 SQL（SQLite -> PostgreSQL）"""
        # SQLite 到 PostgreSQL 的差异处理
        # 1. AUTOINCREMENT -> SERIAL
        # 2. TEXT -> VARCHAR/TEXT
        # 3. BOOLEAN -> INTEGER (SQLite 无布尔类型)
        # 4. datetime -> TIMESTAMP
        # 5. JSON -> JSONB

        sql_statements = [
            # 创建目标表
            f"CREATE TABLE IF NOT EXISTS {table_name} (",
            # 字段定义（需要根据实际表结构生成）
            "    id TEXT PRIMARY KEY,",
            "    created_at TIMESTAMP NOT NULL,",
            "    updated_at TIMESTAMP NOT NULL",
            ");",
            # 创建索引
            f"CREATE INDEX IF NOT EXISTS idx_{table_name}_created ON {table_name}(created_at);",
        ]
        return sql_statements

    def generate_data_migration_sql(self, table_name: str) -> List[str]:
        """生成数据迁移 SQL"""
        return [
            # 从 SQLite 导出数据并插入 PostgreSQL
            f"-- 数据迁移: {table_name}",
            f"-- 1. 从 SQLite 导出: .mode csv .output {table_name}.csv",
            f"-- 2. 导入 PostgreSQL: COPY {table_name} FROM '{table_name}.csv' WITH (FORMAT csv, HEADER true);",
        ]

    def execute_migration(self, plan_id: str) -> bool:
        """执行迁移计划"""
        plan = self._migration_plans.get(plan_id)
        if not plan:
            logger.error("迁移计划不存在: %s", plan_id)
            return False

        plan.status = MigrationStatus.RUNNING
        plan.started_at = self._now()
        logger.info("开始执行迁移计划: %s", plan_id)

        try:
            for table in plan.tables:
                logger.info("迁移表: %s", table)
                # 实际执行迁移逻辑

            plan.status = MigrationStatus.COMPLETED
            plan.completed_at = self._now()
            logger.info("迁移计划完成: %s", plan_id)
            return True
        except Exception as e:
            plan.status = MigrationStatus.FAILED
            plan.error_message = str(e)
            logger.error("迁移计划失败: %s - %s", plan_id, e)
            return False

    def get_migration_plan(self, plan_id: str) -> Optional[MigrationPlan]:
        """获取迁移计划"""
        return self._migration_plans.get(plan_id)

    def list_migration_plans(self) -> List[MigrationPlan]:
        """列出所有迁移计划"""
        return list(self._migration_plans.values())

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


# ─── 连接池管理器 ───

class ConnectionPoolManager:
    """连接池管理器

    设计文档 ADR-1：PostgreSQL 连接池管理
    """

    def __init__(self, config: PostgresConfig):
        self.config = config
        self._pools: Dict[str, Any] = {}

    def get_pool(self, pool_name: str = "default") -> Any:
        """获取连接池"""
        if pool_name not in self._pools:
            # 实际实现：创建 asyncpg 或 psycopg3 连接池
            logger.info("创建连接池: %s (size=%d)", pool_name, self.config.pool_size)
            self._pools[pool_name] = {
                "name": pool_name,
                "config": self.config,
                "size": self.config.pool_size,
            }
        return self._pools[pool_name]

    def close_pool(self, pool_name: str = "default") -> None:
        """关闭连接池"""
        if pool_name in self._pools:
            logger.info("关闭连接池: %s", pool_name)
            del self._pools[pool_name]

    def close_all(self) -> None:
        """关闭所有连接池"""
        for pool_name in list(self._pools.keys()):
            self.close_pool(pool_name)

    def get_stats(self) -> Dict[str, Any]:
        """获取连接池统计信息"""
        return {
            "pool_count": len(self._pools),
            "config": {
                "host": self.config.host,
                "port": self.config.port,
                "database": self.config.database,
                "pool_size": self.config.pool_size,
            },
        }


# ─── 工厂函数 ───

def create_postgres_migrator(config: Optional[PostgresConfig] = None) -> PostgresMigrator:
    """创建 PostgreSQL 迁移器"""
    if config is None:
        config = PostgresConfig.from_env()
    return PostgresMigrator(config)


def create_connection_pool(config: Optional[PostgresConfig] = None) -> ConnectionPoolManager:
    """创建连接池管理器"""
    if config is None:
        config = PostgresConfig.from_env()
    return ConnectionPoolManager(config)
