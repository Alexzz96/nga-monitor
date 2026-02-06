"""
统计信息路由
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta

from db.models import get_db, MonitorTarget, SentRecord, SystemLog

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/")
async def get_stats(db: Session = Depends(get_db)):
    """获取详细统计信息"""
    from sqlalchemy import func
    
    targets_count = db.query(MonitorTarget).count()
    enabled_count = db.query(MonitorTarget).filter(MonitorTarget.enabled == True).count()
    total_sent = db.query(SentRecord).count()
    success_sent = db.query(SentRecord).filter(SentRecord.success == True).count()
    
    # 最近 24 小时发送数
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_sent = db.query(SentRecord).filter(SentRecord.sent_at >= day_ago).count()
    
    # 按目标统计发送数
    target_stats = db.query(
        MonitorTarget.id,
        MonitorTarget.name,
        MonitorTarget.uid,
        func.count(SentRecord.id).label('sent_count')
    ).outerjoin(
        SentRecord, MonitorTarget.id == SentRecord.target_id
    ).group_by(MonitorTarget.id).all()
    
    return {
        "targets": {"total": targets_count, "enabled": enabled_count},
        "sent": {
            "total": total_sent,
            "success": success_sent,
            "failed": total_sent - success_sent,
            "success_rate": round(success_sent / total_sent * 100, 1) if total_sent > 0 else 0,
            "recent_24h": recent_sent
        },
        "target_stats": [{
            "id": t.id,
            "name": t.name,
            "uid": t.uid,
            "sent_count": t.sent_count
        } for t in target_stats]
    }


@router.get("/logs")
async def get_logs(
    level: str = None,
    target_uid: str = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取日志"""
    query = db.query(SystemLog)
    
    if level:
        query = query.filter(SystemLog.level == level.upper())
    if target_uid:
        query = query.filter(SystemLog.target_uid == target_uid)
    
    logs = query.order_by(SystemLog.created_at.desc()).limit(limit).all()
    return {"logs": [log.to_dict() for log in logs]}


@router.post("/logs/cleanup")
async def cleanup_logs(days: int = 7, db: Session = Depends(get_db)):
    """清理旧日志"""
    from db.models import cleanup_old_logs
    deleted = cleanup_old_logs(days)
    return {"success": True, "deleted": deleted}


@router.get("/browser")
async def get_browser_stats():
    """获取浏览器连接池详细统计"""
    from browser_pool import BrowserPool
    
    pool = BrowserPool.get_instance()
    
    return {
        "initialized": pool.is_initialized,
        "stats": pool.get_stats()
    }


@router.get("/rate-limiter")
async def get_rate_limiter_stats():
    """获取限流器统计"""
    from rate_limiter import get_limiter_stats
    
    return get_limiter_stats()
