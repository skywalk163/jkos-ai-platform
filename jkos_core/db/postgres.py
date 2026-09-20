"""DSH PostgreSQL 迁移准备（M4 任务 4.5，M19 真实化）

设计文档 ADR-1：存储 MVP 用 SQLite（WAL），P1 迁 PostgreSQL
设计文档 ADR-2：事件总线内嵌（本地消息表）→ 后续 NATS

PostgreSQL 迁移准备：
- 连接池管理（真实 asyncpg 连接池懒加载）
- 迁移脚本生成（真实 SQLite -> PostgreSQL DDL/数据转换）
- SQLite 到 PostgreSQL 的数据迁移工具
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

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


# ─── SQLite -> PostgreSQL 类型映射 ───

SQLITE_TO_PG_TYPE_MAP: Dict[str, str] = {
    "INTEGER": "INTEGER",
    "INT": "INTEGER",
    "BIGINT": "BIGINT",
    "TINYINT": "SMALLINT",
    "SMALLINT": "SMALLINT",
    "REAL": "REAL",
    "DOUBLE": "DOUBLE PRECISION",
    "DOUBLE PRECISION": "DOUBLE PRECISION",
    "FLOAT": "DOUBLE PRECISION",
    "NUMERIC": "NUMERIC",
    "DECIMAL": "DECIMAL",
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
    "TEXT": "TEXT",
    "CLOB": "TEXT",
    "VARCHAR": "VARCHAR",
    "NVARCHAR": "VARCHAR",
    "CHAR": "CHAR",
    "NCHAR": "CHAR",
    "DATETIME": "TIMESTAMP",
    "TIMESTAMP": "TIMESTAMP",
    "DATE": "DATE",
    "TIME": "TIME",
    "JSON": "JSONB",
    "JSONB": "JSONB",
    "BLOB": "BYTEA",
    "BINARY": "BYTEA",
    "VARBINARY": "BYTEA",
}


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

    M19 真实化：
    - generate_migration_sql：真实 SQLite -> PostgreSQL DDL 转换
      （显式 DDL 解析 / sqlite_master 内省 / 通用骨架兜底）
    - generate_data_migration_sql：从 SQLite 源库导出真实数据为 INSERT
    - execute_migration：生成并保存迁移产物（同步契约，不需目标库在线）
    - execute_migration_async：在真实 PostgreSQL 目标库执行迁移
    """

    def __init__(self, config: PostgresConfig,
                 source_sqlite_path: Optional[str] = None):
        self.config = config
        self.source_sqlite_path = source_sqlite_path
        self._migration_plans: Dict[str, MigrationPlan] = {}
        self._artifacts: Dict[str, Dict[str, Dict[str, List[str]]]] = {}

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

    def generate_migration_sql(self, table_name: str,
                               sqlite_ddl: Optional[str] = None) -> List[str]:
        """生成单表迁移 SQL（SQLite -> PostgreSQL）

        优先顺序：
        1. 显式传入 sqlite_ddl（字符串解析）
        2. source_sqlite_path 指向的 SQLite 库（sqlite_master 内省）
        3. 通用骨架兜底
        """
        indexes: List[str] = []
        triggers: List[str] = []
        if sqlite_ddl is None and self.source_sqlite_path and \
                os.path.exists(self.source_sqlite_path):
            sqlite_ddl, indexes, triggers = self._introspect_table(table_name)

        if sqlite_ddl:
            columns = self._parse_create_table_ddl(sqlite_ddl)
            if columns:
                statements = [f"CREATE TABLE IF NOT EXISTS {table_name} ("]
                statements.extend(f"    {column}," for column in columns[:-1])
                statements.append(f"    {columns[-1]}")
                statements.append(");")
                statements.extend(indexes)
                statements.extend(triggers)
                return statements

        # 通用骨架兜底（无真实 DDL 可用时）
        return [
            f"CREATE TABLE IF NOT EXISTS {table_name} (",
            "    id TEXT PRIMARY KEY,",
            "    created_at TIMESTAMP NOT NULL,",
            "    updated_at TIMESTAMP NOT NULL",
            ");",
            f"CREATE INDEX IF NOT EXISTS idx_{table_name}_created ON {table_name}(created_at);",
        ]

    def _introspect_table(self, table_name: str) -> Tuple[Optional[str], List[str], List[str]]:
        """从 SQLite 源库内省表结构（DDL + 索引 + 触发器）"""
        conn = sqlite3.connect(self.source_sqlite_path)
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            ddl = row[0] if row else None

            index_rows = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name=? AND sql IS NOT NULL",
                (table_name,),
            ).fetchall()
            indexes: List[str] = []
            for (sql,) in index_rows:
                if "sqlite_autoindex" in sql:
                    continue  # 主键/唯一约束自动索引由建表语句覆盖
                indexes.append(
                    sql.replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS")
                       .replace("`", '"'))

            trigger_rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name=?",
                (table_name,),
            ).fetchall()
            triggers: List[str] = []
            for name, sql in trigger_rows:
                if sql and "RAISE" in sql.upper():
                    triggers.append(
                        f"-- 触发器 {name} 使用 SQLite RAISE，需在 PostgreSQL 中手工改写")
                    triggers.append(f"-- {sql}")
                elif sql:
                    triggers.append(sql.replace("`", '"'))
            return ddl, indexes, triggers
        finally:
            conn.close()

    def _parse_create_table_ddl(self, ddl: str) -> List[str]:
        """解析 SQLite CREATE TABLE 语句，返回 PostgreSQL 列定义列表"""
        start = ddl.find("(")
        end = ddl.rfind(")")
        if start < 0 or end <= start:
            return []
        chunks = self._split_ddl_columns(ddl[start + 1:end])
        columns: List[str] = []
        for chunk in chunks:
            upper = chunk.upper()
            if upper.startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK")):
                if upper.startswith("PRIMARY KEY") and "(" not in chunk:
                    continue  # 无列引用的裸 PRIMARY KEY 由列级约束覆盖
                columns.append(re.sub(r"\s+", " ", chunk))
                continue
            rendered = self._render_column(chunk)
            if rendered:
                columns.append(rendered)
        return columns

    @staticmethod
    def _split_ddl_columns(body: str) -> List[str]:
        """在顶层逗号处切分列定义（感知括号与引号）"""
        chunks: List[str] = []
        buffer = ""
        depth = 0
        quote: Optional[str] = None
        for ch in body:
            if quote:
                buffer += ch
                if ch == quote:
                    quote = None
                continue
            if ch in "'\"`":
                quote = ch
                buffer += ch
            elif ch == "(":
                depth += 1
                buffer += ch
            elif ch == ")":
                depth -= 1
                buffer += ch
            elif ch == "," and depth == 0:
                chunks.append(buffer.strip())
                buffer = ""
            else:
                buffer += ch
        if buffer.strip():
            chunks.append(buffer.strip())
        return [chunk for chunk in chunks if chunk]

    def _render_column(self, chunk: str) -> str:
        """渲染单列定义（SQLite 方言 -> PostgreSQL）"""
        match = re.match(r'^("[^"]+"|`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)\s+(.+)$',
                         chunk, re.S)
        if not match:
            return ""
        column_name = match.group(1).strip('"`')
        rest = match.group(2).strip()
        type_match = re.match(r"^([A-Za-z_]+)\s*(\([^)]*\))?", rest)
        base_type = type_match.group(1) if type_match else "TEXT"
        type_args = type_match.group(2) if type_match and type_match.group(2) else ""
        flags = rest[type_match.end():].strip() if type_match else rest

        pg_type = self._map_column_type(base_type)
        if type_args:
            pg_type = f"{pg_type}{type_args}"

        upper = flags.upper()
        autoincrement = "AUTOINCREMENT" in upper
        primary_key = "PRIMARY KEY" in upper
        not_null = "NOT NULL" in upper
        unique = "UNIQUE" in upper

        default_value: Optional[str] = None
        default_match = re.search(r"DEFAULT\s+(.+)$", flags, re.S)
        if default_match:
            default_value = default_match.group(1).strip().rstrip(",").strip()

        if autoincrement and primary_key and base_type.upper() == "INTEGER":
            # SQLite AUTOINCREMENT -> PostgreSQL identity 列
            parts = [f'"{column_name}" SERIAL', "PRIMARY KEY"]
        else:
            parts = [f'"{column_name}" {pg_type}']
            if not_null:
                parts.append("NOT NULL")
            if default_value is not None:
                parts.append(f"DEFAULT {self._convert_default(default_value)}")
            if unique and not primary_key:
                parts.append("UNIQUE")
            if primary_key:
                parts.append("PRIMARY KEY")
        return " ".join(parts)

    def _map_column_type(self, col_type: str) -> str:
        """SQLite 列类型 -> PostgreSQL 列类型（未知类型兜底 TEXT，如 ULID）"""
        key = col_type.strip().upper()
        return SQLITE_TO_PG_TYPE_MAP.get(key, "TEXT")

    @staticmethod
    def _convert_default(default_value: str) -> str:
        """转换 SQLite DEFAULT 表达式为 PostgreSQL 兼容表达式"""
        upper = default_value.upper()
        if "STRFTIME" in upper or upper.startswith("DATETIME("):
            return "CURRENT_TIMESTAMP"
        return default_value.rstrip(";").strip()

    def generate_data_migration_sql(self, table_name: str) -> List[str]:
        """生成数据迁移 SQL

        配置了 source_sqlite_path 时，从 SQLite 源库读取真实数据，
        生成批量 INSERT 语句；否则返回 CSV/COPY 迁移指引注释。
        """
        if not (self.source_sqlite_path and os.path.exists(self.source_sqlite_path)):
            return [
                f"-- 数据迁移: {table_name}",
                f"-- 1. 从 SQLite 导出: .mode csv .output {table_name}.csv",
                f"-- 2. 导入 PostgreSQL: COPY {table_name} FROM '{table_name}.csv' "
                "WITH (FORMAT csv, HEADER true);",
            ]
        conn = sqlite3.connect(self.source_sqlite_path)
        try:
            columns = [row[1] for row in conn.execute(
                f"PRAGMA table_info({table_name})").fetchall()]
            if not columns:
                return [f"-- 表 {table_name} 不存在或无列"]
            rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
            if not rows:
                return [f"-- 表 {table_name} 无数据"]
            column_list = ", ".join(f'"{column}"' for column in columns)
            statements: List[str] = []
            for row in rows:
                values = ", ".join(self._sql_literal(value) for value in row)
                statements.append(
                    f"INSERT INTO {table_name} ({column_list}) VALUES ({values});")
            return statements
        finally:
            conn.close()

    @staticmethod
    def _sql_literal(value: Any) -> str:
        """Python 值 -> PostgreSQL SQL 字面量"""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, (dict, list)):
            return "'" + json.dumps(value).replace("'", "''") + "'::jsonb"
        if isinstance(value, bytes):
            return "E'\\x" + value.hex() + "'"
        if isinstance(value, (datetime, date, time)):
            return "'" + value.isoformat() + "'"
        return "'" + str(value).replace("'", "''") + "'"

    def execute_migration(self, plan_id: str) -> bool:
        """执行迁移计划（生成迁移产物，不要求目标库在线）"""
        plan = self._migration_plans.get(plan_id)
        if not plan:
            logger.error("迁移计划不存在: %s", plan_id)
            return False

        plan.status = MigrationStatus.RUNNING
        plan.started_at = self._now()
        logger.info("开始执行迁移计划: %s", plan_id)

        try:
            artifacts: Dict[str, Dict[str, List[str]]] = {}
            for table in plan.tables:
                ddl_statements = self.generate_migration_sql(table)
                data_statements = self.generate_data_migration_sql(table)
                artifacts[table] = {"ddl": ddl_statements, "data": data_statements}
                logger.info("迁移表: %s (DDL %d 条, 数据 %d 条)",
                            table, len(ddl_statements), len(data_statements))
            self._artifacts[plan_id] = artifacts
            plan.status = MigrationStatus.COMPLETED
            plan.completed_at = self._now()
            logger.info("迁移计划完成: %s", plan_id)
            return True
        except Exception as e:
            plan.status = MigrationStatus.FAILED
            plan.error_message = str(e)
            logger.error("迁移计划失败: %s - %s", plan_id, e)
            return False

    def write_migration_artifacts(self, plan_id: str, output_dir: str) -> List[str]:
        """将迁移产物（DDL + 数据 SQL）写入文件，返回文件路径列表"""
        artifacts = self._artifacts.get(plan_id)
        if not artifacts:
            return []
        os.makedirs(output_dir, exist_ok=True)
        paths: List[str] = []
        for table, parts in artifacts.items():
            path = os.path.join(output_dir, f"migration_{plan_id}_{table}.sql")
            lines = [
                f"-- 迁移计划 {plan_id} -> 表 {table}",
                "-- 由 jkos_core.db.postgres.PostgresMigrator 生成",
                "-- BEGIN SCHEMA",
            ]
            lines.extend(parts["ddl"])
            lines.append("-- BEGIN DATA")
            lines.extend(parts["data"])
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            paths.append(path)
            logger.info("写入迁移产物: %s", path)
        return paths

    async def execute_migration_async(self, plan_id: str) -> bool:
        """在真实 PostgreSQL 目标库上执行迁移计划

        需要目标库可达；连接或执行失败时计划置为 FAILED 并返回 False。
        """
        plan = self._migration_plans.get(plan_id)
        if not plan:
            logger.error("迁移计划不存在: %s", plan_id)
            return False

        plan.status = MigrationStatus.RUNNING
        plan.started_at = self._now()
        logger.info("开始真实执行迁移计划: %s", plan_id)

        pool_manager = ConnectionPoolManager(self.config)
        try:
            pool = await pool_manager.get_async_pool()
            async with pool.acquire() as conn:
                for table in plan.tables:
                    for statement in self.generate_migration_sql(table):
                        await conn.execute(statement)
                    for statement in self.generate_data_migration_sql(table):
                        if statement.lstrip().startswith("--"):
                            continue
                        await conn.execute(statement)
            plan.status = MigrationStatus.COMPLETED
            plan.completed_at = self._now()
            logger.info("迁移计划完成（真实执行）: %s", plan_id)
            return True
        except Exception as e:
            plan.status = MigrationStatus.FAILED
            plan.error_message = str(e)
            logger.error("迁移计划失败（真实执行）: %s - %s", plan_id, e)
            return False
        finally:
            await pool_manager.close_all_async()

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
    - get_pool()：返回池元信息（同步契约，兼容既有客户端）
    - get_async_pool()：懒加载真实 asyncpg 连接池（需目标库可达）
    """

    def __init__(self, config: PostgresConfig):
        self.config = config
        self._pools: Dict[str, Any] = {}
        self._async_pools: Dict[str, Any] = {}

    def get_pool(self, pool_name: str = "default") -> Any:
        """获取连接池元信息（同步契约）"""
        if pool_name not in self._pools:
            logger.info("创建连接池: %s (size=%d)", pool_name, self.config.pool_size)
            self._pools[pool_name] = {
                "name": pool_name,
                "config": self.config,
                "size": self.config.pool_size,
            }
        return self._pools[pool_name]

    async def get_async_pool(self, pool_name: str = "default") -> Any:
        """获取（或创建）真实 asyncpg 连接池

        懒加载：首次调用时建立到 PostgreSQL 的真实连接池；
        目标库不可达或 asyncpg 未安装时抛出 ConnectionError。
        """
        if pool_name in self._async_pools:
            return self._async_pools[pool_name]
        try:
            import asyncpg
        except ImportError:
            raise ConnectionError(
                "asyncpg 未安装，无法建立 PostgreSQL 连接池: pip install asyncpg") from None
        logger.info("创建真实连接池: %s (%s:%d/%s)",
                    pool_name, self.config.host, self.config.port, self.config.database)
        try:
            pool = await asyncpg.create_pool(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.username,
                password=self.config.password,
                min_size=1,
                max_size=self.config.pool_size + self.config.pool_max_overflow,
                timeout=3,
            )
        except Exception as e:
            raise ConnectionError(
                f"无法连接 PostgreSQL ({self.config.host}:{self.config.port}/"
                f"{self.config.database}): {e}") from e
        self._async_pools[pool_name] = pool
        return pool

    async def close_async_pool(self, pool_name: str = "default") -> None:
        """关闭真实连接池"""
        pool = self._async_pools.pop(pool_name, None)
        if pool is not None:
            try:
                await pool.close()
            except Exception as e:
                logger.warning("关闭连接池失败: %s - %s", pool_name, e)

    async def close_all_async(self) -> None:
        """关闭所有真实连接池"""
        for pool_name in list(self._async_pools.keys()):
            await self.close_async_pool(pool_name)

    def close_pool(self, pool_name: str = "default") -> None:
        """关闭连接池（元信息）"""
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
            "async_pool_count": len(self._async_pools),
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
