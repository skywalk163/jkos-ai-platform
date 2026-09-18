"""极快AI操作系统 - harness sidecar 生命周期（M16）

SidecarManager 负责 harness runtime 子进程的懒启动、状态标记与幂等关闭。

FreeBSD 关键点：官方 `deepseek-harness-runtime-bin` 没有 FreeBSD wheel，
SDK 仅在 dsh_bin 为 None 时才导入 deepseek_harness_runtime
（python/sdk/src/deepseek_harness/client.py:459-469），因此显式传入 dsh_bin
即可完全绕开该依赖、使用 0.82 源码构建的 launcher。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from jkos_core.harness.config import HarnessConfig

logger = logging.getLogger("dsh.harness")


class HarnessUnavailable(RuntimeError):
    """harness runtime 不可用（SDK 未安装 / 启动失败 / 已降级）"""


def sdk_available() -> bool:
    """SDK 是否可导入（集成测试据此 skip，而非 fail）"""
    try:
        import deepseek_harness  # noqa: F401
    except Exception:  # noqa: BLE001 - 任何导入期异常都视为不可用
        return False
    return True


def _default_client_factory(config: HarnessConfig) -> Any:
    """构造官方 SDK 客户端（延迟导入，避免无 SDK 环境下 import 失败）"""
    try:
        from deepseek_harness import DeepSeekHarness
    except Exception as exc:  # noqa: BLE001
        raise HarnessUnavailable(
            "未安装 deepseek-harness-sdk；FreeBSD 上可用 "
            "`pip install --no-deps deepseek-harness-sdk` 并显式配置 "
            "JKOS_HARNESS_DSH_BIN 指向源码构建的 launcher"
        ) from exc

    kwargs: Dict[str, Any] = {
        "dsh_home": config.dsh_home,
        "cwd": config.workspace,
        "provider": config.provider,
        "model": config.model,
        "initialize_timeout_seconds": config.initialize_timeout,
    }
    if config.dsh_bin:
        kwargs["dsh_bin"] = config.dsh_bin
    if config.request_timeout:
        kwargs["request_timeout_seconds"] = config.request_timeout
    # 凭据显式注入子进程；不写盘、不进日志
    if config.api_key:
        kwargs["api_key"] = config.api_key
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return DeepSeekHarness(**kwargs)


class SidecarManager:
    """harness runtime 子进程管理器（单实例、懒启动）

    client_factory 可注入，便于测试在无 SDK 环境下验证生命周期语义。
    """

    def __init__(self, config: HarnessConfig,
                 client_factory: Optional[Callable[[HarnessConfig], Any]] = None):
        self.config = config
        self._client_factory = client_factory or _default_client_factory
        self._client: Optional[Any] = None
        self._lock = threading.Lock()
        self._degraded_reason: Optional[str] = None
        self._start_count = 0

    # ─── 状态 ───

    @property
    def started(self) -> bool:
        """sidecar 是否已启动"""
        return self._client is not None

    @property
    def degraded(self) -> bool:
        """是否处于降级态（上次运行失败）"""
        return self._degraded_reason is not None

    @property
    def start_count(self) -> int:
        return self._start_count

    def health(self) -> Dict[str, object]:
        """供 /health 探活使用"""
        if self.degraded:
            status = "degraded"
        elif self.started:
            status = "ok"
        else:
            status = "idle"
        return {
            "status": status,
            "started": self.started,
            "start_count": self._start_count,
            "degraded_reason": self._degraded_reason,
            # describe() 已脱敏，不含 api_key
            "config": self.config.describe(),
        }

    # ─── 生命周期 ───

    def ensure_started(self) -> Any:
        """懒启动并返回 SDK 客户端；已启动则复用"""
        with self._lock:
            if self._client is not None:
                return self._client
            client = self._client_factory(self.config)
            self._client = client
            self._start_count += 1
            self._degraded_reason = None
            logger.info("harness sidecar 已启动（第 %d 次）：%s",
                        self._start_count, self.config.describe())
            return client

    def mark_degraded(self, reason: str) -> None:
        """标记降级：保留可读原因，供健康检查与运维排障"""
        self._degraded_reason = reason
        logger.error("harness sidecar 降级：%s", reason)

    def mark_recovered(self) -> None:
        self._degraded_reason = None

    def close(self) -> None:
        """幂等关闭：释放子进程并复位状态"""
        with self._lock:
            client = self._client
            self._client = None
            if client is None:
                return
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不应中断上层清理
                logger.warning("harness sidecar 关闭异常：%s", exc)
            else:
                logger.info("harness sidecar 已关闭")