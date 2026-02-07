#!/usr/bin/env python3
"""
情绪分析模块 - 分析回复内容的情绪倾向
"""
import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Tuple

from openai import OpenAI

logger = logging.getLogger(__name__)

# 情绪分析 Prompt
SENTIMENT_PROMPT = """分析以下论坛回复的情绪倾向。只输出 JSON 格式结果，不要其他内容。

回复内容：
{content}

请分析该回复表达的情绪，输出格式：
{
    "sentiment": "positive" | "neutral" | "negative",
    "score": 0.8,  // -1.0 到 1.0 之间的数值，越接近1越乐观，越接近-1越悲观
    "confidence": 0.9,  // 置信度 0-1
    "keywords": ["股票", "看涨"]  // 提取的关键投资相关词汇
}

判断标准：
- positive: 表达乐观、看好、上涨、盈利、推荐买入等积极态度
- negative: 表达悲观、看跌、下跌、亏损、警告风险等消极态度  
- neutral: 客观陈述、提问、无明显情绪倾向
"""


class SentimentAnalyzer:
    """情绪分析器"""
    
    def __init__(self):
        api_key = os.getenv('KIMI_API_KEY')
        if not api_key:
            raise ValueError("未设置 KIMI_API_KEY 环境变量")
        
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.moonshot.cn/v1"
        )
        self.model = os.getenv('KIMI_MODEL', 'moonshot-v1-8k')
    
    async def analyze(self, content: str) -> Dict:
        """
        分析单条回复的情绪
        
        Returns:
            {
                'sentiment': 'positive'/'neutral'/'negative',
                'score': float (-1.0 to 1.0),
                'confidence': float (0-1),
                'keywords': list
            }
        """
        if not content or len(content.strip()) < 10:
            # 内容太短，无法判断
            return {
                'sentiment': 'neutral',
                'score': 0.0,
                'confidence': 1.0,
                'keywords': []
            }
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个专业的投资情绪分析助手，擅长分析论坛帖子中的情绪倾向。"},
                    {"role": "user", "content": SENTIMENT_PROMPT.format(content=content[:2000])}  # 限制长度
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # 标准化结果
            sentiment = result.get('sentiment', 'neutral').lower()
            if sentiment not in ['positive', 'neutral', 'negative']:
                sentiment = 'neutral'
            
            score = float(result.get('score', 0))
            score = max(-1.0, min(1.0, score))  # 限制范围
            
            return {
                'sentiment': sentiment,
                'score': score,
                'confidence': float(result.get('confidence', 0.5)),
                'keywords': result.get('keywords', [])
            }
            
        except Exception as e:
            logger.error(f"[SentimentAnalyzer] 情绪分析失败: {e}")
            return {
                'sentiment': 'neutral',
                'score': 0.0,
                'confidence': 0.0,
                'keywords': []
            }
    
    async def analyze_batch(self, contents: List[str]) -> List[Dict]:
        """批量分析情绪"""
        results = []
        for content in contents:
            result = await self.analyze(content)
            results.append(result)
        return results


def calculate_sentiment_index(positive: int, neutral: int, negative: int) -> float:
    """
    计算情绪指数
    
    公式: (positive - negative) / total
    范围: -1.0 (极度悲观) 到 +1.0 (极度乐观)
    """
    total = positive + neutral + negative
    if total == 0:
        return 0.0
    
    # 乐观 +1, 中性 0, 悲观 -1
    index = (positive * 1 + neutral * 0 + negative * (-1)) / total
    return round(index, 2)


def aggregate_sentiment_by_date(replies: List[Dict]) -> Dict[str, Dict]:
    """
    按日期聚合情绪数据
    
    Args:
        replies: 回复列表，每个包含 sentiment, created_at 等
        
    Returns:
        {
            '2026-02-07': {
                'total': 10,
                'positive': 3,
                'neutral': 5,
                'negative': 2,
                'index': 0.1
            },
            ...
        }
    """
    from collections import defaultdict
    
    daily_data = defaultdict(lambda: {
        'total': 0,
        'positive': 0,
        'neutral': 0,
        'negative': 0
    })
    
    for reply in replies:
        # 提取日期
        created_at = reply.get('created_at')
        if isinstance(created_at, datetime):
            date_str = created_at.strftime('%Y-%m-%d')
        elif isinstance(created_at, str):
            date_str = created_at[:10]
        else:
            continue
        
        sentiment = reply.get('sentiment', 'neutral')
        
        daily_data[date_str]['total'] += 1
        if sentiment == 'positive':
            daily_data[date_str]['positive'] += 1
        elif sentiment == 'negative':
            daily_data[date_str]['negative'] += 1
        else:
            daily_data[date_str]['neutral'] += 1
    
    # 计算情绪指数
    result = {}
    for date, data in daily_data.items():
        data['index'] = calculate_sentiment_index(
            data['positive'],
            data['neutral'],
            data['negative']
        )
        result[date] = data
    
    return result
