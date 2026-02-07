"""
可视化分析路由 - 情绪趋势和数据可视化
"""
import json
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from db.models import get_db, ReplyArchive, SentimentAnalysis, MonitorTarget

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/sentiment/trend")
async def get_sentiment_trend(
    target_id: int = None,
    days: int = Query(default=30, ge=7, le=90),
    db: Session = Depends(get_db)
):
    """
    获取情绪趋势数据
    
    Args:
        target_id: 用户ID，不传则返回汇总数据
        days: 天数范围 (7-90)
    
    Returns:
        {
            "dates": ["2026-02-01", ...],
            "series": [
                {
                    "name": "海伯利安之歌",
                    "data": [0.2, 0.5, -0.1, ...],
                    "reply_counts": [5, 8, 3, ...]
                }
            ]
        }
    """
    # 计算日期范围
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    # 生成日期列表
    date_list = []
    current = start_date
    while current <= end_date:
        date_list.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    
    result = {
        "dates": date_list,
        "series": []
    }
    
    if target_id:
        # 单个用户
        target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
        if not target:
            return result
        
        series_data = await _get_target_sentiment_series(db, target_id, date_list)
        series_data["name"] = target.name or f"用户{target.uid}"
        result["series"].append(series_data)
        
    else:
        # 所有用户 + 汇总
        targets = db.query(MonitorTarget).all()
        
        # 汇总数据
        summary_data = await _get_summary_sentiment_series(db, date_list)
        summary_data["name"] = "所有用户平均"
        summary_data["type"] = "average"
        result["series"].append(summary_data)
        
        # 每个用户的数据
        for target in targets:
            series_data = await _get_target_sentiment_series(db, target.id, date_list)
            series_data["name"] = target.name or f"用户{target.uid}"
            result["series"].append(series_data)
    
    return result


async def _get_target_sentiment_series(db: Session, target_id: int, date_list: List[str]) -> dict:
    """获取单个用户的情绪序列数据"""
    # 从汇总表查询
    summaries = db.query(SentimentAnalysis).filter(
        SentimentAnalysis.target_id == target_id,
        SentimentAnalysis.date.in_(date_list)
    ).all()
    
    # 构建日期到数据的映射
    date_data = {s.date: s for s in summaries}
    
    sentiment_scores = []
    reply_counts = []
    
    for date in date_list:
        if date in date_data:
            s = date_data[date]
            sentiment_scores.append(s.sentiment_index)
            reply_counts.append(s.total_replies)
        else:
            # 无数据
            sentiment_scores.append(None)
            reply_counts.append(0)
    
    return {
        "data": sentiment_scores,
        "reply_counts": reply_counts
    }


async def _get_summary_sentiment_series(db: Session, date_list: List[str]) -> dict:
    """获取所有用户的汇总情绪序列"""
    # 按日期汇总
    results = db.query(
        SentimentAnalysis.date,
        func.avg(SentimentAnalysis.sentiment_index).label('avg_index'),
        func.sum(SentimentAnalysis.total_replies).label('total_replies')
    ).filter(
        SentimentAnalysis.date.in_(date_list)
    ).group_by(SentimentAnalysis.date).all()
    
    date_data = {r.date: r for r in results}
    
    sentiment_scores = []
    reply_counts = []
    
    for date in date_list:
        if date in date_data:
            r = date_data[date]
            sentiment_scores.append(round(float(r.avg_index), 2) if r.avg_index else 0)
            reply_counts.append(int(r.total_replies) if r.total_replies else 0)
        else:
            sentiment_scores.append(None)
            reply_counts.append(0)
    
    return {
        "data": sentiment_scores,
        "reply_counts": reply_counts
    }


