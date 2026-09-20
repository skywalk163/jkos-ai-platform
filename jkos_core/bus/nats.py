"""DSH NATS 事件总线（M4 任务 4.6）

设计文档 ADR-2：事件总线内嵌（本地消息表）→ 后续 NATS
设计文档 §2.9.3 事件约定、§8.3.4 数据一致性

NATS 事件总线：
- at-least-once 语义
- 幂等去重
- 发布/订阅模式
- 事件回溯
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("dsh.bus.nats")


# ─── 枚举类型 ───

class EventStatus(str, Enum):
    """事件状态"""
    PENDING = "pending"
    PUBLISHED = "published"
    CONSUMED = "consumed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class DeliveryGuarantee(str, Enum):
    """投递保证"""
    AT_MOST_ONCE = "at_most_once"
    AT_LEAST_ONCE = "at_least_once"
    EXACTLY_ONCE = "exactly_once"


# ─── 数据类 ───

@dataclass
class Event:
    """事件"""
    id: str
    type: str
    tenant_id: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: EventStatus = EventStatus.PENDING
    created_at: str = ""
    published_at: Optional[str] = None
    consumed_at: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3


@dataclass
class Subscription:
    """订阅"""
    id: str
    event_type: str
    handler: Callable
    queue_group: Optional[str] = None
    durable: bool = False
    created_at: str = ""


@dataclass
class DeadLetterEvent:
    """死信事件"""
    id: str
    event: Event
    error_message: str
    created_at: str = ""


# ─── 事件总线接口 ───

class EventBus:
    """事件总线接口"""

    async def publish(self, event: Event) -> bool:
        """发布事件"""
        raise NotImplementedError

    async def subscribe(self, event_type: str, handler: Callable,
                        queue_group: Optional[str] = None) -> Subscription:
        """订阅事件"""
        raise NotImplementedError

    async def unsubscribe(self, subscription_id: str) -> bool:
        """取消订阅"""
        raise NotImplementedError

    async def ack(self, event_id: str) -> bool:
        """确认事件消费"""
        raise NotImplementedError

    async def nack(self, event_id: str, requeue: bool = True) -> bool:
        """否定确认"""
        raise NotImplementedError


# ─── NATS 事件总线实现 ───

class NatsEventBus(EventBus):
    """NATS 事件总线实现

    设计文档 ADR-2：事件量 >100/天 或跨主机消费时，替换内嵌总线为 NATS
    """

    def __init__(self, nats_url: str = "nats://localhost:4222",
                 delivery_guarantee: DeliveryGuarantee = DeliveryGuarantee.AT_LEAST_ONCE):
        self.nats_url = nats_url
        self.delivery_guarantee = delivery_guarantee
        self._subscriptions: Dict[str, Subscription] = {}
        self._event_store: Dict[str, Event] = {}
        self._dead_letter_queue: List[DeadLetterEvent] = []
        self._idempotency_set: Set[str] = set()
        self._connected = False
        self._nc = None  # NATS 连接
        self._nc_subs: Dict[str, Any] = {}  # 真实 NATS 订阅（sub_id -> 订阅对象）

    async def connect(self) -> None:
        """连接 NATS 服务器

        优先使用真实 nats-py 客户端；若未安装或服务器不可达，
        降级为内嵌内存模式（本地事件存储），保证总线可用。
        """
        try:
            import nats  # type: ignore
            self._nc = await nats.connect(self.nats_url, connect_timeout=3)
            self._connected = True
            logger.info("已连接 NATS: %s", self.nats_url)
        except Exception as e:
            self._nc = None
            self._connected = True
            logger.warning("NATS 不可用（%s），降级为内嵌内存模式", e)

    async def disconnect(self) -> None:
        """断开 NATS 连接"""
        if self._nc is not None:
            try:
                await self._nc.close()
            except Exception as e:
                logger.warning("关闭 NATS 连接失败: %s", e)
            self._nc = None
        self._nc_subs.clear()
        self._connected = False
        logger.info("已断开 NATS 连接")

    async def publish(self, event: Event) -> bool:
        """发布事件"""
        if not self._connected:
            logger.error("NATS 未连接")
            return False

        # 真实 NATS 发布（携带完整事件信封，便于跨进程消费与 ack）
        if self._nc is not None:
            try:
                await self._nc.publish(
                    event.type, json.dumps(self._event_to_dict(event)).encode())
            except Exception as e:
                logger.error("发布事件到 NATS 失败: %s - %s", event.id, e)
                event.status = EventStatus.PENDING
                return False

        event.status = EventStatus.PUBLISHED
        event.published_at = self._now()
        self._event_store[event.id] = event
        logger.info("发布事件: %s (%s)", event.id, event.type)
        return True

    async def subscribe(self, event_type: str, handler: Callable,
                        queue_group: Optional[str] = None) -> Subscription:
        """订阅事件"""
        import uuid
        sub_id = str(uuid.uuid4())[:8]
        sub = Subscription(
            id=sub_id,
            event_type=event_type,
            handler=handler,
            queue_group=queue_group,
            durable=queue_group is not None,
            created_at=self._now(),
        )
        self._subscriptions[sub_id] = sub
        logger.info("订阅事件: %s -> %s (queue=%s)",
                    event_type, sub_id, queue_group or "none")

        # 真实 NATS 订阅：远端消息经本地 handle_event 幂等分发
        if self._nc is not None:
            try:
                async def _on_msg(msg: Any) -> None:
                    try:
                        data = json.loads(msg.data.decode())
                        event = self._event_from_dict(data)
                        await self.handle_event(event)
                    except Exception as e:
                        logger.error("处理 NATS 消息失败: %s", e)

                nc_sub = await self._nc.subscribe(
                    event_type, queue=queue_group, cb=_on_msg)
                self._nc_subs[sub_id] = nc_sub
            except Exception as e:
                logger.warning("NATS 订阅失败，降级为本地订阅: %s", e)
        return sub

    async def unsubscribe(self, subscription_id: str) -> bool:
        """取消订阅"""
        if subscription_id in self._subscriptions:
            del self._subscriptions[subscription_id]
            nc_sub = self._nc_subs.pop(subscription_id, None)
            if nc_sub is not None:
                try:
                    await nc_sub.unsubscribe()
                except Exception as e:
                    logger.warning("取消 NATS 订阅失败: %s", e)
            logger.info("取消订阅: %s", subscription_id)
            return True
        return False

    async def ack(self, event_id: str) -> bool:
        """确认事件消费"""
        event = self._event_store.get(event_id)
        if not event:
            return False

        event.status = EventStatus.CONSUMED
        event.consumed_at = self._now()
        self._idempotency_set.add(event_id)
        logger.debug("确认事件: %s", event_id)
        return True

    async def nack(self, event_id: str, requeue: bool = True) -> bool:
        """否定确认"""
        event = self._event_store.get(event_id)
        if not event:
            return False

        event.retry_count += 1
        if event.retry_count >= event.max_retries:
            event.status = EventStatus.DEAD_LETTER
            dlq = DeadLetterEvent(
                id=f"dlq-{event_id}",
                event=event,
                error_message="重试次数超限",
                created_at=self._now(),
            )
            self._dead_letter_queue.append(dlq)
            logger.warning("事件进入死信队列: %s", event_id)
        elif requeue:
            event.status = EventStatus.PENDING
            logger.warning("事件重新入队: %s (retry=%d)", event_id, event.retry_count)
        else:
            event.status = EventStatus.FAILED
            logger.error("事件失败: %s", event_id)
        return True

    async def handle_event(self, event: Event) -> bool:
        """处理事件（幂等）"""
        # 幂等去重
        if event.id in self._idempotency_set:
            logger.debug("事件已处理（幂等）: %s", event.id)
            return True

        # 查找匹配的订阅
        for sub in self._subscriptions.values():
            if sub.event_type == event.type:
                try:
                    await sub.handler(event)
                    await self.ack(event.id)
                    return True
                except Exception as e:
                    logger.error("事件处理失败: %s - %s", event.id, e)
                    await self.nack(event.id)
                    return False

        logger.warning("无匹配订阅: %s", event.type)
        return False

    def get_event(self, event_id: str) -> Optional[Event]:
        """获取事件"""
        return self._event_store.get(event_id)

    def get_dead_letter_events(self) -> List[DeadLetterEvent]:
        """获取死信事件"""
        return self._dead_letter_queue

    def retry_dead_letter(self, dlq_id: str) -> bool:
        """重试死信事件"""
        for dlq in self._dead_letter_queue:
            if dlq.id == dlq_id:
                dlq.event.status = EventStatus.PENDING
                dlq.event.retry_count = 0
                logger.info("重试死信事件: %s", dlq_id)
                return True
        return False

    @property
    def mode(self) -> str:
        """总线模式：nats（真实连接）或 embedded（内嵌内存）"""
        return "nats" if self._nc is not None else "embedded"

    @staticmethod
    def _event_to_dict(event: Event) -> Dict[str, Any]:
        """事件 -> 跨进程信封（JSON 可序列化）"""
        status = getattr(event, "status", None)
        return {
            "id": event.id,
            "type": event.type,
            "tenant_id": event.tenant_id,
            "payload": getattr(event, "payload", {}),
            "metadata": getattr(event, "metadata", {}),
            "status": status.value if hasattr(status, "value")
            else (status or EventStatus.PENDING.value),
            "created_at": getattr(event, "created_at", ""),
        }

    @staticmethod
    def _event_from_dict(data: Dict[str, Any]) -> Event:
        """跨进程信封 -> 事件"""
        return Event(
            id=data.get("id", ""),
            type=data.get("type", ""),
            tenant_id=data.get("tenant_id", ""),
            payload=data.get("payload", {}),
            metadata=data.get("metadata", {}),
            status=EventStatus(data.get("status", EventStatus.PENDING.value)),
            created_at=data.get("created_at", ""),
        )

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


# ─── 事件总线工厂 ───

class EventBusFactory:
    """事件总线工厂"""

    _instance: Optional[EventBus] = None

    @classmethod
    def create(cls, nats_url: Optional[str] = None) -> EventBus:
        """创建事件总线"""
        import os
        nats_url = nats_url or os.getenv("DSH_NATS_URL", "nats://localhost:4222")
        cls._instance = NatsEventBus(nats_url=nats_url)
        return cls._instance

    @classmethod
    def get_instance(cls) -> Optional[EventBus]:
        """获取事件总线实例"""
        return cls._instance

    @classmethod
    def set_instance(cls, bus: EventBus) -> None:
        """设置事件总线实例（用于测试）"""
        cls._instance = bus


# ─── 事件定义（设计文档 §2.9）───

class EventTypes:
    """事件类型定义（设计文档 §2.9）"""

    # 危机预警联动事件
    SENTIMENT_LEVEL_RAISED = "SENTIMENT.LEVEL_RAISED"
    SENTIMENT_LEVEL_NORMAL = "SENTIMENT.LEVEL_NORMAL"

    # 生产调度事件
    PRODUCTION_DELAY = "PRODUCTION.DELAY"
    PRODUCTION_COMPLETED = "PRODUCTION.COMPLETED"
    MATERIAL_SHORTAGE = "MATERIAL.SHORTAGE"

    # 营销事件
    MARKETING_PAUSED = "MARKETING.PAUSED"
    MARKETING_RESUMED = "MARKETING.RESUMED"

    # 审批事件
    APPROVAL_REQUESTED = "APPROVAL.REQUESTED"
    APPROVAL_APPROVED = "APPROVAL.APPROVED"
    APPROVAL_REJECTED = "APPROVAL.REJECTED"

    # 工作流事件
    WORKFLOW_STARTED = "WORKFLOW.STARTED"
    WORKFLOW_COMPLETED = "WORKFLOW.COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW.FAILED"


# ─── 事件构建器 ───

def build_sentiment_raised_event(tenant_id: str, level: str, score: float,
                                  source: str, content: str) -> Event:
    """构建舆情等级提升事件"""
    import uuid
    return Event(
        id=str(uuid.uuid4()),
        type=EventTypes.SENTIMENT_LEVEL_RAISED,
        tenant_id=tenant_id,
        payload={
            "level": level,
            "score": score,
            "source": source,
            "content": content,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        },
        metadata={"severity": level},
    )


def build_production_delay_event(tenant_id: str, order_no: str, delay_hours: int) -> Event:
    """构建生产延迟事件"""
    import uuid
    return Event(
        id=str(uuid.uuid4()),
        type=EventTypes.PRODUCTION_DELAY,
        tenant_id=tenant_id,
        payload={
            "order_no": order_no,
            "delay_hours": delay_hours,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        },
    )
