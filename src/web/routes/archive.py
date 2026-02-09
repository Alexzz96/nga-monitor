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


@router.post("/cleanup-user/{target_id}")
async def cleanup_user_archive(
    target_id: int,
    data: dict,
    db: Session = Depends(get_db)
):
    """删除指定用户的所有归档数据"""
    dry_run = data.get('dry_run', False)
    
    # 检查用户是否存在
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 统计要删除的记录
    user_records = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id)
    count = user_records.count()
    
    if dry_run:
        return {
            "dry_run": True,
            "target_id": target_id,
            "target_name": target.name,
            "would_delete": count
        }
    
    # 执行删除
    deleted = user_records.delete()
    db.commit()
    
    return {
        "success": True,
        "target_id": target_id,
        "target_name": target.name,
        "deleted": deleted
    }


@router.post("/cleanup-all")
async def cleanup_all_archive(
    data: dict,
    db: Session = Depends(get_db)
):
    """删除所有归档数据（危险操作）"""
    dry_run = data.get('dry_run', False)
    confirm = data.get('confirm', False)
    
    # 统计总数
    total_count = db.query(ReplyArchive).count()
    
    if dry_run:
        return {
            "dry_run": True,
            "would_delete": total_count,
            "warning": "这将删除所有回复归档数据，不可恢复！"
        }
    
    # 需要显式确认
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="危险操作！请设置 confirm=true 确认删除全部数据"
        )
    
    # 执行删除
    deleted = db.query(ReplyArchive).delete()
    db.commit()
    
    return {
        "success": True,
        "deleted": deleted,
        "message": "所有归档数据已删除"
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
    return {
        "target_id": target_id,
        "target_name": target.name,
        "export_time": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": [r.to_dict() for r in records]
    }


@router.post("/sync-time")
async def sync_post_time(
    background_tasks: BackgroundTasks,
    data: dict = None,
    db: Session = Depends(get_db)
):
    """
    同步所有回复的准确时间（后台任务）
    
    Args:
        target_id: 可选，只同步指定用户
        limit: 可选，限制处理数量（测试用）
    """
    target_id = data.get('target_id') if data else None
    limit = data.get('limit') if data else None
    
    # 启动后台任务
    background_tasks.add_task(_sync_time_task, target_id, limit)
    
    return {
        "success": True,
        "message": "时间同步任务已启动",
        "target_id": target_id,
        "limit": limit
    }


async def _sync_time_task(target_id: int = None, limit: int = None):
    """后台任务：同步时间"""
    import sys
    import asyncio
    sys.path.insert(0, '/app/src')
    
    from db.models import SessionLocal, ReplyArchive, MonitorTarget
    from nga_crawler import NgaCrawler
    from browser_pool import ManagedBrowserContext
    
    db = SessionLocal()
    
    try:
        # 构建查询
        query = db.query(ReplyArchive).filter(ReplyArchive.pid.isnot(None))
        
        if target_id:
            query = query.filter(ReplyArchive.target_id == target_id)
        
        if limit:
            query = query.limit(limit)
        
        replies = query.all()
        total = len(replies)
        
        logger.info(f"[SyncTime] 开始同步时间，共 {total} 条记录")
        
        updated = 0
        failed = 0
        
        # 使用 browser context 复用
        async with ManagedBrowserContext('/app/data/storage_state.json') as context:
            crawler = NgaCrawler('/app/data/storage_state.json')
            
            for i, reply in enumerate(replies):
                try:
                    # 获取准确时间
                    accurate_time = await crawler._get_accurate_post_time(
                        context, reply.tid, reply.pid
                    )
                    
                    if accurate_time:
                        # 更新数据库
                        old_time = reply.post_date
                        reply.post_date = accurate_time['post_date']
                        db.commit()
                        updated += 1
                        logger.info(f"[SyncTime] {i+1}/{total} 更新: {reply.pid} {old_time} -> {accurate_time['post_date']}")
                    else:
                        failed += 1
                        logger.warning(f"[SyncTime] {i+1}/{total} 失败: {reply.pid}")
                    
                    # 每10条休眠1秒，避免限流
                    if (i + 1) % 10 == 0:
                        await asyncio.sleep(1)
                        
                except Exception as e:
                    failed += 1
                    logger.error(f"[SyncTime] {i+1}/{total} 错误: {e}")
                    continue
        
        logger.info(f"[SyncTime] 完成: 更新 {updated} 条, 失败 {failed} 条")
        
    except Exception as e:
        logger.error(f"[SyncTime] 任务失败: {e}")
    finally:
        db.close()


@router.post("/sync-time")
async def sync_post_time(
    background_tasks: BackgroundTasks,
    data: dict = None,
    db: Session = Depends(get_db)
):
    """
    同步所有回复的准确时间（后台任务）
    
    Args:
        target_id: 可选，只同步指定用户
        limit: 可选，限制处理数量（测试用）
    """
    target_id = data.get('target_id') if data else None
    limit = data.get('limit') if data else None
    
    # 启动后台任务
    background_tasks.add_task(_sync_time_task, target_id, limit)
    
    return {
        "success": True,
        "message": "时间同步任务已启动",
        "target_id": target_id,
        "limit": limit
    }


async def _sync_time_task(target_id: int = None, limit: int = None):
    """后台任务：同步时间"""
    import sys
    import asyncio
    sys.path.insert(0, '/app/src')
    
    from db.models import SessionLocal, ReplyArchive, MonitorTarget
    from nga_crawler import NgaCrawler
    from browser_pool import ManagedBrowserContext
    
    db = SessionLocal()
    
    try:
        # 构建查询
        query = db.query(ReplyArchive).filter(ReplyArchive.pid.isnot(None))
        
        if target_id:
            query = query.filter(ReplyArchive.target_id == target_id)
        
        if limit:
            query = query.limit(limit)
        
        replies = query.all()
        total = len(replies)
        
        logger.info(f"[SyncTime] 开始同步时间，共 {total} 条记录")
        
        updated = 0
        failed = 0
        
        # 使用 browser context 复用
        async with ManagedBrowserContext('/app/data/storage_state.json') as context:
            crawler = NgaCrawler('/app/data/storage_state.json')
            
            for i, reply in enumerate(replies):
                try:
                    # 获取准确时间
                    accurate_time = await crawler._get_accurate_post_time(
                        context, reply.tid, reply.pid
                    )
                    
                    if accurate_time:
                        # 更新数据库
                        old_time = reply.post_date
                        reply.post_date = accurate_time['post_date']
                        db.commit()
                        updated += 1
                        logger.info(f"[SyncTime] {i+1}/{total} 更新: {reply.pid} {old_time} -> {accurate_time['post_date']}")
                    else:
                        failed += 1
                        logger.warning(f"[SyncTime] {i+1}/{total} 失败: {reply.pid}")
                    
                    # 每10条休眠1秒，避免限流
                    if (i + 1) % 10 == 0:
                        await asyncio.sleep(1)
                        
                except Exception as e:
                    failed += 1
                    logger.error(f"[SyncTime] {i+1}/{total} 错误: {e}")
                    continue
        
        logger.info(f"[SyncTime] 完成: 更新 {updated} 条, 失败 {failed} 条")
        
    except Exception as e:
        logger.error(f"[SyncTime] 任务失败: {e}")
    finally:
        db.close()



@router.post("/tasks/cleanup")
async def cleanup_stuck_tasks(
    data: dict = None,
    db: Session = Depends(get_db)
):
    """
    清理卡住的任务
    
    将长时间运行中的任务标记为失败
    """
    from datetime import datetime, timezone, timedelta
    
    max_minutes = data.get('max_minutes', 30) if data else 30
    dry_run = data.get('dry_run', False) if data else False
    
    # 计算截止时间
    cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=max_minutes)
    
    # 查找卡住的任务
    stuck_tasks = db.query(ArchiveTask).filter(
        ArchiveTask.status == 'running',
        ArchiveTask.started_at < cutoff_time
    ).all()
    
    stuck_count = len(stuck_tasks)
    
    if dry_run:
        return {
            "dry_run": True,
            "max_minutes": max_minutes,
            "would_cleanup": stuck_count,
            "tasks": [
                {
                    "id": t.id,
                    "target_name": t.target.name if t.target else None,
                    "started_at": t.started_at.isoformat(),
                    "running_minutes": round((datetime.now(timezone.utc) - t.started_at).total_seconds() / 60, 1)
                }
                for t in stuck_tasks
            ]
        }
    
    # 执行清理
    cleaned_count = 0
    for task in stuck_tasks:
        task.status = 'failed'
        task.error_message = f'任务执行超过{max_minutes}分钟，系统自动标记为失败'
        task.completed_at = datetime.now(timezone.utc)
        cleaned_count += 1
    
    db.commit()
    
    return {
        "success": True,
        "max_minutes": max_minutes,
        "cleaned_count": cleaned_count,
        "message": f"已清理 {cleaned_count} 个卡住的任务"
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_archive_task(
    task_id: int,
    db: Session = Depends(get_db)
):
    """
    手动取消任务
    """
    from datetime import datetime, timezone
    
    task = db.query(ArchiveTask).filter(ArchiveTask.id == task_id).first()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.status not in ['pending', 'running']:
        raise HTTPException(status_code=400, detail=f"任务状态为 {task.status}，无法取消")
    
    old_status = task.status
    task.status = 'failed'
    task.error_message = '用户手动取消'
    task.completed_at = datetime.now(timezone.utc)
    db.commit()
    
    return {
        "success": True,
        "task_id": task_id,
        "old_status": old_status,
        "new_status": "failed",
        "message": "任务已取消"
    }


@router.delete("/tasks/{task_id}")
async def delete_archive_task(
    task_id: int,
    db: Session = Depends(get_db)
):
    """
    删除任务记录
    
    Args:
        task_id: 任务ID
        
    Returns:
        操作结果
    """
    task = db.query(ArchiveTask).filter(ArchiveTask.id == task_id).first()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    db.delete(task)
    db.commit()
    
    return {
        "success": True,
        "task_id": task_id,
        "message": "任务已删除"
    }


@router.post("/tasks/cleanup-all")
async def cleanup_all_tasks(
    data: dict = None,
    db: Session = Depends(get_db)
):
    """
    批量清理任务
    
    Args:
        statuses: 要清理的状态列表 ["running", "completed", "failed"]
        
    Returns:
        清理结果
    """
    statuses = data.get('statuses', ['running', 'completed', 'failed']) if data else ['running', 'completed', 'failed']
    
    # 查找要删除的任务
    tasks_to_delete = db.query(ArchiveTask).filter(
        ArchiveTask.status.in_(statuses)
    ).all()
    
    deleted_count = len(tasks_to_delete)
    
    # 删除任务
    for task in tasks_to_delete:
        db.delete(task)
    
    db.commit()
    
    return {
        "success": True,
        "deleted_count": deleted_count,
        "statuses": statuses,
        "message": f"已删除 {deleted_count} 个任务"
    }

