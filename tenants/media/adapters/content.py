"""自媒体中台 - 内容发布适配器（M2 任务 2.1）

模拟内容发布到各平台（微信公众号、微博、抖音等）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class ContentPublisher:
    """内容发布适配器（MVP 阶段模拟，M3 接入真实 API）"""

    PLATFORMS = ["wechat", "weibo", "douyin", "xiaohongshu"]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.published: List[Dict[str, Any]] = []

    def publish(self, platform: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """发布内容到指定平台

        Args:
            platform: 平台名称（wechat/weibo/douyin/xiaohongshu）
            content: 内容正文
            metadata: 附加元数据（标题、标签、封面图等）

        Returns:
            发布结果（含 post_id、url、status）
        """
        if platform not in self.PLATFORMS:
            raise ValueError(f"不支持的平台: {platform}，可选: {self.PLATFORMS}")

        post_id = f"post_{len(self.published) + 1}_{platform}"
        result = {
            "post_id": post_id,
            "platform": platform,
            "url": f"https://{platform}.com/{post_id}",
            "status": "published",
            "content_length": len(content),
            "metadata": metadata or {},
        }
        self.published.append(result)
        return result

    def publish_to_all(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """发布内容到所有平台"""
        results = []
        for platform in self.PLATFORMS:
            results.append(self.publish(platform, content, metadata))
        return results

    def get_published(self, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取已发布内容列表"""
        if platform:
            return [p for p in self.published if p["platform"] == platform]
        return self.published


class SentimentMonitor:
    """舆情监控适配器（M2 任务 2.2）

    模拟监控各平台评论/提及，M3 接入真实舆情 API。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.monitored_keywords: List[str] = []
        self.collected_mentions: List[Dict[str, Any]] = []

    def track_keyword(self, keyword: str) -> None:
        """添加监控关键词"""
        if keyword not in self.monitored_keywords:
            self.monitored_keywords.append(keyword)

    def collect_mentions(self, platform: str, count: int = 10) -> List[Dict[str, Any]]:
        """收集指定平台的提及/评论（模拟数据）"""
        import random
        sentiments = ["positive", "neutral", "negative"]
        mentions = []
        for i in range(count):
            mention = {
                "id": f"mention_{len(self.collected_mentions) + i + 1}",
                "platform": platform,
                "text": f"模拟评论内容 {i + 1} 关于 {self.monitored_keywords[0] if self.monitored_keywords else '关键词'}",
                "sentiment": random.choice(sentiments),
                "author": f"user_{random.randint(1000, 9999)}",
                "timestamp": "2026-09-14T10:00:00Z",
                "likes": random.randint(0, 1000),
            }
            mentions.append(mention)
        self.collected_mentions.extend(mentions)
        return mentions

    def get_mentions(self, platform: Optional[str] = None, sentiment: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取已收集的提及"""
        result = self.collected_mentions
        if platform:
            result = [m for m in result if m["platform"] == platform]
        if sentiment:
            result = [m for m in result if m["sentiment"] == sentiment]
        return result
