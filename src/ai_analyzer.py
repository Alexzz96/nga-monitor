#!/usr/bin/env python3
"""
AI 分析模块 - 分析用户回复风格
支持 OpenAI 和 Kimi (Moonshot)
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from db.models import SessionLocal, ReplyArchive, AIAnalysisReport, MonitorTarget, Config
import logging

logger = logging.getLogger(__name__)


class AIAnalyzer:
    """AI 分析器"""
    
    def __init__(self, config: dict = None):
        """
        初始化 AI 分析器
        
        Args:
            config: AI 配置字典，默认从数据库读取
        """
        logger.info("[AIAnalyzer] 初始化分析器...")
        
        if config:
            self.config = config
            logger.info("[AIAnalyzer] 使用传入的配置")
        else:
            self.config = self._load_config_from_db()
            logger.info("[AIAnalyzer] 从数据库加载配置")
        
        self.provider = self.config.get('provider', 'kimi').lower()
        self.api_key = self.config.get('api_key', '')
        self.api_base = self.config.get('base_url', 'https://api.moonshot.cn/v1')
        self.model = self.config.get('model', 'moonshot-v1-8k')
        self.system_prompt = self.config.get('system_prompt', '你是一位专业的投资行为分析师。')
        self.analysis_prompt = self.config.get('analysis_prompt', '')
        
        logger.info(f"[AIAnalyzer] 配置: provider={self.provider}, model={self.model}, base={self.api_base}")
        logger.info(f"[AIAnalyzer] API Key 已配置: {bool(self.api_key)}")
        # 安全：不记录 API Key 的任何部分
    
    def _load_config_from_db(self) -> dict:
        """从数据库加载配置"""
        db = SessionLocal()
        try:
            return Config.get_ai_config(db)
        finally:
            db.close()
    
    async def _call_api(self, messages: List[Dict]) -> Optional[str]:
        """调用 AI API (异步)"""
        try:
            import httpx
            
            logger.info(f"[AI API] 开始调用 - Model: {self.model}, Base: {self.api_base}")
            # 安全：不记录 API Key
            
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            
            # 不传 temperature，使用模型默认值
            payload = {
                'model': self.model,
                'messages': messages,
                'max_tokens': 2000
            }
            
            # 安全：不记录可能包含敏感信息的 payload
            logger.debug(f"[AI API] 消息数量: {len(messages)}")
            
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f'{self.api_base}/chat/completions',
                    headers=headers,
                    json=payload
                )
            
            logger.info(f"[AI API] 响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                logger.info(f"[AI API] 调用成功，返回内容长度: {len(content)}")
                logger.debug(f"[AI API] 返回内容前200字: {content[:200]}...")
                return content
            else:
                logger.error(f"[AI API] 错误: {response.status_code}")
                # 安全：限制错误响应长度，避免泄露敏感信息
                error_text = response.text[:500] if len(response.text) > 500 else response.text
                logger.error(f"[AI API] 响应内容: {error_text}")
                return None
                
        except httpx.TimeoutException:
            logger.error("[AI API] 请求超时")
            return None
        except httpx.RequestError as e:
            logger.error(f"[AI API] 请求失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[AI API] 调用失败: {e}", exc_info=True)
            return None
    
    async def analyze_user_style(self, target_id: int, time_range: str = 'week') -> Optional[Dict]:
        """
        分析单个用户的操作风格 (异步)
        
        Args:
            target_id: 监控目标 ID
            time_range: 'week' 本周, 'month' 本月, 'all' 全部
            
        Returns:
            分析报告字典
        """
        db = SessionLocal()
        try:
            logger.info(f"[分析用户] 开始分析 target_id={target_id}, time_range={time_range}")
            
            # 获取用户信息
            target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
            if not target:
                logger.error(f"[分析用户] 目标不存在: {target_id}")
                return None
            
            logger.info(f"[分析用户] 目标用户: {target.name} (uid={target.uid})")
            
            # 获取时间范围
            start_date, end_date = self._get_date_range(time_range)
            logger.info(f"[分析用户] 时间范围: {start_date} 到 {end_date}")
            
            # 获取该时段的回复
            replies = db.query(ReplyArchive).filter(
                ReplyArchive.target_id == target_id,
                ReplyArchive.created_at >= start_date,
                ReplyArchive.created_at <= end_date
            ).order_by(ReplyArchive.created_at.desc()).all()
            
            logger.info(f"[分析用户] 查询到 {len(replies)} 条回复")
            
            if len(replies) < 3:
                logger.warning(f"[分析用户] 回复数量不足，无法分析: {len(replies)} 条")
                return None
            
            # 准备分析文本
            analysis_text = self._prepare_analysis_text(replies, target.name)
            logger.debug(f"[分析用户] 分析文本长度: {len(analysis_text)}")
            
            # 使用自定义提示词或默认提示词
            if self.analysis_prompt:
                user_prompt = self.analysis_prompt.format(
                    user_name=target.name,
                    content=analysis_text
                )
            else:
                user_prompt = f"""请分析以下用户 "{target.name}" 的投资风格和发言特征：

