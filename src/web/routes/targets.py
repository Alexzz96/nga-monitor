"""
监控目标路由
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.models import get_db, MonitorTarget, SentRecord
from monitor import check_and_send

router = APIRouter(prefix="/api/targets", tags=["targets"])


@router.get("/")
async def get_targets(db: Session = Depends(get_db)):
    """获取所有监控目标"""
    targets = db.query(MonitorTarget).all()
    return {"targets": [t.to_dict() for t in targets]}


@router.post("/")
async def create_target(data: dict, db: Session = Depends(get_db)):
    """创建监控目标"""
    uid = data.get('uid', '').strip()
    if not uid:
        raise HTTPException(status_code=400, detail="UID 不能为空")
    
    # 检查是否已存在
    existing = db.query(MonitorTarget).filter(MonitorTarget.uid == uid).first()
    if existing:
        raise HTTPException(status_code=400, detail="该 UID 已存在")
    
    target = MonitorTarget(
        uid=uid,
        name=data.get('name', f'用户 {uid}'),
        url=f'https://nga.178.com/thread.php?searchpost=1&authorid={uid}',
        enabled=data.get('enabled', True),
        check_interval=data.get('check_interval', 60)
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return {"success": True, "target": target.to_dict()}


@router.put("/{target_id}")
async def update_target(target_id: int, data: dict, db: Session = Depends(get_db)):
    """更新监控目标"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    if 'name' in data:
        target.name = data['name']
    if 'enabled' in data:
        target.enabled = data['enabled']
    if 'check_interval' in data:
        target.check_interval = data['check_interval']
    
    db.commit()
    db.refresh(target)
    return {"success": True, "target": target.to_dict()}


@router.delete("/{target_id}")
async def delete_target(target_id: int, db: Session = Depends(get_db)):
    """删除监控目标"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    db.delete(target)
    db.commit()
    return {"success": True}


@router.post("/{target_id}/test")
async def test_target(target_id: int, force: bool = False, db: Session = Depends(get_db)):
    """测试单个监控目标"""
    import os
    
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    storage_path = os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json')
    if not os.path.exists(storage_path):
        raise HTTPException(status_code=400, detail="Storage state 文件不存在")
    
    result = await check_and_send(target_id, force=force)
    
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result["message"])


@router.post("/{target_id}/force-send")
async def force_send_target(target_id: int, db: Session = Depends(get_db)):
    """强制发送最新回复"""
    return await test_target(target_id, force=True, db=db)


@router.get("/{target_id}/stats")
async def get_target_stats(target_id: int, db: Session = Depends(get_db)):
    """获取目标统计信息"""
    from sqlalchemy import func
    
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 发送统计
    sent_count = db.query(SentRecord).filter(SentRecord.target_id == target_id).count()
    success_count = db.query(SentRecord).filter(
        SentRecord.target_id == target_id,
        SentRecord.success == True
    ).count()
    
    return {
        "target": target.to_dict(),
        "sent_count": sent_count,
        "success_count": success_count,
        "success_rate": round(success_count / sent_count * 100, 1) if sent_count > 0 else 0
    }