@router.get("/sentiment/distribution")
async def get_sentiment_distribution(
    target_id: int = None,
    days: int = Query(default=30, ge=1, le=90),
    db: Session = Depends(get_db)
):
    """
    获取情绪分布统计
    
    Returns:
        {
            "positive": 35,
            "neutral": 45,
            "negative": 20,
            "total": 100
        }
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    
    query = db.query(ReplyArchive).filter(
        ReplyArchive.sentiment.isnot(None),
        ReplyArchive.created_at >= since
    )
    
    if target_id:
        query = query.filter(ReplyArchive.target_id == target_id)
    
    replies = query.all()
    
    positive = sum(1 for r in replies if r.sentiment == 'positive')
    negative = sum(1 for r in replies if r.sentiment == 'negative')
    neutral = sum(1 for r in replies if r.sentiment == 'neutral')
    total = len(replies)
    
    return {
        "positive": positive,
        "neutral": neutral,
        "negative": negative,
        "total": total,
        "positive_rate": round(positive / total * 100, 1) if total > 0 else 0,
        "negative_rate": round(negative / total * 100, 1) if total > 0 else 0
    }


@router.get("/activity/heatmap")
async def get_activity_heatmap(
    target_id: int = None,
    days: int = Query(default=30, ge=7, le=90),
    db: Session = Depends(get_db)
):
    """
    获取活跃度热力图数据
    
    Returns:
        {
            "dates": ["2026-02-01", ...],
            "hours": [0, 1, 2, ..., 23],
            "data": [[count, ...], ...]  // 24小时 x 天数 的矩阵
        }
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    
    query = db.query(
        ReplyArchive
    ).filter(
        ReplyArchive.created_at >= since
    )
    
    if target_id:
        query = query.filter(ReplyArchive.target_id == target_id)
    
    replies = query.all()
    
    # 初始化热力图数据 [hour][day_index]
    hour_day_counts = [[0] * days for _ in range(24)]
    
    end_date = datetime.now(timezone.utc)
    
    for reply in replies:
        if reply.created_at:
            # 计算日期索引
            day_diff = (end_date.date() - reply.created_at.date()).days
            if 0 <= day_diff < days:
                day_index = days - 1 - day_diff  # 倒序，最新日期在最后
                hour = reply.created_at.hour
                hour_day_counts[hour][day_index] += 1
    
    # 生成日期列表
    date_list = []
    for i in range(days - 1, -1, -1):
        d = end_date - timedelta(days=i)
        date_list.append(d.strftime('%Y-%m-%d'))
    
    return {
        "dates": date_list,
        "hours": list(range(24)),
        "data": hour_day_counts
    }


@router.get("/keywords/sentiment")
async def get_keyword_sentiment(
    target_id: int = None,
    days: int = Query(default=30, ge=1, le=90),
    top_n: int = Query(default=10, ge=5, le=20),
    db: Session = Depends(get_db)
):
    """
    获取关键词情绪排行
    
    Returns:
        {
            "keywords": [
                {"word": "股票", "count": 15, "avg_sentiment": 0.6},
                {"word": "基金", "count": 10, "avg_sentiment": 0.2},
                ...
            ]
        }
    """
    # 从回复中提取关键词和情绪
    since = datetime.now(timezone.utc) - timedelta(days=days)
    
    query = db.query(ReplyArchive).filter(
        ReplyArchive.sentiment.isnot(None),
        ReplyArchive.created_at >= since
    )
    
    if target_id:
        query = query.filter(ReplyArchive.target_id == target_id)
    
    replies = query.all()
    
    # 统计关键词情绪
    keyword_stats = {}
    
    for reply in replies:
        if reply.main_content:
            # 简单分词（基于常见投资词汇）
            words = _extract_keywords(reply.main_content)
            
            for word in words:
                if word not in keyword_stats:
                    keyword_stats[word] = {
                        "count": 0,
                        "sentiment_sum": 0,
                        "sentiment_scores": []
                    }
                
                keyword_stats[word]["count"] += 1
                score = reply.sentiment_score or 0
                keyword_stats[word]["sentiment_sum"] += score
                keyword_stats[word]["sentiment_scores"].append(score)
    
    # 计算平均情绪并排序
    keywords = []
    for word, stats in keyword_stats.items():
        if stats["count"] >= 3:  # 至少出现3次
            avg_sentiment = stats["sentiment_sum"] / stats["count"]
            keywords.append({
                "word": word,
                "count": stats["count"],
                "avg_sentiment": round(avg_sentiment, 2)
            })
    
    # 按出现次数排序，取前 N
    keywords.sort(key=lambda x: x["count"], reverse=True)
    
    return {
        "keywords": keywords[:top_n]
    }


