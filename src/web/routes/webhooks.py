"""
Webhook 路由 - 支持多个 webhook 管理
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.models import get_db, Webhook, Config
from discord_sender import DiscordSender

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.get("/")
async def list_webhooks(db: Session = Depends(get_db)):
    """获取所有 webhook 列表"""
    webhooks = db.query(Webhook).order_by(Webhook.created_at.desc()).all()
    return {"webhooks": [w.to_dict() for w in webhooks]}


@router.post("/")
async def create_webhook(data: dict, db: Session = Depends(get_db)):
    """添加新的 webhook"""
    name = data.get('name', '').strip()
    url = data.get('url', '').strip()
    is_default = data.get('is_default', False)
    
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    # 解析 URL
    if url.isdigit():
        # 纯数字，尝试从已保存的 webhook 获取 token
        existing = db.query(Webhook).filter(Webhook.enabled == True).first()
        if existing:
            parts = existing.url.split('/')
            if len(parts) >= 7:
                token = parts[-1]
                url = f"https://discord.com/api/webhooks/{url}/{token}"
        else:
            raise HTTPException(status_code=400, detail="首次添加需要提供完整 URL")
    
    # 验证 URL 格式
    if 'discord.com/api/webhooks' in url:
        parts = url.split('/')
        if len(parts) < 7 or not parts[-2].isdigit():
            raise HTTPException(status_code=400, detail="Discord Webhook URL 格式不正确")
    
    # 如果设为默认，取消其他默认
    if is_default:
        db.query(Webhook).filter(Webhook.is_default == True).update({"is_default": False})
    
    webhook = Webhook(
        name=name,
        url=url,
        is_default=is_default,
        enabled=True
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    
    # 兼容旧版 - 同时更新 Config
    Config.set_webhook(db, url)
    
    return {"success": True, "webhook": webhook.to_dict()}


@router.put("/{webhook_id}")
async def update_webhook(webhook_id: int, data: dict, db: Session = Depends(get_db)):
    """更新 webhook"""
    webhook = db.query(Webhook).filter(Webhook.id == webhook_id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook 不存在")
    
    if 'name' in data:
        webhook.name = data['name'].strip()
    if 'url' in data:
        url = data['url'].strip()
        if url:
            # 处理纯数字 ID
            if url.isdigit():
                parts = webhook.url.split('/')
                if len(parts) >= 7:
                    token = parts[-1]
                    url = f"https://discord.com/api/webhooks/{url}/{token}"
            webhook.url = url
    if 'is_default' in data:
        if data['is_default']:
            db.query(Webhook).filter(Webhook.is_default == True).update({"is_default": False})
        webhook.is_default = data['is_default']
    if 'enabled' in data:
        webhook.enabled = data['enabled']
    
    db.commit()
    db.refresh(webhook)
    
    # 如果设为默认，更新 Config
    if webhook.is_default:
        Config.set_webhook(db, webhook.url)
    
    return {"success": True, "webhook": webhook.to_dict()}


@router.delete("/{webhook_id}")
async def delete_webhook(webhook_id: int, db: Session = Depends(get_db)):
    """删除 webhook"""
    webhook = db.query(Webhook).filter(Webhook.id == webhook_id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook 不存在")
    
    db.delete(webhook)
    db.commit()
    
    # 如果删除的是默认 webhook，重新设置一个默认
    if webhook.is_default:
        new_default = db.query(Webhook).filter(Webhook.enabled == True).first()
        if new_default:
            new_default.is_default = True
            Config.set_webhook(db, new_default.url)
            db.commit()
    
    return {"success": True}


@router.post("/{webhook_id}/test")
async def test_webhook(webhook_id: int, db: Session = Depends(get_db)):
    """测试指定 webhook"""
    from datetime import datetime, timezone
    
    webhook = db.query(Webhook).filter(Webhook.id == webhook_id).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook 不存在")
    
    sender = DiscordSender(webhook.url)
    test_data = {
        "topic_title": "[测试] Webhook 连接测试",
        "url": "https://nga.178.com",
        "forum": "[测试版块]",
        "post_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "main_content": f"这是一条测试消息，验证 Webhook \"{webhook.name}\" 配置是否正确。",
        "quote_content": "",
        "content_full": f"这是一条测试消息，验证 Webhook \"{webhook.name}\" 配置是否正确。",
        "images": [],
        "tid": "test",
        "pid": "test",
        "target_name": webhook.name
    }
    
    success = await sender.send_reply(test_data)
    if success:
        return {"success": True, "message": f"测试消息已发送到 {webhook.name}"}
    else:
        raise HTTPException(status_code=500, detail="发送失败")


@router.post("/test-default")
async def test_default_webhook(db: Session = Depends(get_db)):
    """测试默认 webhook（兼容旧版）"""
    webhook = db.query(Webhook).filter(Webhook.is_default == True, Webhook.enabled == True).first()
    if not webhook:
        # 尝试获取任意启用的 webhook
        webhook = db.query(Webhook).filter(Webhook.enabled == True).first()
    
    if not webhook:
        # 回退到旧版 Config
        url = Config.get_webhook(db)
        if not url:
            raise HTTPException(status_code=400, detail="没有配置 Webhook")
        
        sender = DiscordSender(url)
        test_data = {
            "topic_title": "[测试] Webhook 连接测试",
            "url": "https://nga.178.com",
            "forum": "[测试版块]",
            "post_date": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "main_content": "这是一条测试消息，验证 Webhook 配置是否正确。",
            "quote_content": "",
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
    
    return await test_webhook(webhook.id, db)
