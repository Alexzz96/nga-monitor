"""
AI 分析模块 - 基于 LLM 的用户风格分析
"""
import os
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from db.models import SessionLocal, ReplyArchive, MonitorTarget, UserStyleProfile, StyleComparison, SentimentAnalysis

logger = logging.getLogger(__name__)


class AIAnalyzer:
    """AI 分析器 - 基于 LLM 的用户风格分析"""
    
    def __init__(self, provider=None, api_key=None, base_url=None, model=None):
        """
        初始化 AI 分析器
        
        Args:
            provider: AI 提供商 (kimi/openai/openrouter)
            api_key: API Key
            base_url: API Base URL
            model: 模型名称
        """
        self.provider = provider or os.getenv('AI_PROVIDER', 'openai')
        self.api_key = api_key or os.getenv('AI_API_KEY', '')
        self.base_url = base_url or os.getenv('AI_BASE_URL', '')
        self.model = model or os.getenv('AI_MODEL', 'gpt-4')
        
        logger.info(f"[AIAnalyzer] 配置: provider={self.provider}, model={self.model}, base={self.base_url}")
        logger.info(f"[AIAnalyzer] API Key 已配置: {bool(self.api_key)}")
    
    @classmethod
    def from_db(cls, db):
        """
        从数据库配置创建 AIAnalyzer 实例
        
        Args:
            db: 数据库会话
            
        Returns:
            AIAnalyzer 实例
        """
        from db.models import Config
        config = Config.get_ai_config(db)
        return cls(
            provider=config.get('provider', 'openai'),
            api_key=config.get('api_key', ''),
            base_url=config.get('base_url', ''),
            model=config.get('model', 'gpt-4')
        )
    
    def _get_client(self) -> AsyncOpenAI:
        """获取 OpenAI 客户端"""
        if self.provider == 'kimi':
            return AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url or "https://api.moonshot.cn/v1"
            )
        elif self.provider == 'openrouter':
            return AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url or "https://openrouter.ai/api/v1"
            )
        else:
            return AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url or "https://api.openai.com/v1"
            )
    
    async def _call_api(self, messages: List[Dict], temperature: float = 0.7) -> Optional[str]:
        """
        调用 AI API
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            
        Returns:
            AI 响应内容
        """
        try:
            client = self._get_client()
            
            # kimi-k2.5 只支持 temperature=1
            if self.provider == 'kimi' and 'k2.5' in self.model:
                temperature = 1.0
            
            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=2000
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"[AIAnalyzer] API 调用失败: {e}")
            return None
    
    def _prepare_analysis_text(self, replies: List[ReplyArchive]) -> str:
        """
        准备分析文本
        
        Args:
            replies: 回复列表
            
        Returns:
            分析文本
        """
        texts = []
        for reply in replies:
            text = f"主题: {reply.topic_title}\n内容: {reply.main_content}"
            texts.append(text)
        
        return "\n\n---\n\n".join(texts[:50])  # 最多取前 50 条
    
    def _prepare_daily_sentiment_text(self, replies: List[ReplyArchive], user_name: str) -> str:
        """准备单日情绪分析文本"""
        text = f"用户: {user_name}\n当日回复数量: {len(replies)}\n\n"
        
        for i, reply in enumerate(replies):
            text += f"\n--- 回复 {i+1} ---\n"
            text += f"主题: {reply.topic_title}\n"
            text += f"内容: {reply.main_content[:300]}\n"
        
        return text
    
    def _calculate_sentiment_score(self, sentiment: str) -> int:
        """计算情感分数"""
        sentiment_map = {
            '乐观': 80,
            '积极': 60,
            '中性': 0,
            '消极': -60,
            '悲观': -80,
            '保守': -20,
            '激进': 50
        }
        return sentiment_map.get(sentiment, 0)
    
    async def analyze_user_style(self, target_id: int, time_range: str = 'week') -> Optional[Dict]:
        """
        分析用户风格
        
        Args:
            target_id: 监控目标 ID
            time_range: 时间范围 (week/month/all)
            
        Returns:
            分析结果字典
        """
        db = SessionLocal()
        try:
            # 获取目标信息
            target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
            if not target:
                logger.error(f"[AIAnalyzer] 目标不存在: {target_id}")
                return None
            
            # 计算时间范围
            end_date = datetime.now(timezone.utc)
            if time_range == 'week':
                start_date = end_date - timedelta(days=7)
            elif time_range == 'month':
                start_date = end_date - timedelta(days=30)
            else:
                start_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
            
            # 获取回复
            replies = db.query(ReplyArchive).filter(
                ReplyArchive.target_id == target_id,
                ReplyArchive.created_at >= start_date,
                ReplyArchive.created_at <= end_date
            ).order_by(ReplyArchive.created_at.desc()).limit(100).all()
            
            if len(replies) < 5:
                logger.warning(f"[AIAnalyzer] 回复数量不足: {len(replies)}")
                return None
            
            # 准备分析文本
            analysis_text = self._prepare_analysis_text(replies)
            
            # 构建提示词
            prompt = f"""请分析以下 NGA 论坛用户在股票/投资相关板块的言论风格。

用户名称: {target.name}
分析时间范围: {time_range} ({start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')})
共 {len(replies)} 条回复

以下是用户的部分回复内容:

{analysis_text}

请从以下几个维度分析该用户的风格，并以 JSON 格式输出:

{{
    "personality": "性格特点描述（如：谨慎、激进、理性、情绪化等）",
    "investment_style": "投资风格（如：价值投资、趋势跟踪、短线投机等）",
    "communication_style": "沟通风格（如：简洁、详细、激进、温和等）",
    "emotional_tendency": "情感倾向（乐观/中性/悲观）",
    "keywords": ["关键词1", "关键词2", "关键词3", "关键词4", "关键词5"],
    "risk_tolerance": "风险偏好（高/中/低）",
    "summary": "总体评价（100字以内）"
}}

注意：输出必须是有效的 JSON 格式。"""

            messages = [
                {
                    "role": "system",
                    "content": "你是一位专业的用户行为分析师，擅长通过文本分析用户的性格特点和投资风格。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
            
            # 调用 AI
            response = await self._call_api(messages)
            if not response:
                return None
            
            # 解析 JSON
            try:
                # 尝试提取 JSON 部分
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                
                if json_start >= 0 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    result = json.loads(json_str)
                else:
                    result = json.loads(response)
                
                # 保存到数据库
                self._save_style_profile(db, target_id, time_range, result)
                
                return {
                    'target_id': target_id,
                    'target_name': target.name,
                    'time_range': time_range,
                    'replies_count': len(replies),
                    **result
                }
                
            except json.JSONDecodeError as e:
                logger.error(f"[AIAnalyzer] JSON 解析失败: {e}")
                logger.debug(f"[AIAnalyzer] 原始响应: {response}")
                return None
                
        except Exception as e:
            logger.error(f"[AIAnalyzer] 分析失败: {e}", exc_info=True)
            return None
        finally:
            db.close()
    
    def _save_style_profile(self, db: Session, target_id: int, time_range: str, result: Dict):
        """
        保存风格档案
        
        Args:
            db: 数据库会话
            target_id: 目标 ID
            time_range: 时间范围
            result: 分析结果
        """
        try:
            # 查找现有记录
            profile = db.query(UserStyleProfile).filter(
                UserStyleProfile.target_id == target_id,
                UserStyleProfile.time_range == time_range
            ).first()
            
            if profile:
                # 更新
                profile.personality = result.get('personality', '')
                profile.investment_style = result.get('investment_style', '')
                profile.communication_style = result.get('communication_style', '')
                profile.emotional_tendency = result.get('emotional_tendency', '中性')
                profile.keywords = json.dumps(result.get('keywords', []), ensure_ascii=False)
                profile.risk_tolerance = result.get('risk_tolerance', '中')
                profile.summary = result.get('summary', '')
                profile.analyzed_at = datetime.now(timezone.utc)
            else:
                # 创建新记录
                profile = UserStyleProfile(
                    target_id=target_id,
                    time_range=time_range,
                    personality=result.get('personality', ''),
                    investment_style=result.get('investment_style', ''),
                    communication_style=result.get('communication_style', ''),
                    emotional_tendency=result.get('emotional_tendency', '中性'),
                    keywords=json.dumps(result.get('keywords', []), ensure_ascii=False),
                    risk_tolerance=result.get('risk_tolerance', '中'),
                    summary=result.get('summary', ''),
                    analyzed_at=datetime.now(timezone.utc)
                )
                db.add(profile)
            
            db.commit()
            logger.info(f"[AIAnalyzer] 风格档案已保存: target_id={target_id}")
            
        except Exception as e:
            logger.error(f"[AIAnalyzer] 保存风格档案失败: {e}")
            db.rollback()
    
    async def compare_users(self, target_ids: List[int], time_range: str = 'week') -> Optional[Dict]:
        """
        对比多个用户
        
        Args:
            target_ids: 目标 ID 列表
            time_range: 时间范围
            
        Returns:
            对比结果
        """
        if len(target_ids) < 2:
            return None
        
        db = SessionLocal()
        try:
            # 获取所有目标的分析结果
            profiles = []
            for target_id in target_ids:
                profile = db.query(UserStyleProfile).filter(
                    UserStyleProfile.target_id == target_id,
                    UserStyleProfile.time_range == time_range
                ).first()
                
                if profile:
                    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
                    profiles.append({
                        'target_id': target_id,
                        'target_name': target.name if target else f"User_{target_id}",
                        'profile': profile
                    })
            
            if len(profiles) < 2:
                logger.warning("[AIAnalyzer] 可对比的用户数量不足")
                return None
            
            # 构建对比文本
            compare_text = []
            for p in profiles:
                compare_text.append(f"""
用户: {p['target_name']}
性格: {p['profile'].personality}
投资风格: {p['profile'].investment_style}
沟通风格: {p['profile'].communication_style}
情感倾向: {p['profile'].emotional_tendency}
关键词: {p['profile'].keywords}
风险偏好: {p['profile'].risk_tolerance}
""")
            
            prompt = f"""请对比以下几位 NGA 论坛用户的风格差异:

{chr(10).join(compare_text)}

请从以下几个方面进行对比分析，并以 JSON 格式输出:

{{
    "similarities": "共同点描述",
    "differences": "主要差异描述",
    "style_comparison": {{
        "investment": "投资风格对比",
        "communication": "沟通风格对比",
        "risk": "风险偏好对比"
    }},
    "recommendations": "投资建议（针对关注这组用户的读者）",
    "summary": "总体评价（100字以内）"
}}

注意：输出必须是有效的 JSON 格式。"""

            messages = [
                {
                    "role": "system",
                    "content": "你是一位专业的用户对比分析师，擅长发现用户之间的异同并提供有价值的见解。"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
            
            response = await self._call_api(messages)
            if not response:
                return None
            
            # 解析 JSON
            try:
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                
                if json_start >= 0 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    result = json.loads(json_str)
                else:
                    result = json.loads(response)
                
                # 保存对比结果
                self._save_comparison(db, target_ids, time_range, result)
                
                return {
                    'target_ids': target_ids,
                    'target_names': [p['target_name'] for p in profiles],
                    'time_range': time_range,
                    **result
                }
                
            except json.JSONDecodeError as e:
                logger.error(f"[AIAnalyzer] JSON 解析失败: {e}")
                return None
                
        except Exception as e:
            logger.error(f"[AIAnalyzer] 对比分析失败: {e}", exc_info=True)
            return None
        finally:
            db.close()
    
    def _save_comparison(self, db: Session, target_ids: List[int], time_range: str, result: Dict):
        """保存对比结果"""
        try:
            comparison = StyleComparison(
                target_ids=json.dumps(target_ids),
                time_range=time_range,
                similarities=result.get('similarities', ''),
                differences=result.get('differences', ''),
                style_comparison=json.dumps(result.get('style_comparison', {}), ensure_ascii=False),
                recommendations=result.get('recommendations', ''),
                summary=result.get('summary', ''),
                compared_at=datetime.now(timezone.utc)
            )
            db.add(comparison)
            db.commit()
            
        except Exception as e:
            logger.error(f"[AIAnalyzer] 保存对比结果失败: {e}")
            db.rollback()
    
    async def analyze_daily_sentiment(self, target_id: int, days: int = 30) -> Optional[Dict]:
        """
        分析每日情绪指数 - 基于每一天的回复计算情绪
        
        Args:
            target_id: 监控目标 ID
            days: 分析天数
            
        Returns:
            每日情绪分析结果
        """
        db = SessionLocal()
        try:
            logger.info(f"[每日情绪分析] 开始分析 target_id={target_id}, days={days}")
            
            # 获取用户信息
            target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
            if not target:
                logger.error(f"[每日情绪分析] 目标不存在: {target_id}")
                return None
            
            # 获取时间范围
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=days)
            
            # 获取该时段的回复
            replies = db.query(ReplyArchive).filter(
                ReplyArchive.target_id == target_id,
                ReplyArchive.created_at >= start_date,
                ReplyArchive.created_at <= end_date
            ).order_by(ReplyArchive.created_at.asc()).all()
            
            logger.info(f"[每日情绪分析] 查询到 {len(replies)} 条回复")
            
            if len(replies) < 3:
                logger.warning(f"[每日情绪分析] 回复数量不足: {len(replies)}")
                return None
            
            # 按日期分组回复
            daily_replies = defaultdict(list)
            for reply in replies:
                # 提取日期 (YYYY-MM-DD)
                date_str = reply.post_date[:10] if reply.post_date else reply.created_at.strftime('%Y-%m-%d')
                daily_replies[date_str].append(reply)
            
            logger.info(f"[每日情绪分析] 分布在 {len(daily_replies)} 天")
            
            # 对每一天进行情绪分析
            results = []
            for date_str, day_replies in sorted(daily_replies.items()):
                logger.info(f"[每日情绪分析] 分析 {date_str}: {len(day_replies)} 条回复")
                
                # 准备当天的分析文本
                day_text = self._prepare_daily_sentiment_text(day_replies, target.name)
                
                # 调用AI分析当天情绪
                sentiment_result = await self._analyze_single_day_sentiment(day_text, target.name, date_str)
                
                if sentiment_result:
                    # 保存到数据库
                    self._save_daily_sentiment(db, target_id, date_str, day_replies, sentiment_result)
                    results.append({
                        'date': date_str,
                        'replies_count': len(day_replies),
                        **sentiment_result
                    })
            
            db.commit()
            logger.info(f"[每日情绪分析] 完成，分析了 {len(results)} 天")
            
            return {
                'target_id': target_id,
                'target_name': target.name,
                'days_analyzed': len(results),
                'daily_results': results
            }
            
        except Exception as e:
            logger.error(f"[每日情绪分析] 分析失败: {e}", exc_info=True)
            db.rollback()
            return None
        finally:
            db.close()
    
    async def _analyze_single_day_sentiment(self, day_text: str, user_name: str, date_str: str) -> Optional[Dict]:
        """分析单日的情绪"""
        prompt = f"""请分析以下用户 "{user_name}" 在 {date_str} 的投资情绪倾向。

{day_text}

请基于回复内容判断该用户当天的整体投资情绪，并给出量化评分。

请输出 JSON 格式:
{{
    "sentiment_label": "乐观/积极/中性/消极/悲观",
    "sentiment_index": 0.0,  // -1.0到1.0之间，0为中性，正值为积极，负值为消极
    "confidence": 0.8,  // 置信度0-1
    "keywords": ["关键词1", "关键词2"],  // 当天提及的投资相关关键词
    "reason": "简要分析理由"
}}

注意：sentiment_index 是-1.0到1.0的浮点数，用于绘制情绪趋势图。"""

        messages = [
            {
                "role": "system",
                "content": "你是一位专业的投资情绪分析师，擅长通过文本分析判断投资者的情绪倾向。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
        
        response = await self._call_api(messages)
        if not response:
            return None
        
        try:
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                result = json.loads(json_str)
            else:
                result = json.loads(response)
            
            return {
                'sentiment_label': result.get('sentiment_label', '中性'),
                'sentiment_index': float(result.get('sentiment_index', 0)),
                'confidence': float(result.get('confidence', 0.5)),
                'keywords': result.get('keywords', []),
                'reason': result.get('reason', '')
            }
        except Exception as e:
            logger.warning(f"[每日情绪分析] JSON解析失败: {e}")
            return {
                'sentiment_label': '中性',
                'sentiment_index': 0.0,
                'confidence': 0.0,
                'keywords': [],
                'reason': '解析失败'
            }
    
    def _save_daily_sentiment(self, db, target_id: int, date_str: str, replies: List[ReplyArchive], sentiment: Dict):
        """保存单日情绪分析结果"""
        # 检查是否已有记录
        existing = db.query(SentimentAnalysis).filter(
            SentimentAnalysis.target_id == target_id,
            SentimentAnalysis.date == date_str
        ).first()
        
        # 统计正负中性（基于AI返回的标签）
        label = sentiment.get('sentiment_label', '中性')
        positive = 1 if label in ['乐观', '积极'] else 0
        negative = 1 if label in ['悲观', '消极'] else 0
        neutral = 1 if label == '中性' or (not positive and not negative) else 0
        
        if existing:
            # 更新
            existing.total_replies = len(replies)
            existing.positive_count = positive
            existing.negative_count = negative
            existing.neutral_count = neutral
            existing.sentiment_index = sentiment.get('sentiment_index', 0)
            existing.keyword_sentiment = json.dumps({
                'keywords': sentiment.get('keywords', []),
                'reason': sentiment.get('reason', ''),
                'confidence': sentiment.get('confidence', 0)
            }, ensure_ascii=False)
            # updated_at 会自动更新
        else:
            # 创建
            analysis = SentimentAnalysis(
                target_id=target_id,
                date=date_str,
                total_replies=len(replies),
                positive_count=positive,
                negative_count=negative,
                neutral_count=neutral,
                sentiment_index=sentiment.get('sentiment_index', 0),
                keyword_sentiment=json.dumps({
                    'keywords': sentiment.get('keywords', []),
                    'reason': sentiment.get('reason', ''),
                    'confidence': sentiment.get('confidence', 0)
                }, ensure_ascii=False)
                # created_at 和 updated_at 有默认值
            )
            db.add(analysis)
            logger.debug(f"[每日情绪分析] 创建 {date_str} 记录")


# 便捷函数 (异步)
async def analyze_user(target_id: int, time_range: str = 'week') -> Optional[Dict]:
    """便捷函数：分析单个用户"""
    db = SessionLocal()
    try:
        analyzer = AIAnalyzer.from_db(db)
        return await analyzer.analyze_user_style(target_id, time_range)
    finally:
        db.close()


async def compare_users(target_ids: List[int], time_range: str = 'week') -> Optional[Dict]:
    """便捷函数：对比多个用户"""
    db = SessionLocal()
    try:
        analyzer = AIAnalyzer.from_db(db)
        return await analyzer.compare_users(target_ids, time_range)
    finally:
        db.close()


async def analyze_daily_sentiment(target_id: int, days: int = 30) -> Optional[Dict]:
    """便捷函数：分析每日情绪"""
    db = SessionLocal()
    try:
        analyzer = AIAnalyzer.from_db(db)
        return await analyzer.analyze_daily_sentiment(target_id, days)
    finally:
        db.close()
