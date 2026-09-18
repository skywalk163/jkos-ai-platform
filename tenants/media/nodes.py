"""自媒体中台 - 工作流节点处理器（M2 任务 2.1/2.2）

H1 内容生产管线节点：
- content_planner: 内容策划
- content_generator: 内容生成
- content_reviewer: 内容审核
- content_publisher: 内容发布

H2 舆情情感管线节点：
- sentiment_monitor: 舆情监控
- sentiment_analyzer: 情感分析
- crisis_alerter: 危机预警
- reply_generator: 回复生成
"""
from __future__ import annotations

from typing import Any, Dict, List

from jkos_core.workflow.base import NodeContext

from tenants.media.adapters.content import ContentPublisher, SentimentMonitor
from tenants.media.analyzers.content import ContentPlanner, SentimentAnalyzer


# ─── H1 内容生产管线节点 ───

async def content_planner(ctx: NodeContext) -> Dict[str, Any]:
    """内容策划节点：基于热点话题生成策划方案"""
    planner = ContentPlanner()
    # 从 context 或 prev_output 获取 topic
    context = ctx.instance.get("context") or {}
    topic = context.get("topic") or (ctx.prev_output.get("topic") if ctx.prev_output else "行业热点")
    plan = planner.plan(topic=topic)
    return {
        "plan_id": plan["id"],
        "title": plan["title"],
        "outline": plan["outline"],
        "platforms": plan["suggested_platforms"],
        "tags": plan["suggested_tags"],
        "topic": topic,
    }


async def content_generator(ctx: NodeContext) -> Dict[str, Any]:
    """内容生成节点：基于策划方案生成正文"""
    plan = ctx.prev_output or {}
    title = plan.get("title", "默认标题")
    outline = plan.get("outline", [])

    # 模拟内容生成
    content = f"# {title}\n\n"
    for i, point in enumerate(outline, 1):
        content += f"## {point}\n\n这里是关于「{point}」的详细内容...\n\n"

    return {
        "title": title,
        "content": content,
        "word_count": len(content),
        "status": "generated",
    }


async def content_reviewer(ctx: NodeContext) -> Dict[str, Any]:
    """内容审核节点：审核内容合规性"""
    content = ctx.prev_output.get("content", "") if ctx.prev_output else ""
    return {
        "content": content[:100] + "..." if len(content) > 100 else content,
        "review_status": "pending_human_review",
        "issues": [],
    }


async def content_publisher(ctx: NodeContext) -> Dict[str, Any]:
    """内容发布节点：多平台分发"""
    publisher = ContentPublisher()
    content = ctx.prev_output.get("content", "") if ctx.prev_output else ""
    title = ctx.prev_output.get("title", "未标题") if ctx.prev_output else "未标题"

    results = publisher.publish_to_all(
        content=content,
        metadata={"title": title, "tags": ["自媒体", "内容"]},
    )

    return {
        "published_count": len(results),
        "platforms": [r["platform"] for r in results],
        "post_ids": [r["post_id"] for r in results],
        "urls": [r["url"] for r in results],
    }


# ─── H2 舆情情感管线节点 ───

async def sentiment_monitor(ctx: NodeContext) -> Dict[str, Any]:
    """舆情监控节点：收集评论/提及"""
    monitor = SentimentMonitor()
    # 从 context 或 prev_output 获取 keyword
    context = ctx.instance.get("context") or {}
    keyword = context.get("keyword") or (ctx.prev_output.get("keyword") if ctx.prev_output else "品牌")
    monitor.track_keyword(keyword)

    # 模拟收集提及
    mentions = monitor.collect_mentions(platform="weibo", count=5)
    return {
        "keyword": keyword,
        "mention_count": len(mentions),
        "platforms": ["weibo", "douyin", "xiaohongshu"],
        "sample_mentions": mentions[:2],
    }


async def sentiment_analyzer(ctx: NodeContext) -> Dict[str, Any]:
    """情感分析节点：分析评论情感倾向"""
    analyzer = SentimentAnalyzer()
    # 从 prev_output 获取 sample_mentions
    prev_output = ctx.prev_output or {}
    sample_mentions = prev_output.get("sample_mentions", [])

    if not sample_mentions:
        # 如果没有 sample_mentions，生成一些模拟数据
        import random
        sentiments = ["positive", "neutral", "negative"]
        for i in range(5):
            sample_mentions.append({
                "id": f"mention_{i}",
                "text": f"模拟评论内容 {i + 1}",
                "sentiment": random.choice(sentiments),
            })

    texts = [m["text"] for m in sample_mentions]
    summary = analyzer.summarize(texts)
    return {
        "total": summary["total"],
        "positive_ratio": summary["positive_ratio"],
        "negative_ratio": summary["negative_ratio"],
        "crisis_count": summary["crisis_count"],
        "overall_sentiment": summary["overall_sentiment"],
        "is_crisis": summary["crisis_count"] > 0,
    }


async def crisis_alerter(ctx: NodeContext) -> Dict[str, Any]:
    """危机预警节点：高风险内容自动升级"""
    analysis = ctx.prev_output or {}
    is_crisis = analysis.get("is_crisis", False)
    crisis_count = analysis.get("crisis_count", 0)

    # 如果 is_crisis 为 False，检查是否有负面情感占比过高
    if not is_crisis:
        negative_ratio = analysis.get("negative_ratio", 0)
        if negative_ratio > 0.5:
            is_crisis = True
            crisis_count = 1

    return {
        "is_crisis": is_crisis,
        "crisis_count": crisis_count,
        "alert_level": "high" if is_crisis else "normal",
        "action": "escalate_to_pr_manager" if is_crisis else "continue_monitoring",
    }


async def reply_generator(ctx: NodeContext) -> Dict[str, Any]:
    """回复生成节点：生成互动回复内容"""
    analysis = ctx.prev_output or {}
    overall_sentiment = analysis.get("overall_sentiment", "neutral")

    replies = {
        "positive": "感谢您的支持！我们会继续努力提供更好的产品和服务。",
        "neutral": "感谢您的反馈，我们会持续改进。",
        "negative": "非常抱歉给您带来不好的体验，我们会认真对待您的反馈并尽快改进。",
    }

    return {
        "sentiment": overall_sentiment,
        "reply": replies.get(overall_sentiment, replies["neutral"]),
        "replies": [replies[s] for s in ["positive", "neutral", "negative"]],
    }


# ─── 节点注册表 ───

MEDIA_NODES: Dict[str, Any] = {
    "content_planner": content_planner,
    "content_generator": content_generator,
    "content_reviewer": content_reviewer,
    "content_publisher": content_publisher,
    "sentiment_monitor": sentiment_monitor,
    "sentiment_analyzer": sentiment_analyzer,
    "crisis_alerter": crisis_alerter,
    "reply_generator": reply_generator,
}
