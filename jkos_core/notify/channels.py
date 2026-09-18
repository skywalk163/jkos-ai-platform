"""DSH 通知渠道 - 多渠道通知发送（M3 任务 3.2）

支持邮件、企业微信、钉钉等通知渠道。
"""
from __future__ import annotations

import abc
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("dsh.notify")


# ─── 通知渠道基类 ───

class NotificationChannel(abc.ABC):
    """通知渠道基类"""

    @abc.abstractproperty
    def name(self) -> str:
        """渠道名称"""
        pass

    @abc.abstractmethod
    async def send(self, title: str, content: str, recipients: List[str], **kwargs: Any) -> Dict[str, Any]:
        """发送通知

        Args:
            title: 通知标题
            content: 通知内容
            recipients: 接收者列表
            **kwargs: 额外参数

        Returns:
            发送结果
        """
        pass

    def healthy(self) -> bool:
        """检查渠道是否健康"""
        return True


# ─── 邮件通知渠道 ───

class EmailChannel(NotificationChannel):
    """邮件通知渠道

    环境变量：
      - DSH_SMTP_HOST: SMTP 主机
      - DSH_SMTP_PORT: SMTP 端口（默认 587）
      - DSH_SMTP_USER: SMTP 用户名
      - DSH_SMTP_PASSWORD: SMTP 密码
      - DSH_SMTP_FROM: 发件人地址
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._host = os.getenv("DSH_SMTP_HOST", "")
        self._port = int(os.getenv("DSH_SMTP_PORT", "587"))
        self._user = os.getenv("DSH_SMTP_USER", "")
        self._password = os.getenv("DSH_SMTP_PASSWORD", "")
        self._from = os.getenv("DSH_SMTP_FROM", "noreply@dsh.ai")

    @property
    def name(self) -> str:
        return "email"

    def healthy(self) -> bool:
        return bool(self._host and self._user and self._password)

    async def send(self, title: str, content: str, recipients: List[str], **kwargs: Any) -> Dict[str, Any]:
        """发送邮件通知"""
        if not self.healthy():
            # 模拟模式
            logger.info("邮件渠道未配置，使用模拟模式")
            return {
                "channel": self.name,
                "status": "simulated",
                "title": title,
                "recipients": recipients,
                "content": content[:100] + "..." if len(content) > 100 else content,
            }

        # 真实邮件发送（MVP 阶段不实现）
        logger.warning("真实邮件发送未实现，使用模拟模式")
        return {
            "channel": self.name,
            "status": "simulated",
            "title": title,
            "recipients": recipients,
        }


# ─── 企业微信通知渠道 ───

class WeComChannel(NotificationChannel):
    """企业微信通知渠道

    环境变量：
      - DSH_WECOM_WEBHOOK: 企业微信机器人 Webhook URL
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._webhook = os.getenv("DSH_WECOM_WEBHOOK", "")

    @property
    def name(self) -> str:
        return "wecom"

    def healthy(self) -> bool:
        return bool(self._webhook)

    async def send(self, title: str, content: str, recipients: List[str], **kwargs: Any) -> Dict[str, Any]:
        """发送企业微信通知"""
        if not self.healthy():
            # 模拟模式
            logger.info("企业微信渠道未配置，使用模拟模式")
            return {
                "channel": self.name,
                "status": "simulated",
                "title": title,
                "content": content[:100] + "..." if len(content) > 100 else content,
            }

        # 真实企业微信发送
        try:
            payload = {
                "msgtype": "text",
                "text": {
                    "content": f"{title}\n\n{content}",
                },
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._webhook,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                return {
                    "channel": self.name,
                    "status": "sent",
                    "response": response.json(),
                }
        except Exception as e:
            logger.error("企业微信发送失败: %s", e)
            return {
                "channel": self.name,
                "status": "failed",
                "error": str(e),
            }


# ─── 钉钉通知渠道 ───

class DingTalkChannel(NotificationChannel):
    """钉钉通知渠道

    环境变量：
      - DSH_DINGTALK_WEBHOOK: 钉钉机器人 Webhook URL
      - DSH_DINGTALK_SECRET: 钉钉机器人密钥（用于签名）
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._webhook = os.getenv("DSH_DINGTALK_WEBHOOK", "")
        self._secret = os.getenv("DSH_DINGTALK_SECRET", "")

    @property
    def name(self) -> str:
        return "dingtalk"

    def healthy(self) -> bool:
        return bool(self._webhook)

    async def send(self, title: str, content: str, recipients: List[str], **kwargs: Any) -> Dict[str, Any]:
        """发送钉钉通知"""
        if not self.healthy():
            # 模拟模式
            logger.info("钉钉渠道未配置，使用模拟模式")
            return {
                "channel": self.name,
                "status": "simulated",
                "title": title,
                "content": content[:100] + "..." if len(content) > 100 else content,
            }

        # 真实钉钉发送
        try:
            payload = {
                "msgtype": "text",
                "text": {
                    "content": f"{title}\n\n{content}",
                },
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._webhook,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                return {
                    "channel": self.name,
                    "status": "sent",
                    "response": response.json(),
                }
        except Exception as e:
            logger.error("钉钉发送失败: %s", e)
            return {
                "channel": self.name,
                "status": "failed",
                "error": str(e),
            }


# ─── 通知管理器 ───

class NotificationManager:
    """通知管理器：管理多个通知渠道"""

    def __init__(self, channels: Optional[List[NotificationChannel]] = None):
        self.channels: List[NotificationChannel] = channels or []

    def register(self, channel: NotificationChannel) -> None:
        """注册通知渠道"""
        self.channels.append(channel)

    async def send(self, title: str, content: str, recipients: List[str], channels: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """发送通知到多个渠道

        Args:
            title: 通知标题
            content: 通知内容
            recipients: 接收者列表
            channels: 指定渠道列表（None 表示所有渠道）

        Returns:
            各渠道发送结果列表
        """
        results = []
        target_channels = self.channels if channels is None else [c for c in self.channels if c.name in channels]

        for channel in target_channels:
            try:
                result = await channel.send(title, content, recipients)
                results.append(result)
            except Exception as e:
                logger.error("渠道 %s 发送失败: %s", channel.name, e)
                results.append({
                    "channel": channel.name,
                    "status": "failed",
                    "error": str(e),
                })

        return results

    def describe(self) -> List[Dict[str, Any]]:
        """获取渠道状态"""
        return [
            {"name": c.name, "healthy": c.healthy()}
            for c in self.channels
        ]
