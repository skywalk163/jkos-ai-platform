"""自媒体中台 - 内容分析器（M2 任务 2.1/2.2）

内容策划器：基于热点话题生成内容策划方案
情感分析器：分析评论情感倾向，识别危机信号
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


class ContentPlanner:
    """内容策划器（M2 任务 2.1）

    基于热点话题、品牌调性、目标受众生成内容策划方案。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.plans: List[Dict[str, Any]] = []

    def plan(self, topic: str, brand_tone: str = "专业", target_audience: str = "年轻用户") -> Dict[str, Any]:
        """生成内容策划方案

        Args:
            topic: 主题/热点话题
            brand_tone: 品牌调性（专业/幽默/温暖/科技感）
            target_audience: 目标受众

        Returns:
            内容策划方案（含标题、正文框架、发布建议）
        """
        plan = {
            "id": f"plan_{len(self.plans) + 1}",
            "topic": topic,
            "brand_tone": brand_tone,
            "target_audience": target_audience,
            "title": f"【{brand_tone}】{topic} - 深度解读",
            "outline": [
                f"引言：为什么{topic}值得关注",
                "核心观点1：行业背景与趋势",
                "核心观点2：关键数据与案例",
                "核心观点3：未来展望与建议",
                "结语：行动号召",
            ],
            "suggested_platforms": ["wechat", "weibo"],
            "suggested_tags": [topic, brand_tone, target_audience],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.plans.append(plan)
        return plan

    def get_plans(self, topic: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取策划方案"""
        if topic:
            return [p for p in self.plans if topic.lower() in p["topic"].lower()]
        return self.plans


class SentimentAnalyzer:
    """情感分析器（M2 任务 2.2）

    分析评论/文本的情感倾向，识别危机信号。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.analysis_history: List[Dict[str, Any]] = []

    def analyze(self, text: str) -> Dict[str, Any]:
        """分析单条文本的情感

        Args:
            text: 待分析文本

        Returns:
            情感分析结果（sentiment、score、keywords、is_crisis）
        """
        import random

        # 模拟情感分析（MVP 阶段）
        sentiments = ["positive", "neutral", "negative"]
        sentiment = random.choice(sentiments)
        score = round(random.uniform(0.1, 0.9), 2)

        # 危机信号检测
        crisis_keywords = ["投诉", "欺诈", "虚假", "骗局", "维权", "曝光"]
        is_crisis = any(kw in text for kw in crisis_keywords)

        result = {
            "text": text[:50] + "..." if len(text) > 50 else text,
            "sentiment": sentiment,
            "score": score,
            "keywords": self._extract_keywords(text),
            "is_crisis": is_crisis,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.analysis_history.append(result)
        return result

    def batch_analyze(self, texts: List[str]) -> List[Dict[str, Any]]:
        """批量分析文本情感"""
        return [self.analyze(text) for text in texts]

    def summarize(self, texts: List[str]) -> Dict[str, Any]:
        """情感汇总分析

        Returns:
            汇总结果（positive_ratio、negative_ratio、crisis_count、overall_sentiment）
        """
        results = self.batch_analyze(texts)
        positive = sum(1 for r in results if r["sentiment"] == "positive")
        negative = sum(1 for r in results if r["sentiment"] == "negative")
        neutral = sum(1 for r in results if r["sentiment"] == "neutral")
        crisis_count = sum(1 for r in results if r["is_crisis"])

        total = len(results) if results else 1
        return {
            "total": total,
            "positive": positive,
            "neutral": neutral,
            "negative": negative,
            "positive_ratio": round(positive / total, 2),
            "negative_ratio": round(negative / total, 2),
            "crisis_count": crisis_count,
            "overall_sentiment": "positive" if positive > negative else "negative" if negative > positive else "neutral",
        }

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词（简化版）"""
        # 模拟关键词提取
        import random
        keywords = ["产品", "服务", "体验", "价格", "质量", "售后", "品牌"]
        return random.sample(keywords, min(3, len(keywords)))