{analysis_text}

请从以下几个方面进行分析，并以 JSON 格式返回：
{{
    "summary": "100字以内的整体评价",
    "investment_style": "投资风格（如：长线价值投资/短线波段操作/均衡配置等）",
    "risk_preference": "风险偏好（保守/稳健/激进）",
    "focus_areas": ["关注的板块1", "关注的板块2"],
    "key_stocks": ["提及的股票/基金代码"],
    "sentiment": "整体情绪倾向（乐观/中性/悲观）",
    "characteristics": ["特点1", "特点2", "特点3"],
    "recommendation": "对该用户的简要评价或建议"
}}"""
            
            # 构建提示词
            messages = [
                {
                    "role": "system",
                    "content": self.system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ]
            
            logger.info(f"[分析用户] 开始调用 AI，用户: {target.name}")
            logger.debug(f"[分析用户] System Prompt: {self.system_prompt[:100]}...")
            logger.debug(f"[分析用户] User Prompt 前200字: {user_prompt[:200]}...")
            
            # 调用 AI (异步)
            response = await self._call_api(messages)
            if not response:
                logger.error("[分析用户] AI 调用返回空，分析失败")
                return None
            
            logger.info(f"[分析用户] 收到 AI 响应，长度: {len(response)}")
            
            # 解析 JSON
            try:
                # 尝试提取 JSON 部分
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                logger.debug(f"[分析用户] JSON 提取: start={json_start}, end={json_end}")
                
                if json_start >= 0 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    logger.debug(f"[分析用户] 提取的 JSON 字符串前200字: {json_str[:200]}...")
                    analysis_result = json.loads(json_str)
                else:
                    analysis_result = json.loads(response)
                
                logger.info(f"[分析用户] JSON 解析成功: {list(analysis_result.keys())}")
            except json.JSONDecodeError as e:
                logger.warning(f"[分析用户] JSON 解析失败: {e}")
                logger.warning(f"[分析用户] AI 返回内容前300字: {response[:300]}...")
                analysis_result = {
                    "summary": response[:200],
                    "investment_style": "未知",
                    "risk_preference": "未知",
                    "focus_areas": [],
                    "key_stocks": [],
                    "sentiment": "未知",
                    "characteristics": [],
                    "recommendation": ""
                }
            
            # 计算情感分数
            sentiment_score = self._calculate_sentiment_score(analysis_result.get('sentiment', '中性'))
            logger.info(f"[分析用户] 情感分数: {sentiment_score}")
            
            # 保存报告
            try:
                report = AIAnalysisReport(
                    target_id=target_id,
                    analysis_type='single',
                    time_range=time_range,
                    start_date=start_date.strftime('%Y-%m-%d'),
                    end_date=end_date.strftime('%Y-%m-%d'),
                    report_content=json.dumps(analysis_result, ensure_ascii=False),
                    summary=analysis_result.get('summary', '')[:500],
                    style_tags=json.dumps(analysis_result.get('characteristics', []), ensure_ascii=False),
                    keywords=json.dumps(analysis_result.get('key_stocks', []) + analysis_result.get('focus_areas', []), ensure_ascii=False),
                    sentiment_score=sentiment_score
                )
                db.add(report)
                db.commit()
                db.refresh(report)
                logger.info(f"[分析用户] 报告已保存到数据库: ID {report.id}")
            except Exception as e:
                logger.error(f"[分析用户] 保存报告失败: {e}", exc_info=True)
                raise
            
            result = {
                'report_id': report.id,
                'target_name': target.name,
                'time_range': time_range,
                'analysis': analysis_result,
                'created_at': report.created_at.isoformat()
            }
            logger.info(f"[分析用户] 分析完成，返回结果: report_id={report.id}")
            return result
            
        except Exception as e:
            logger.error(f"[分析用户] 分析过程中发生错误: {e}", exc_info=True)
            return None
        finally:
            db.close()
            logger.debug("[分析用户] 数据库连接已关闭")
    
    async def compare_users(self, target_ids: List[int], time_range: str = 'week') -> Optional[Dict]:
        """
        对比分析多个用户 (异步)
        
        Args:
            target_ids: 监控目标 ID 列表
            time_range: 时间范围
            
        Returns:
            对比分析报告
        """
        if len(target_ids) < 2:
            logger.error("至少需要 2 个用户进行对比")
            return None
        
        db = SessionLocal()
        try:
            start_date, end_date = self._get_date_range(time_range)
            
            users_data = []
            for target_id in target_ids:
                target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
                if not target:
                    continue
                
                replies = db.query(ReplyArchive).filter(
                    ReplyArchive.target_id == target_id,
                    ReplyArchive.created_at >= start_date,
                    ReplyArchive.created_at <= end_date
                ).order_by(ReplyArchive.created_at.desc()).limit(20).all()
                
                if replies:
                    users_data.append({
                        'name': target.name,
                        'uid': target.uid,
                        'replies': replies
                    })
            
            if len(users_data) < 2:
                logger.error("有效用户不足，无法对比")
                return None
            
            # 构建对比文本
            compare_text = ""
            for user in users_data:
                compare_text += f"\n\n=== 用户: {user['name']} ===\n"
                for reply in user['replies'][:10]:  # 每人取 10 条
                    compare_text += f"\n[{reply.post_date}] {reply.topic_title}\n{reply.main_content[:300]}\n"
            
            messages = [
                {
                    "role": "system",
                    "content": "你是一位专业的投资行为分析师，擅长对比不同投资者的风格差异。请用中文回答。"
                },
                {
                    "role": "user",
                    "content": f"""请对比分析以下 {len(users_data)} 位用户的投资风格和发言特征：

