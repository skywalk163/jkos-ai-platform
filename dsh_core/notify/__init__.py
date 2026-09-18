"""DSH 通知服务（M1 任务 1.5）——webhook 推送 + 审批提醒

设计（开发计划 1.5）：
  - Notifier 协议 + 两个实现：WebhookNotifier（POST JSON 到 DSH_NOTIFY_WEBHOOK）、
    LoggedNotifier（未配置 webhook 时降级，只打日志，保证通知旁路永不阻塞主流程）；
  - NotifyService 统一入口：send() 捕获一切异常返回 False（通知是旁路）；
  - 审计：通知投递结果由调用方（引擎）已审计业务事件，本模块只记日志不重复审计。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("dsh.notify")

WEBHOOK_TIMEOUT_SECONDS = 3.0   # 通知旁路必须短超时，不拖慢工作流


@dataclass
class NotifyMessage:
    """通知消息（统一信封，webhook payload 即其 JSON 序列化）"""
    event: str                        # 事件名：approval.created / approval.escalated / workflow.completed ...
    title: str
    body: str = ""
    tenant_id: Optional[str] = None
    ref_type: Optional[str] = None    # 关联资源（如 workflow_instance）
    ref_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "event": self.event, "title": self.title, "body": self.body,
            "tenant_id": self.tenant_id, "ref_type": self.ref_type,
            "ref_id": self.ref_id, "meta": self.meta,
        }


class Notifier:
    """通知器协议：send 返回是否投递成功"""

    def send(self, message: NotifyMessage) -> bool:  # pragma: no cover - 协议定义
        raise NotImplementedError


class LoggedNotifier(Notifier):
    """降级通知器：只写日志（未配置 DSH_NOTIFY_WEBHOOK 时的默认实现）"""

    def send(self, message: NotifyMessage) -> bool:
        logger.info("[notify-fallback] %s | %s | %s", message.event, message.title, message.body)
        return False


class WebhookNotifier(Notifier):
    """Webhook 通知器：POST JSON 到配置的群机器人/回调地址"""

    def __init__(self, url: str, timeout: float = WEBHOOK_TIMEOUT_SECONDS):
        self.url = url
        self.timeout = timeout

    def send(self, message: NotifyMessage) -> bool:
        data = json.dumps(message.to_payload(), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=data, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "User-Agent": "dsh-notify/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ok = 200 <= resp.status < 300
                if not ok:
                    logger.warning("webhook 非成功状态 status=%s event=%s", resp.status, message.event)
                return ok
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("webhook 发送失败 event=%s: %s", message.event, exc)
            return False


class NotifyService:
    """通知统一入口（引擎经 comps.notify 注入使用）"""

    def __init__(self, notifier: Optional[Notifier] = None):
        self.notifier = notifier or self._default_notifier()

    @staticmethod
    def _default_notifier() -> Notifier:
        url = os.environ.get("DSH_NOTIFY_WEBHOOK", "").strip()
        return WebhookNotifier(url) if url else LoggedNotifier()

    def send(
        self,
        event: str,
        *,
        title: str,
        body: str = "",
        tenant_id: Optional[str] = None,
        ref_type: Optional[str] = None,
        ref_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """发送通知；任何异常吞掉并返回 False（通知永不阻塞业务主流程）"""
        message = NotifyMessage(
            event=event, title=title, body=body, tenant_id=tenant_id,
            ref_type=ref_type, ref_id=ref_id, meta=meta or {},
        )
        try:
            return self.notifier.send(message)
        except Exception as exc:  # noqa: BLE001 - 旁路容错
            logger.warning("通知发送异常 event=%s: %s", event, exc)
            return False