def _extract_keywords(text: str) -> List[str]:
    """从文本中提取投资相关关键词"""
    import re
    
    # 常见投资关键词列表
    investment_keywords = [
        "股票", "基金", "债券", "期货", "期权", "外汇", "黄金", "白银", "比特币",
        "以太坊", "加密货币", "A股", "港股", "美股", "沪深", "创业板", "科创板",
        "茅台", "腾讯", "阿里", " Tesla", "苹果", "微软", "英伟达",
        "牛市", "熊市", "涨停", "跌停", "大涨", "大跌", "反弹", "回调",
        "抄底", "逃顶", "加仓", "减仓", "止盈", "止损", "套牢", "解套",
        "市盈率", "市净率", "ROE", "分红", "股息", "财报", "年报", "季报",
        "美联储", "加息", "降息", "CPI", "PPI", "GDP", "通胀", "通缩",
        "人民币", "美元", "欧元", "日元", "汇率", "原油", "天然气"
    ]
    
    found = []
    for keyword in investment_keywords:
        if keyword in text:
            found.append(keyword)
    
    return found


@router.get("/summary")
async def get_analytics_summary(
    days: int = Query(default=30, ge=1, le=90),
    db: Session = Depends(get_db)
):
    """
    获取汇总统计信息
    
    Returns:
        {
            "total_replies": 100,
            "analyzed_replies": 80,
            "avg_sentiment": 0.15,
            "most_active_day": "2026-02-05",
            "most_positive_day": "2026-02-03",
            "most_negative_day": "2026-02-01"
        }
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    
    # 总回复数
    total_replies = db.query(ReplyArchive).filter(
        ReplyArchive.created_at >= since
    ).count()
    
    # 已分析回复
    analyzed = db.query(ReplyArchive).filter(
        ReplyArchive.sentiment.isnot(None),
        ReplyArchive.created_at >= since
    ).all()
    
    if not analyzed:
        return {
            "total_replies": total_replies,
            "analyzed_replies": 0,
            "avg_sentiment": 0,
            "most_active_day": None,
            "most_positive_day": None,
            "most_negative_day": None
        }
    
    # 平均情绪
    avg_sentiment = sum(r.sentiment_score or 0 for r in analyzed) / len(analyzed)
    
    # 按日期统计
    from collections import defaultdict
    daily_stats = defaultdict(lambda: {"count": 0, "sentiment_sum": 0})
    
    for r in analyzed:
        if r.created_at:
            date_str = r.created_at.strftime('%Y-%m-%d')
            daily_stats[date_str]["count"] += 1
            daily_stats[date_str]["sentiment_sum"] += r.sentiment_score or 0
    
    # 找出最活跃、最乐观、最悲观的日子
    most_active_day = max(daily_stats.items(), key=lambda x: x[1]["count"])[0]
    
    daily_avg_sentiment = {
        date: data["sentiment_sum"] / data["count"]
        for date, data in daily_stats.items()
        if data["count"] > 0
    }
    
    most_positive_day = max(daily_avg_sentiment.items(), key=lambda x: x[1])[0] if daily_avg_sentiment else None
    most_negative_day = min(daily_avg_sentiment.items(), key=lambda x: x[1])[0] if daily_avg_sentiment else None
    
    return {
        "total_replies": total_replies,
        "analyzed_replies": len(analyzed),
        "avg_sentiment": round(avg_sentiment, 2),
        "most_active_day": most_active_day,
        "most_positive_day": most_positive_day,
        "most_negative_day": most_negative_day
    }
