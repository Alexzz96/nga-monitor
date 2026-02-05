"""
Web API 模块
"""
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import (
    get_db, MonitorTarget, SentRecord, SystemLog, Config, ScheduleRule,
    init_db, cleanup_old_logs
)
from discord_sender import DiscordSender
from monitor import check_and_send
from schedule_manager import ScheduleManager

init_db()

app = FastAPI(title="NGA Monitor")
templates = Jinja2Templates(directory="/app/src/web/templates")

STORAGE_STATE_PATH = Path(os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json'))

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    """首页 - 监控目标列表"""
    targets = db.query(MonitorTarget).all()
    webhook = Config.get_webhook(db)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "targets": targets,
        "webhook": webhook[:50] + "..." if webhook else "未配置"
    })

@app.get("/api/targets")
async def get_targets(db: Session = Depends(get_db)):
    """获取所有监控目标"""
    targets = db.query(MonitorTarget).all()
    return {"targets": [t.to_dict() for t in targets]}

@app.post("/api/targets")
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

@app.put("/api/targets/{target_id}")
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

@app.delete("/api/targets/{target_id}")
async def delete_target(target_id: int, db: Session = Depends(get_db)):
    """删除监控目标"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    db.delete(target)
    db.commit()
    return {"success": True}

@app.get("/api/webhook")
async def get_webhook(db: Session = Depends(get_db)):
    """获取 webhook URL"""
    webhook = Config.get_webhook(db)
    return {"webhook": webhook}

@app.post("/api/webhook")
async def update_webhook(data: dict, db: Session = Depends(get_db)):
    """更新 webhook URL"""
    url = data.get('url', '').strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    Config.set_webhook(db, url)
    return {"success": True}

@app.post("/api/webhook/test")
async def test_webhook(db: Session = Depends(get_db)):
    """测试 webhook"""
    webhook = Config.get_webhook(db)
    if not webhook:
        raise HTTPException(status_code=400, detail="Webhook 未配置")
    
    sender = DiscordSender(webhook)
    test_data = {
        "topic_title": "[测试] Webhook 连接测试",
        "url": "https://nga.178.com",
        "forum": "[测试版块]",
        "post_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "content_full": "这是一条测试消息，验证 Webhook 配置是否正确。",
        "images": [],
        "tid": "test",
        "pid": "test"
    }
    
    success = sender.send_reply(test_data)
    if success:
        return {"success": True, "message": "测试消息已发送"}
    else:
        raise HTTPException(status_code=500, detail="发送失败")

@app.post("/api/targets/{target_id}/test")
async def test_target(target_id: int, force: bool = False, db: Session = Depends(get_db)):
    """
    测试单个监控目标
    
    Args:
        force: 是否强制发送（即使已发送过）
    """
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    storage_path = os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json')
    if not os.path.exists(storage_path):
        raise HTTPException(status_code=400, detail="Storage state 文件不存在")
    
    # 调用统一的监控逻辑
    result = await check_and_send(target_id, force=force)
    
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result["message"])

@app.post("/api/targets/{target_id}/force-send")
async def force_send_target(target_id: int, db: Session = Depends(get_db)):
    """强制发送最新回复（不管是否已发送过）"""
    return await test_target(target_id, force=True, db=db)

@app.get("/api/logs")
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

@app.post("/api/logs/cleanup")
async def cleanup_logs(days: int = 7, db: Session = Depends(get_db)):
    """清理旧日志"""
    deleted = cleanup_old_logs(days)
    return {"success": True, "deleted": deleted}

@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    """获取详细统计信息"""
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
    
    target_stats_list = [{
        "id": t.id,
        "name": t.name,
        "uid": t.uid,
        "sent_count": t.sent_count
    } for t in target_stats]
    
    return {
        "targets": {"total": targets_count, "enabled": enabled_count},
        "sent": {"total": total_sent, "success": success_sent, "recent_24h": recent_sent},
        "target_stats": target_stats_list
    }

@app.post("/api/parse-url")
async def parse_url(data: dict):
    """从 NGA URL 解析 UID"""
    url = data.get('url', '').strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    # 支持多种 URL 格式
    patterns = [
        r'authorid=(\d+)',  # thread.php?searchpost=1&authorid=xxx
        r'uid=(\d+)',       # nuke.php?func=ucp&uid=xxx
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            uid = match.group(1)
            return {"success": True, "uid": uid}
    
    raise HTTPException(status_code=400, detail="无法从 URL 解析 UID")

@app.get("/api/cookie-status")
async def get_cookie_status():
    """获取 Cookie 登录状态"""
    try:
        if not STORAGE_STATE_PATH.exists():
            return {
                "exists": False,
                "message": "Cookie 文件不存在"
            }
        
        with open(STORAGE_STATE_PATH, 'r') as f:
            state = json.load(f)
        
        cookies = state.get('cookies', [])
        
        # 查找关键 Cookie
        nga_cookies = {}
        for cookie in cookies:
            name = cookie.get('name', '')
            if 'nga' in name.lower() or name in ['ngaPassportUid', 'ngacn0comUserInfo', '_178c']:
                nga_cookies[name] = {
                    "expires": cookie.get('expires', 'N/A'),
                    "has_value": bool(cookie.get('value'))
                }
        
        return {
            "exists": True,
            "cookie_count": len(cookies),
            "nga_cookies": nga_cookies,
            "last_modified": datetime.fromtimestamp(
                STORAGE_STATE_PATH.stat().st_mtime
            ).isoformat()
        }
    except Exception as e:
        return {
            "exists": True,
            "error": str(e)
        }

# ========== 调度规则管理 ==========

@app.get("/api/schedule/rules")
async def get_schedule_rules(db: Session = Depends(get_db)):
    """获取所有调度规则"""
    rules = db.query(ScheduleRule).order_by(ScheduleRule.priority.desc()).all()
    return {"rules": [r.to_dict() for r in rules]}

@app.post("/api/schedule/rules")
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

@app.put("/api/schedule/rules/{rule_id}")
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

@app.delete("/api/schedule/rules/{rule_id}")
async def delete_schedule_rule(rule_id: int, db: Session = Depends(get_db)):
    """删除调度规则"""
    rule = db.query(ScheduleRule).filter(ScheduleRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    
    db.delete(rule)
    db.commit()
    return {"success": True}

@app.get("/api/schedule/status")
async def get_schedule_status():
    """获取当前调度状态"""
    manager = ScheduleManager()
    return manager.get_current_status()
