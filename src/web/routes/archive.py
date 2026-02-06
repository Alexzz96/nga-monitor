"""
数据归档路由
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta

from db.models import get_db, MonitorTarget, ReplyArchive, ArchiveTask
from monitor import archive_history_task

router = APIRouter(prefix="/api/archive", tags=["archive"])


@router.get("/history/{target_id}")
async def get_user_history(
    target_id: int,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """获取用户的历史回复列表（分页）"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 获取总数
    total = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id).count()
    
    # 分页查询
    records = db.query(ReplyArchive).filter(
        ReplyArchive.target_id == target_id
    ).order_by(ReplyArchive.post_date.desc()).offset((page - 1) * limit).limit(limit).all()
    
    return {
        "target_id": target_id,
        "target_name": target.name,
        "total": total,
        "page": page,
        "limit": limit,
        "records": [r.to_dict() for r in records]
    }


@router.post("/history/{target_id}")
async def archive_history(
    target_id: int,
    data: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """抓取用户历史回复并存档"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    max_pages = data.get('max_pages', 25)
    
    # 使用后台任务执行
    background_tasks.add_task(archive_history_task, target_id, max_pages)
    
    return {
        "success": True,
        "message": f"已开始抓取 {target.name} 的历史数据（{max_pages}页）",
        "target_id": target_id,
        "max_pages": max_pages
    }


@router.get("/status/{target_id}")
async def get_archive_status(target_id: int, db: Session = Depends(get_db)):
    """获取目标的历史归档状态"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 统计存档数量
    total_count = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id).count()
    
    # 获取最新和最早的存档
    latest = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id).order_by(
        ReplyArchive.created_at.desc()
    ).first()
    earliest = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id).order_by(
        ReplyArchive.created_at.asc()
    ).first()
    
    # 获取进行中的任务
    running_task = db.query(ArchiveTask).filter(
        ArchiveTask.target_id == target_id,
        ArchiveTask.status == 'running'
    ).first()
    
    return {
        "target_id": target_id,
        "target_name": target.name,
        "total_archived": total_count,
        "latest_post_date": latest.post_date if latest else None,
        "earliest_post_date": earliest.post_date if earliest else None,
        "running_task": running_task.to_dict() if running_task else None
    }


@router.get("/tasks")
async def get_archive_tasks(
    target_id: int = None,
    status: str = None,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    """获取归档任务列表"""
    query = db.query(ArchiveTask)
    
    if target_id:
        query = query.filter(ArchiveTask.target_id == target_id)
    if status:
        query = query.filter(ArchiveTask.status == status)
    
    tasks = query.order_by(ArchiveTask.started_at.desc()).limit(limit).all()
    
    return {
        "tasks": [t.to_dict() for t in tasks],
        "count": len(tasks)
    }


@router.get("/stats")
async def get_archive_overall_stats(db: Session = Depends(get_db)):
    """获取归档总体统计"""
    from sqlalchemy import func
    import os
    
    # 总存档数
    total_archived = db.query(ReplyArchive).count()
    
    # 数据库文件大小
    db_path = os.getenv('DB_PATH', '/app/data/nga_monitor.db')
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    
    # 每个用户的存档统计
    user_stats = db.query(
        MonitorTarget.id,
        MonitorTarget.name,
        func.count(ReplyArchive.id).label('count')
    ).outerjoin(
        ReplyArchive, MonitorTarget.id == ReplyArchive.target_id
    ).group_by(MonitorTarget.id).all()
    
    return {
        "total_archived": total_archived,
        "db_size": db_size,
        "user_stats": [{"id": u.id, "name": u.name, "count": u.count} for u in user_stats]
    }


@router.post("/cleanup")
async def cleanup_archive(data: dict, db: Session = Depends(get_db)):
    """清理旧归档数据"""
    days = data.get('days', 90)
    dry_run = data.get('dry_run', False)
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    # 统计要删除的记录
    old_records = db.query(ReplyArchive).filter(ReplyArchive.created_at < cutoff)
    count = old_records.count()
    
    if dry_run:
        return {
            "dry_run": True,
            "would_delete": count,
            "cutoff_date": cutoff.isoformat()
        }
    
    # 执行删除
    deleted = old_records.delete()
    db.commit()
    
    return {
        "deleted": deleted,
        "cutoff_date": cutoff.isoformat()
    }


@router.post("/export/{target_id}")
async def export_archive(
    target_id: int,
    data: dict,
    db: Session = Depends(get_db)
):
    """导出归档数据为 JSON"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    days = data.get('days')
    
    query = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id)
    
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(ReplyArchive.created_at >= cutoff)
    
    records = query.order_by(ReplyArchive.post_date.desc()).all()
    
    return {
        "target_id": target_id,
        "target_name": target.name,
        "export_time": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": [r.to_dict() for r in records]
    }
