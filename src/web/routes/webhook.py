"""
Webhook 路由 - 兼容层，重定向到 webhooks.py
旧版 API 保留用于兼容性
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.models import get_db
from .webhooks import router as new_router

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


@router.get("/")
async def get_webhook_compat(db: Session = Depends(get_db)):
    """获取默认 webhook URL (兼容旧版)"""
    from db.models import Webhook
    webhook = db.query(Webhook).filter(Webhook.is_default == True, Webhook.enabled == True).first()
    if webhook:
        return {"webhook": webhook.url}
    return {"webhook": None}


@router.post("/")
async def update_webhook_compat(data: dict, db: Session = Depends(get_db)):
    """更新 webhook (兼容旧版，重定向到新API)"""
    # 转发到新API
    from .webhooks import create_webhook, update_webhook
    
    url = data.get('url', '').strip()
    if not url:
        raise HTTPException(status_code=400, detail="请输入内容")
    
    # 检查是否已有默认 webhook
    from db.models import Webhook
    existing = db.query(Webhook).filter(Webhook.is_default == True).first()
    
    if existing:
        # 更新现有
        return await update_webhook(existing.id, {"url": url}, db)
    else:
        # 创建新的
        return await create_webhook({"name": "默认", "url": url, "is_default": True}, db)


@router.post("/test")
async def test_webhook_compat(db: Session = Depends(get_db)):
    """测试默认 webhook (兼容旧版)"""
    from .webhooks import test_default_webhook
    return await test_default_webhook(db)
