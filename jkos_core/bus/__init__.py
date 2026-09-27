"""DSH 事件总线（M2-M4）

设计文档 ADR-2：事件总线内嵌（本地消息表）→ 后续 NATS

模块：
- nats.py：NATS 事件总线实现（M4 任务 4.6）
"""
from __future__ import annotations

from jkos_core.bus.nats import (
    Event,
    EventBus,
    EventBusFactory,
    EventTypes,
    NatsEventBus,
    build_production_delay_event,
    build_sentiment_raised_event,
)

__all__ = [
    "Event",
    "EventBus",
    "EventBusFactory",
    "EventTypes",
    "NatsEventBus",
    "build_production_delay_event",
    "build_sentiment_raised_event",
]
