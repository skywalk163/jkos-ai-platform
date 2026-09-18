"""DSH 存储模块 - 统一管理 PostgreSQL、Redis、MinIO"""

from jkos_core.storage.manager import (
    StorageConfig,
    StorageManager,
    init_database,
)

__all__ = ["StorageConfig", "StorageManager", "init_database"]