{compare_text}

请从以下几个方面进行对比分析，并以 JSON 格式返回：
{{
    "summary": "整体对比总结",
    "comparisons": [
        {{
            "user": "用户名",
            "style": "投资风格",
            "strengths": ["优势1", "优势2"],
            "weaknesses": ["不足1", "不足2"]
        }}
    ],
    "similarities": ["共同点1", "共同点2"],
    "differences": ["差异点1", "差异点2"],
    "recommendations": "对不同用户的建议"
}}"""
                }
            ]
            
            logger.info(f"开始对比分析 {len(users_data)} 位用户")
            
            # 调用 AI (异步)
            response = await self._call_api(messages)
            if not response:
                return None
            
            # 解析 JSON
            try:
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    analysis_result = json.loads(json_str)
                else:
                    analysis_result = {"summary": response[:500]}
            except json.JSONDecodeError:
                analysis_result = {"summary": response[:500]}
            
            # 保存报告（使用第一个用户作为 target_id，标记为对比类型）
            report = AIAnalysisReport(
                target_id=target_ids[0],
                analysis_type='compare',
                time_range=time_range,
                start_date=start_date.strftime('%Y-%m-%d'),
                end_date=end_date.strftime('%Y-%m-%d'),
                report_content=json.dumps(analysis_result, ensure_ascii=False),
                summary=analysis_result.get('summary', '')[:500],
                style_tags=json.dumps([f"对比{len(users_data)}人"], ensure_ascii=False),
                keywords=json.dumps([u['name'] for u in users_data], ensure_ascii=False),
                sentiment_score=0
            )
            db.add(report)
            db.commit()
            db.refresh(report)
            
            return {
                'report_id': report.id,
                'users': [u['name'] for u in users_data],
                'time_range': time_range,
                'analysis': analysis_result,
                'created_at': report.created_at.isoformat()
            }
            
        finally:
            db.close()
    
    def _get_date_range(self, time_range: str) -> tuple:
        """获取日期范围"""
        end_date = datetime.now(timezone.utc)
        
        if time_range == 'week':
            start_date = end_date - timedelta(days=7)
        elif time_range == 'month':
            start_date = end_date - timedelta(days=30)
        else:  # all
            start_date = end_date - timedelta(days=365*10)  # 10 年
        
        return start_date, end_date
    
    def _prepare_analysis_text(self, replies: List[ReplyArchive], user_name: str) -> str:
        """准备分析文本"""
        text = f"用户: {user_name}\n回复数量: {len(replies)}\n\n"
        
        for i, reply in enumerate(replies[:20]):  # 最多取 20 条
            text += f"\n--- 回复 {i+1} ---\n"
            text += f"时间: {reply.post_date}\n"
            text += f"主题: {reply.topic_title}\n"
            text += f"内容: {reply.main_content[:500]}\n"  # 限制单条长度
        
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


# 便捷函数 (异步)
async def analyze_user(target_id: int, time_range: str = 'week') -> Optional[Dict]:
    """便捷函数：分析单个用户"""
    analyzer = AIAnalyzer()
    return await analyzer.analyze_user_style(target_id, time_range)


async def compare_users(target_ids: List[int], time_range: str = 'week') -> Optional[Dict]:
    """便捷函数：对比多个用户"""
    analyzer = AIAnalyzer()
    return await analyzer.compare_users(target_ids, time_range)
