"""DSH 多租户 L2 Schema 隔离（M4 任务 4.4 / M18.2 数据层落地）

设计文档 §8.4.2 多租户架构：
- L1：共享表，tenant_id 字段区分（逻辑隔离）
- L2：物理隔离（独立 Schema，独立载体）——M18.2 数据层落地
- L3：独立数据库 / 独立文件（隔离最强）

M18.2 数据层落地（SQLite 真实隔离 + PostgreSQL 渲染就绪）：
- SQLite 模式：L2 租户 = 独立 .db 文件（真实物理隔离），
  create_schema / drop_schema / migrate_schema 执行真实 SQL，
  依赖 jkos_core.db.connection.Database + schema.MIGRATIONS 幂等迁移；
- PostgreSQL 模式（M19 就绪）：渲染可再生的 DDL（render_pg_schema_sql），
  不建立真实连接（测试契约：假连接串全程零连接）；
- 连接路由：按租户返回独立 Database 实例（get_tenant_database）；
- 跨租户查询需显式授权（CrossTenantQuery，设计文档 §8.4.3）；
- 生命周期收敛：close / close_all / unregister_tenant（防止长驻进程连接泄漏）；
- 存储路径：data_dir 参数优先，其次环境变量 DSH_TENANT_DATA_DIR。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from jkos_core.db.connection import Database, DatabaseConfig
from jkos_core.db.schema import MIGRATIONS

logger = logging.getLogger("dsh.db.tenant_schema")

# 租户码白名单：小写字母/数字开头，仅含小写字母、数字、下划线、连字符（防路径穿越）
_TENANT_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


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


# ─── 运行时隔离配置（M18.2 接线）───

@dataclass
class TenantIsolationConfig:
    """L2 物理隔离的运行时开关（默认关闭，保证既有行为不变）

    环境变量：
      - JKOS_TENANT_L2_ENABLED：是否启用 L2 物理隔离（默认 false）；
      - DSH_TENANT_DATA_DIR：租户独立 .db 存放目录（默认 <主库目录>/tenants）；
      - JKOS_TENANT_L2_CODES：逗号分隔的租户码白名单；为空时取租户表中
        isolation_level='L2' 的行（种子租户均为 L3，故默认零动作）。
    """
    enabled: bool = False
    data_dir: str = ""
    codes: List[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "TenantIsolationConfig":
        raw_codes = os.getenv("JKOS_TENANT_L2_CODES", "")
        codes = [c.strip() for c in raw_codes.split(",") if c.strip()]
        return cls(
            enabled=os.getenv("JKOS_TENANT_L2_ENABLED", "false").lower() in ("1", "true", "yes"),
            data_dir=os.getenv("DSH_TENANT_DATA_DIR", ""),
            codes=codes,
        )

    def describe(self) -> str:
        """配置摘要（日志用，不含敏感信息）"""
        state = "启用" if self.enabled else "关闭"
        return f"L2 隔离{state}（data_dir={self.data_dir or '默认'}, codes={self.codes or '按租户表'})"


# ─── Schema 管理器 ───

class TenantSchemaManager:
    """多租户 Schema 管理器

    M18.2 数据层：
    - SQLite 模式（连接串以 sqlite:// 或裸路径开头）：
      L2 租户 = 独立 .db 文件，create/migrate/drop 执行真实 SQL；
    - PostgreSQL 模式（连接串以 postgresql:// 开头，M19 就绪）：
      仅渲染可再生 DDL，不建立真实连接。
    """

    def __init__(self, default_connection_string: str,
                 *, data_dir: Optional[str] = None):
        self.default_connection_string = default_connection_string
        self._tenant_schemas: Dict[str, TenantSchemaConfig] = {}
        self._dbs: Dict[str, Database] = {}  # tenant_id -> 独立 Database（SQLite 模式）
        self._data_dir = data_dir
        self._mode = self._detect_mode(default_connection_string)

    # ── 模式判定 ──

    def _detect_mode(self, connection_string: str) -> str:
        """按连接串判定载体：sqlite（真实落地）| postgresql（渲染就绪）"""
        cs = connection_string.strip().lower()
        if cs.startswith("postgresql://") or cs.startswith("postgres://"):
            return "postgresql"
        return "sqlite"

    def _open_database(self, path: str) -> Database:
        """打开/创建独立租户数据库（先建目录，再连接）"""
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        db = Database(DatabaseConfig(path=path))
        db.connect()
        return db

    def _tenant_db_path(self, tenant_id: str) -> Optional[str]:
        """SQLite L2 模式下返回租户独立 db 文件路径；否则 None"""
        if self._mode != "sqlite":
            return None
        config = self._tenant_schemas.get(tenant_id)
        if not config or config.isolation_level != IsolationLevel.L2:
            return None
        base = self._data_dir or os.getenv("DSH_TENANT_DATA_DIR") \
            or os.path.join(os.getcwd(), "data", "tenants")
        return os.path.join(base, f"{config.schema_name}.db")

    # ── 注册与路由 ──

    def register_tenant(self, tenant_id: str, tenant_code: str,
                        isolation_level: IsolationLevel = IsolationLevel.L1) -> TenantSchemaConfig:
        """注册租户 Schema（tenant_code 校验为安全文件名片段）"""
        if not _TENANT_CODE_RE.match(tenant_code or ""):
            raise ValueError(
                f"非法租户码: {tenant_code!r}（须为 1-32 位小写字母/数字/下划线/连字符）"
            )
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

    def get_config_by_code(self, tenant_code: str) -> Optional[TenantSchemaConfig]:
        """按租户码查配置（M18.2 运行时路由：租户码 -> tenant_id）"""
        for config in self._tenant_schemas.values():
            if config.tenant_code == tenant_code:
                return config
        return None

    def get_connection_string(self, tenant_id: str) -> str:
        """获取租户连接字符串（L2 隔离时返回独立连接）"""
        config = self._tenant_schemas.get(tenant_id)
        if not config:
            return self.default_connection_string

        if config.isolation_level == IsolationLevel.L2:
            if self._mode == "sqlite":
                # L2 隔离（SQLite）：返回独立 db 文件的连接字符串
                path = self._tenant_db_path(tenant_id)
                return f"sqlite:///{path}" if path else self.default_connection_string
            # L2 隔离（PostgreSQL，M4.4 契约）：返回独立 schema 的连接字符串
            return f"{self.default_connection_string}&options=-c%20search_path%3D{config.schema_name}"
        else:
            # L1/L3 隔离：使用默认连接
            return self.default_connection_string

    # ── 生命周期：创建 / 迁移 / 删除 ──

    def create_schema(self, tenant_id: str) -> bool:
        """创建租户 Schema

        - SQLite + L2：独立 .db 文件 + 幂等 MIGRATIONS 迁移（真实落地）；
        - PostgreSQL：渲染可再生 DDL，不建立真实连接（M4.4 契约：返回 True）。
        """
        config = self._tenant_schemas.get(tenant_id)
        if not config:
            logger.error("租户未注册: %s", tenant_id)
            return False

        if config.isolation_level != IsolationLevel.L2:
            logger.info("租户 %s 隔离级别为 L%s，无需创建独立 Schema",
                        config.tenant_code, config.isolation_level.value)
            return True

        if self._mode == "sqlite":
            db = self._dbs.get(tenant_id)
            if db is None:
                path = self._tenant_db_path(tenant_id)
                if path is None:  # pragma: no cover - 前置分支已保证 sqlite + L2
                    raise RuntimeError(f"租户 {tenant_id} 无独立数据库路径（非 SQLite 或非 L2）")
                db = self._open_database(path)
                try:
                    db.migrate()  # 应用 MIGRATIONS（schema_version 幂等，种子可用）
                except Exception:
                    # 迁移失败：立即关闭连接，避免连接泄漏（不进入 _dbs）
                    db.close()
                    logger.exception("租户 %s 迁移失败，已回滚连接: %s",
                                     config.schema_name, path)
                    raise
                self._dbs[tenant_id] = db
                logger.info("创建租户 Schema（独立 db 文件）: %s -> %s",
                            config.schema_name, path)
            return True

        # PostgreSQL 模式（M19 就绪）：生成可再生的 DDL，此次不连库
        statements = self.render_pg_schema_sql(tenant_id)
        logger.info("[PG 就绪] 创建租户 Schema（%d 条 DDL，未连接）: %s",
                    len(statements), config.schema_name)
        return True

    def drop_schema(self, tenant_id: str) -> bool:
        """删除租户 Schema

        - SQLite + L2：关闭连接并删除独立 db 文件（含 -wal/-shm）；
        - 非 L2：无独立物理载体，直接返回 True；
        - PostgreSQL：日志记录，不建立真实连接。
        """
        config = self._tenant_schemas.get(tenant_id)
        if not config:
            return False

        if config.isolation_level != IsolationLevel.L2:
            logger.info("租户 %s 隔离级别为 L%s，无独立物理文件可删",
                        config.tenant_code, config.isolation_level.value)
            return True

        if self._mode == "sqlite":
            self.close(tenant_id)
            path = self._tenant_db_path(tenant_id)
            if path is not None:
                for suffix in ("", "-wal", "-shm"):
                    candidate = path + suffix
                    try:
                        if os.path.exists(candidate):
                            os.remove(candidate)
                    except OSError as exc:  # pragma: no cover
                        logger.warning("删除租户 db 文件失败: %s (%s)", candidate, exc)
            logger.info("删除租户 Schema（物理文件）: %s", config.schema_name)
            return True

        logger.info("[PG 就绪] 删除租户 Schema（未连接）: %s", config.schema_name)
        return True

    def migrate_schema(self, tenant_id: str,
                       migration_sql: Optional[List[str]] = None) -> bool:
        """执行租户 Schema 迁移

        - migration_sql 为空（None）时：SQLite L2 租户应用 MIGRATIONS 全量迁移；
        - 提供迁移 SQL 时：事务内逐条执行，失败自动回滚；
        - PostgreSQL：日志记录迁移脚本，不建立真实连接（M4.4 契约：返回 True）。
        """
        config = self._tenant_schemas.get(tenant_id)
        if not config:
            return False

        if self._mode == "sqlite":
            if config.isolation_level != IsolationLevel.L2:
                logger.info("租户 %s 隔离级别为 L%s，跳过独立迁移",
                            config.tenant_code, config.isolation_level.value)
                return True
            db = self._dbs.get(tenant_id)
            if db is None:
                self.create_schema(tenant_id)  # 已注册的 L2 租户必然建库成功（失败会抛异常）
                db = self._dbs.get(tenant_id)
                if db is None:  # pragma: no cover - create_schema 成功即入 _dbs
                    raise RuntimeError(f"租户 {tenant_id} 独立数据库未建立")
            if migration_sql is None:
                db.migrate()
                logger.info("执行租户 Schema 迁移（MIGRATIONS，%d 版本）: %s",
                            len(MIGRATIONS), config.schema_name)
            else:
                with db.transaction() as conn:
                    for stmt in migration_sql:
                        conn.execute(stmt.strip())
                logger.info("执行租户 Schema 迁移（%d 条自定义 SQL）: %s",
                            len(migration_sql), config.schema_name)
            return True

        logger.info("[PG 就绪] 执行租户 Schema 迁移（未连接）: %s（%d 条 SQL）",
                    config.schema_name, len(migration_sql or []))
        return True

    # ── 连接生命周期 ──

    def close(self, tenant_id: str) -> bool:
        """关闭单个租户的独立连接（幂等）；未建立连接返回 False"""
        db = self._dbs.pop(tenant_id, None)
        if db is None:
            return False
        try:
            db.close()
        except Exception as exc:  # pragma: no cover - close 本身极少失败
            logger.warning("关闭租户连接失败: %s (%s)", tenant_id, exc)
        return True

    def close_all(self) -> int:
        """关闭全部租户独立连接，返回关闭数量（进程退出/测试清理用）"""
        closed = 0
        for tenant_id in list(self._dbs):
            if self.close(tenant_id):
                closed += 1
        if closed:
            logger.info("已关闭 %d 个租户独立连接", closed)
        return closed

    def unregister_tenant(self, tenant_id: str) -> bool:
        """注销租户（关闭独立连接并移出注册表）；未注册返回 False"""
        if tenant_id not in self._tenant_schemas:
            return False
        self.close(tenant_id)
        config = self._tenant_schemas.pop(tenant_id)
        logger.info("注销租户 Schema: %s", config.schema_name)
        return True

    # ── 查询与渲染 ──

    def get_tenant_database(self, tenant_id: str) -> Optional[Database]:
        """获取租户独立 Database（SQLite L2 模式）；未创建返回 None"""
        return self._dbs.get(tenant_id)

    def render_pg_schema_sql(self, tenant_id: str) -> List[str]:
        """渲染 PostgreSQL 可再生 DDL（M19 就绪，不建立连接）

        - CREATE SCHEMA IF NOT EXISTS tenant_{code};
        - SET search_path 后拼接 MIGRATIONS 全量 DDL（含 V2 approval_task）；
        - 每条归一化为单行并以分号收尾，可直接再生执行。
        """
        config = self._tenant_schemas.get(tenant_id)
        if not config:
            raise KeyError(f"租户未注册: {tenant_id}")
        statements: List[str] = [
            f"CREATE SCHEMA IF NOT EXISTS {config.schema_name};",
            f"SET search_path TO {config.schema_name};",
        ]
        for _version, _description, ddl in MIGRATIONS:
            for stmt in ddl:
                normalized = " ".join(stmt.split()).rstrip()
                if not normalized.endswith(";"):
                    normalized += ";"
                statements.append(normalized)
        return statements

    def list_tenant_schemas(self) -> List[TenantSchemaConfig]:
        """列出所有租户 Schema"""
        return list(self._tenant_schemas.values())

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

def create_tenant_schema_manager(default_connection_string: str, *,
                                 data_dir: Optional[str] = None) -> TenantSchemaManager:
    """创建租户 Schema 管理器（data_dir 缺省时回落 DSH_TENANT_DATA_DIR）"""
    return TenantSchemaManager(default_connection_string, data_dir=data_dir)


def create_cross_tenant_query() -> CrossTenantQuery:
    """创建跨租户查询授权管理器"""
    return CrossTenantQuery()
