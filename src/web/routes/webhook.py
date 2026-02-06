"""
Webhook 路由
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.models import get_db, Config
from discord_sender import DiscordSender

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


@router.get("/")
async def get_webhook(db: Session = Depends(get_db)):
    """获取 webhook URL"""
    webhook = Config.get_webhook(db)
    return {"webhook": webhook}


@router.post("/")
async def update_webhook(data: dict, db: Session = Depends(get_db)):
    """更新 webhook URL"""
    url = data.get('url', '').strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    Config.set_webhook(db, url)
    return {"success": True}


@router.post("/test")
async def test_webhook(db: Session = Depends(get_db)):
    """测试 webhook"""
    from datetime import datetime, timezone
    
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
    
    success = await sender.send_reply(test_data)
    if success:
        return {"success": True, "message": "测试消息已发送"}
    else:
        raise HTTPException(status_code=500, detail="发送失败")
