#!/usr/bin/env python3
"""
批量情绪分析任务
用于分析已有回复的情绪
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/app/src')

from db.models import SessionLocal, ReplyArchive, init_db
from sentiment_analyzer import SentimentAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def analyze_pending_replies(batch_size: int = 10, limit: int = None):
    """
    分析未分析情绪的回复
    
    Args:
        batch_size: 每批分析数量（控制 API 调用频率）
        limit: 最大分析数量（None 表示无限制）
    """
    init_db()
    db = SessionLocal()
    
    try:
        # 查询未分析的回复
        query = db.query(ReplyArchive).filter(
            ReplyArchive.sentiment.is_(None)
        ).order_by(ReplyArchive.created_at.desc())
        
        if limit:
            query = query.limit(limit)
        
        pending_replies = query.all()
        
        if not pending_replies:
            logger.info("[SentimentTask] 没有需要分析的回复")
            return
        
        logger.info(f"[SentimentTask] 找到 {len(pending_replies)} 条待分析回复")
        
        # 初始化分析器
        try:
            analyzer = SentimentAnalyzer()
        except ValueError as e:
            logger.error(f"[SentimentTask] 初始化失败: {e}")
            return
        
        # 批量分析
        processed = 0
        success = 0
        failed = 0
        
        for i in range(0, len(pending_replies), batch_size):
            batch = pending_replies[i:i + batch_size]
            
            for reply in batch:
                try:
                    # 使用主内容进行分析
                    content = reply.main_content or reply.content_full or ""
                    
                    if len(content.strip()) < 10:
                        # 内容太短，标记为中性
                        reply.sentiment = 'neutral'
                        reply.sentiment_score = 0.0
                        reply.sentiment_analyzed_at = datetime.now(timezone.utc)
                        success += 1
                    else:
                        # AI 分析
                        result = await analyzer.analyze(content)
                        
                        reply.sentiment = result['sentiment']
                        reply.sentiment_score = result['score']
                        reply.sentiment_analyzed_at = datetime.now(timezone.utc)
                        success += 1
                    
                    processed += 1
                    
                except Exception as e:
                    logger.error(f"[SentimentTask] 分析回复 {reply.id} 失败: {e}")
                    failed += 1
            
            # 提交本批次
            db.commit()
            logger.info(f"[SentimentTask] 已处理 {processed}/{len(pending_replies)}")
            
            # 延迟避免限流
            if i + batch_size < len(pending_replies):
                await asyncio.sleep(2)
        
        logger.info(f"[SentimentTask] 完成: 成功 {success}, 失败 {failed}")
        
    except Exception as e:
        logger.error(f"[SentimentTask] 任务失败: {e}")
        db.rollback()
    finally:
        db.close()


async def analyze_recent_replies(days: int = 1):
    """分析最近 N 天的回复"""
    init_db()
    db = SessionLocal()
    
    try:
        # 计算日期范围
        since = datetime.now(timezone.utc) - timedelta(days=days)
        
        # 查询最近未分析的回复
        pending_replies = db.query(ReplyArchive).filter(
            ReplyArchive.sentiment.is_(None),
            ReplyArchive.created_at >= since
        ).all()
        
        if not pending_replies:
            logger.info(f"[SentimentTask] 最近 {days} 天没有待分析回复")
            return
        
        logger.info(f"[SentimentTask] 最近 {days} 天有 {len(pending_replies)} 条待分析回复")
        
        # 初始化分析器
        try:
            analyzer = SentimentAnalyzer()
        except ValueError as e:
            logger.error(f"[SentimentTask] 初始化失败: {e}")
            return
        
        # 分析（每批 5 条，避免限流）
        for i, reply in enumerate(pending_replies):
            try:
                content = reply.main_content or reply.content_full or ""
                
                if len(content.strip()) < 10:
                    reply.sentiment = 'neutral'
                    reply.sentiment_score = 0.0
                else:
                    result = await analyzer.analyze(content)
                    reply.sentiment = result['sentiment']
                    reply.sentiment_score = result['score']
                
                reply.sentiment_analyzed_at = datetime.now(timezone.utc)
                
                # 每 5 条提交一次
                if (i + 1) % 5 == 0:
                    db.commit()
                    logger.info(f"[SentimentTask] 已处理 {i + 1}/{len(pending_replies)}")
                    await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"[SentimentTask] 分析失败: {e}")
                continue
        
        db.commit()
        logger.info(f"[SentimentTask] 最近 {days} 天分析完成")
        
    except Exception as e:
        logger.error(f"[SentimentTask] 任务失败: {e}")
        db.rollback()
    finally:
        db.close()


async def generate_daily_sentiment_summary(date_str: str = None):
    """
    生成每日情绪汇总 - 优化版 (使用 GROUP BY 减少查询次数)
    
    Args:
        date_str: 日期字符串 YYYY-MM-DD，默认为昨天
    """
    from db.models import SentimentAnalysis, MonitorTarget
    from sqlalchemy import func, case
    
    init_db()
    db = SessionLocal()
    
    try:
        if date_str is None:
            # 默认昨天
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            date_str = yesterday.strftime('%Y-%m-%d')
        
        logger.info(f"[SentimentTask] 生成 {date_str} 情绪汇总 (优化版)")
        
        # 优化: 使用 GROUP BY 一次查询所有目标的统计数据 (替代循环中的多次查询)
        stats = db.query(
            ReplyArchive.target_id,
            func.count().label('total'),
            func.sum(case((ReplyArchive.sentiment == 'positive', 1), else_=0)).label('positive'),
            func.sum(case((ReplyArchive.sentiment == 'negative', 1), else_=0)).label('negative'),
            func.sum(case((ReplyArchive.sentiment == 'neutral', 1), else_=0)).label('neutral')
        ).filter(
            ReplyArchive.sentiment.isnot(None),  # 已分析
            ReplyArchive.created_at >= f"{date_str} 00:00:00",
            ReplyArchive.created_at < f"{date_str} 23:59:59"
        ).group_by(ReplyArchive.target_id).all()
        
        logger.info(f"[SentimentTask] 查询到 {len(stats)} 个目标的统计数据")
        
        # 批量查询现有的汇总记录
        existing_summaries = {
            s.target_id: s for s in db.query(SentimentAnalysis).filter(
                SentimentAnalysis.date == date_str
            ).all()
        }
        
        # 更新或创建汇总记录
        from sentiment_analyzer import calculate_sentiment_index
        for row in stats:
            target_id = row.target_id
            total = row.total or 0
            positive = row.positive or 0
            negative = row.negative or 0
            neutral = row.neutral or 0
            
            if total == 0:
                continue
            
            # 计算情绪指数
            index = calculate_sentiment_index(positive, neutral, negative)
            
            if target_id in existing_summaries:
                # 更新现有记录
                summary = existing_summaries[target_id]
                summary.total_replies = total
                summary.positive_count = positive
                summary.neutral_count = neutral
                summary.negative_count = negative
                summary.sentiment_index = index
                summary.updated_at = datetime.now(timezone.utc)
            else:
                # 创建新记录
                summary = SentimentAnalysis(
                    target_id=target_id,
                    date=date_str,
                    total_replies=total,
                    positive_count=positive,
                    neutral_count=neutral,
                    negative_count=negative,
                    sentiment_index=index,
                    keyword_sentiment='{}'
                )
                db.add(summary)
        
        db.commit()
        logger.info(f"[SentimentTask] {date_str} 情绪汇总生成完成, 共 {len(stats)} 个目标")
        
    except Exception as e:
        logger.error(f"[SentimentTask] 生成汇总失败: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="情绪分析任务")
    parser.add_argument("--pending", action="store_true", help="分析所有待处理回复")
    parser.add_argument("--recent", type=int, help="分析最近 N 天的回复")
    parser.add_argument("--summary", action="store_true", help="生成每日汇总")
    parser.add_argument("--date", type=str, help="指定日期 YYYY-MM-DD")
    parser.add_argument("--batch-size", type=int, default=10, help="批处理大小")
    parser.add_argument("--limit", type=int, help="最大处理数量")
    
    args = parser.parse_args()
    
    if args.pending:
        asyncio.run(analyze_pending_replies(args.batch_size, args.limit))
    elif args.recent:
        asyncio.run(analyze_recent_replies(args.recent))
    elif args.summary:
        asyncio.run(generate_daily_sentiment_summary(args.date))
    else:
        # 默认：分析最近1天的回复
        asyncio.run(analyze_recent_replies(1))
