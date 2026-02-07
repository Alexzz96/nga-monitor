#!/usr/bin/env python3
"""
情绪分析模块 - 分析回复内容的情绪倾向
"""
import os
import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Dict, Tuple

from openai import OpenAI

logger = logging.getLogger(__name__)

# 情绪分析 Prompt
SENTIMENT_PROMPT = """分析以下论坛回复的情绪倾向。只输出 JSON 格式结果，不要其他内容。

回复内容：
{content}

请分析该回复表达的情绪，输出格式：
{{
    "sentiment": "positive" | "neutral" | "negative",
    "score": 0.8,  // -1.0 到 1.0 之间的数值，越接近1越乐观，越接近-1越悲观
    "confidence": 0.9,  // 置信度 0-1
    "keywords": ["股票", "看涨"]  // 提取的关键投资相关词汇
}}

判断标准：
- positive: 表达乐观、看好、上涨、盈利、推荐买入等积极态度
- negative: 表达悲观、看跌、下跌、亏损、警告风险等消极态度  
- neutral: 客观陈述、提问、无明显情绪倾向
"""


class SentimentAnalyzer:
    """情绪分析器 - 使用与 AI 分析相同的配置"""
    
    def __init__(self, config: dict = None):
        """
        初始化情绪分析器
        
        Args:
            config: AI 配置字典，默认从数据库读取（与 AIAnalyzer 一致）
        """
        if config:
            self.config = config
        else:
            # 尝试从数据库读取配置
            self.config = self._load_config_from_db()
        
        self.provider = self.config.get('provider', 'kimi').lower()
        self.api_key = self.config.get('api_key', '')
        self.api_base = self.config.get('base_url', 'https://api.moonshot.cn/v1')
        self.model = self.config.get('model', 'moonshot-v1-8k')
        
        if not self.api_key:
            raise ValueError("未配置 API Key，请先配置 AI 分析设置")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base
        )
        logger.info(f"[SentimentAnalyzer] 初始化完成: provider={self.provider}, model={self.model}")
    
    def _load_config_from_db(self) -> dict:
        """从数据库加载 AI 配置"""
        try:
            from db.models import SessionLocal, Config
            db = SessionLocal()
            try:
                configs = db.query(Config).all()
                config_dict = {c.key: c.value for c in configs}
                
                # 映射配置项名称（与 AIAnalyzer 保持一致）
                return {
                    'provider': config_dict.get('ai_provider', 'kimi'),
                    'api_key': config_dict.get('ai_api_key', ''),
                    'base_url': config_dict.get('ai_base_url', 'https://api.moonshot.cn/v1'),
                    'model': config_dict.get('ai_model', 'moonshot-v1-8k'),
                }
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[SentimentAnalyzer] 从数据库加载配置失败: {e}")
            # 回退到环境变量
            return {
                'provider': 'kimi',
                'api_key': os.getenv('KIMI_API_KEY', ''),
                'base_url': 'https://api.moonshot.cn/v1',
                'model': os.getenv('KIMI_MODEL', 'moonshot-v1-8k'),
            }
    
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
                    {"role": "system", "content": "你是一个专业的投资情绪分析助手，擅长分析论坛帖子中的情绪倾向。只输出 JSON 格式结果，格式必须是：{\"sentiment\": \"positive/neutral/negative\", \"score\": 0.5, \"confidence\": 0.8, \"keywords\": []}"},
                    {"role": "user", "content": SENTIMENT_PROMPT.format(content=content[:2000])}
                ],
                temperature=0.3,
                max_tokens=200
            )
            
            content_text = response.choices[0].message.content.strip()
            
            # 尝试提取 JSON
            result = self._parse_json_response(content_text)
            
            # 标准化结果
            sentiment = result.get('sentiment', 'neutral')
            if isinstance(sentiment, str):
                sentiment = sentiment.lower().strip()
            else:
                sentiment = 'neutral'
            
            if sentiment not in ['positive', 'neutral', 'negative']:
                sentiment = 'neutral'
            
            score = float(result.get('score', 0)) if result.get('score') is not None else 0.0
            score = max(-1.0, min(1.0, score))
            
            confidence = float(result.get('confidence', 0.5)) if result.get('confidence') is not None else 0.5
            confidence = max(0.0, min(1.0, confidence))
            
            keywords = result.get('keywords', [])
            if not isinstance(keywords, list):
                keywords = []
            
            return {
                'sentiment': sentiment,
                'score': score,
                'confidence': confidence,
                'keywords': keywords
            }
            
        except Exception as e:
            logger.error(f"[SentimentAnalyzer] 情绪分析失败: {e}, 内容: {content[:100]}")
            return {
                'sentiment': 'neutral',
                'score': 0.0,
                'confidence': 0.0,
                'keywords': []
            }
    
    def _parse_json_response(self, text: str) -> Dict:
        """解析 AI 返回的 JSON 响应"""
        # 首先尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # 尝试提取 JSON 块
        # 查找花括号包裹的内容
        match = re.search(r'\{[\s\S]*?\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        
        # 尝试提取键值对
        result = {}
        
        # 提取 sentiment
        sentiment_match = re.search(r'["\']?sentiment["\']?\s*:\s*["\']?(\w+)["\']?', text, re.IGNORECASE)
        if sentiment_match:
            result['sentiment'] = sentiment_match.group(1).lower()
        
        # 提取 score
        score_match = re.search(r'["\']?score["\']?\s*:\s*([\d\.-]+)', text, re.IGNORECASE)
        if score_match:
            try:
                result['score'] = float(score_match.group(1))
            except ValueError:
                result['score'] = 0.0
        
        # 提取 confidence
        confidence_match = re.search(r'["\']?confidence["\']?\s*:\s*([\d\.-]+)', text, re.IGNORECASE)
        if confidence_match:
            try:
                result['confidence'] = float(confidence_match.group(1))
            except ValueError:
                result['confidence'] = 0.5
        
        # 提取 keywords（简化处理）
        keywords_match = re.search(r'["\']?keywords["\']?\s*:\s*\[(.*?)\]', text, re.IGNORECASE | re.DOTALL)
        if keywords_match:
            keywords_str = keywords_match.group(1)
            # 提取引号中的词
            keywords = re.findall(r'["\']([^"\']+)["\']', keywords_str)
            result['keywords'] = keywords
        
        return result if result else {'sentiment': 'neutral', 'score': 0.0, 'confidence': 0.5, 'keywords': []}


# 便捷函数
def calculate_sentiment_index(positive: int, neutral: int, negative: int) -> float:
    """
    计算情绪指数 (-1.0 到 1.0)
    
    Args:
        positive: 乐观回复数
        neutral: 中性回复数
        negative: 悲观回复数
    
    Returns:
        情绪指数，范围 -1.0 (极度悲观) 到 1.0 (极度乐观)
    """
    total = positive + neutral + negative
    if total == 0:
        return 0.0
    
    # 权重计算
    index = (positive - negative) / total
    
    return round(index, 2)


def aggregate_sentiment_by_date(replies: List[Dict]) -> Dict[str, Dict]:
    """
    按日期聚合情绪数据
    
    Args:
        replies: 回复列表，每项包含 'created_at' 和 'sentiment'
    
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