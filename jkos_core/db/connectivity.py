"""DSH 数据层 - 数据库连通管理（资源配置 companion，V3）

对 resource 的 `kind + config_json` 做连接参数校验与连通性探测，
返回可直接落 connection_check 表的可序列化结果。

实现策略（纯标准库，避免强制第三方驱动）：
- sqlite    : 真实建连 + SELECT 1（文件 / 内存）
- postgres / mysql / redis / mongodb : TCP socket 探活（host:port）
- http/https : HEAD/GET 探测（2xx/3xx 视为连通）

设计文档映射：资源配置 / 数据库连通管理、§8.3.4 数据一致性。
"""
from __future__ import annotations

import logging
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger("dsh.db.connectivity")

RESOURCE_KIND_SQLITE = "sqlite"
RESOURCE_KIND_POSTGRES = "postgres"
RESOURCE_KIND_MYSQL = "mysql"
RESOURCE_KIND_REDIS = "redis"
RESOURCE_KIND_MONGODB = "mongodb"
RESOURCE_KIND_HTTP = "http"

RESOURCE_KINDS = (
    RESOURCE_KIND_SQLITE,
    RESOURCE_KIND_POSTGRES,
    RESOURCE_KIND_MYSQL,
    RESOURCE_KIND_REDIS,
    RESOURCE_KIND_MONGODB,
    RESOURCE_KIND_HTTP,
)

# 走 TCP 探活的类型
_SOCKET_KINDS = (
    RESOURCE_KIND_POSTGRES,
    RESOURCE_KIND_MYSQL,
    RESOURCE_KIND_REDIS,
    RESOURCE_KIND_MONGODB,
)

_PORT_RANGE = (1, 65535)
_SENSITIVE_KEYS = {"password", "pwd", "secret", "token", "api_key", "apikey"}


class ConnectorError(ValueError):
    """参数校验或连通性探测失败；message 可直接回显 API"""


def redact_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏连接配置副本（详情/日志回显用，敏感键打码）"""
    out: Dict[str, Any] = {}
    for key, value in (config or {}).items():
        if str(key).lower().replace(" ", "") in _SENSITIVE_KEYS:
            out[key] = "***"
        else:
            out[key] = value
    return out


class DatabaseConnector:
    """对 resource 配置做连通性检测（无状态，可复用单例）"""

    DEFAULT_TIMEOUT = 3.0

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def validate(self, kind: str, config: Dict[str, Any]) -> None:
        """仅参数合法性校验；非法抛 ConnectorError"""
        config = config or {}
        if kind == RESOURCE_KIND_SQLITE:
            if not config.get("path"):
                raise ConnectorError("sqlite 资源缺少 path")
        elif kind in _SOCKET_KINDS:
            host = config.get("host")
            if not host:
                raise ConnectorError(f"{kind} 资源缺少 host")
            port = config.get("port")
            if not isinstance(port, int) or not (_PORT_RANGE[0] <= port <= _PORT_RANGE[1]):
                raise ConnectorError(
                    f"{kind} 资源 port 非法（需 {_PORT_RANGE[0]}-{_PORT_RANGE[1]}）: {port!r}")
        elif kind == RESOURCE_KIND_HTTP:
            url = config.get("url")
            if not url:
                raise ConnectorError("http 资源缺少 url")
            try:
                parsed = urllib.parse.urlsplit(str(url))
            except (TypeError, ValueError) as exc:
                raise ConnectorError(f"http 资源 url 非法: {exc}") from exc
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ConnectorError(f"http 资源 url 不合法: {url!r}")
        else:
            raise ConnectorError(
                f"不支持的资源类型: {kind!r}（可选 {RESOURCE_KINDS}）")

    def check(self, kind: str, config: Dict[str, Any],
              timeout: Optional[float] = None) -> Dict[str, Any]:
        """连通性探测 → {connected, latency_ms, detail}"""
        t = timeout if timeout is not None else self.timeout
        start = time.monotonic()
        error: Optional[str] = None
        connected = True
        try:
            self.validate(kind, config)
            if kind == RESOURCE_KIND_SQLITE:
                self._check_sqlite(config, t)
            elif kind in _SOCKET_KINDS:
                self._check_socket(config, t)
            else:
                self._check_http(config, t)
        except ConnectorError as exc:
            connected = False
            error = str(exc)
        except OSError as exc:  # socket / urllib 底层异常兜底
            connected = False
            error = f"连接异常: {exc}"
        latency_ms = int(round((time.monotonic() - start) * 1000))
        detail: Dict[str, Any] = {
            "kind": kind,
            "connected": connected,
            "params": redact_config(config),
        }
        if error:
            detail["error"] = error
        logger.info("连通性探测 kind=%s connected=%s latency_ms=%d",
                    kind, connected, latency_ms)
        return {"connected": connected, "latency_ms": latency_ms, "detail": detail}

    # ─── 分类型探测 ───

    def _check_sqlite(self, config: Dict[str, Any], timeout: float) -> None:
        path = config["path"]
        try:
            conn = sqlite3.connect(path, timeout=timeout)
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise ConnectorError(f"SQLite 连接失败: {exc}") from exc

    def _check_socket(self, config: Dict[str, Any], timeout: float) -> None:
        host = config["host"]
        port = config["port"]
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except OSError as exc:
            raise ConnectorError(f"无法连接 {host}:{port}: {exc}") from exc

    def _check_http(self, config: Dict[str, Any], timeout: float) -> None:
        url = str(config["url"])
        method = (config.get("method") or "HEAD").upper()
        if method not in ("HEAD", "GET"):
            method = "HEAD"
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", "dsh-connectivity-probe/1.0")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:  # 4xx/5xx 均视为失败
            raise ConnectorError(f"HTTP 状态异常: {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ConnectorError(f"HTTP 探测失败: {exc.reason}") from exc
        if not (200 <= status < 400):
            raise ConnectorError(f"HTTP 状态异常: {status}")