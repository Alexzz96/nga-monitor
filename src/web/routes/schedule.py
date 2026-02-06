"""
调度规则路由
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.models import get_db, ScheduleRule
from schedule_manager import ScheduleManager

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


@router.get("/rules")
async def get_schedule_rules(db: Session = Depends(get_db)):
    """获取所有调度规则"""
    rules = db.query(ScheduleRule).order_by(ScheduleRule.priority.desc()).all()
    return {"rules": [r.to_dict() for r in rules]}


@router.post("/rules")
async def create_schedule_rule(data: dict, db: Session = Depends(get_db)):
    """创建调度规则"""
    name = data.get('name', '').strip()
    start_time = data.get('start_time', '').strip()
    end_time = data.get('end_time', '').strip()
    
    if not name:
        raise HTTPException(status_code=400, detail="规则名称不能为空")
    if not start_time or not end_time:
        raise HTTPException(status_code=400, detail="开始时间和结束时间不能为空")
    
    # 验证时间格式
    try:
        datetime.strptime(start_time, '%H:%M')
        datetime.strptime(end_time, '%H:%M')
    except ValueError:
        raise HTTPException(status_code=400, detail="时间格式错误，请使用 HH:MM 格式")
    
    rule = ScheduleRule(
        name=name,
        start_time=start_time,
        end_time=end_time,
        interval_seconds=data.get('interval_seconds', 60),
        is_summary=data.get('is_summary', False),
        enabled=data.get('enabled', True),
        priority=data.get('priority', 0)
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {"success": True, "rule": rule.to_dict()}


@router.put("/rules/{rule_id}")
async def update_schedule_rule(rule_id: int, data: dict, db: Session = Depends(get_db)):
    """更新调度规则"""
    rule = db.query(ScheduleRule).filter(ScheduleRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    
    if 'name' in data:
        rule.name = data['name']
    if 'start_time' in data:
        try:
            datetime.strptime(data['start_time'], '%H:%M')
            rule.start_time = data['start_time']
        except ValueError:
            raise HTTPException(status_code=400, detail="开始时间格式错误")
    if 'end_time' in data:
        try:
            datetime.strptime(data['end_time'], '%H:%M')
            rule.end_time = data['end_time']
        except ValueError:
            raise HTTPException(status_code=400, detail="结束时间格式错误")
    if 'interval_seconds' in data:
        rule.interval_seconds = data['interval_seconds']
    if 'is_summary' in data:
        rule.is_summary = data['is_summary']
    if 'enabled' in data:
        rule.enabled = data['enabled']
    if 'priority' in data:
        rule.priority = data['priority']
    
    db.commit()
    db.refresh(rule)
    return {"success": True, "rule": rule.to_dict()}


@router.delete("/rules/{rule_id}")
async def delete_schedule_rule(rule_id: int, db: Session = Depends(get_db)):
    """删除调度规则"""
    rule = db.query(ScheduleRule).filter(ScheduleRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    
    db.delete(rule)
    db.commit()
    return {"success": True}


@router.get("/status")
async def get_schedule_status():
    """获取当前调度状态"""
    manager = ScheduleManager()
    return manager.get_current_status()
