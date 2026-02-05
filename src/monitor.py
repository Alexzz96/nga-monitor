#!/usr/bin/env python3
"""
监控任务模块 - 供 Web 和后台调度器共用
"""

import os
import logging
from datetime import datetime, timezone

from db.models import SessionLocal, MonitorTarget, SentRecord, Config
from nga_crawler import NgaCrawler
from discord_sender import DiscordSender

STORAGE_STATE_PATH = os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json')
DEBUG_MODE = os.getenv('DEBUG', 'false').lower() == 'true'
logger = logging.getLogger(__name__)

def get_webhook_from_db():
    """从数据库获取 webhook"""
    db = SessionLocal()
    try:
        return Config.get_webhook(db)
    finally:
        db.close()

async def check_and_send(target_id, force=False):
    """
    检查目标并发送新回复
    
    Args:
        target_id: 监控目标 ID
        force: 是否强制发送（即使已发送过）
        
    Returns:
        dict: 结果信息
    """
    db = SessionLocal()
    try:
        target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
        if not target:
            return {"success": False, "message": "目标不存在"}
        
        if not target.enabled and not force:
            return {"success": False, "message": "目标已禁用"}
        
        logger.info(f"开始检查用户 {target.uid}", extra={'target_uid': target.uid})
        
        sent_pids = {r.pid for r in db.query(SentRecord).filter(
            SentRecord.target_id == target.id
        ).all()}
        
        crawler = NgaCrawler(STORAGE_STATE_PATH)
        replies = await crawler.fetch_replies(target.url)
        
        if not replies:
            logger.info(f"用户 {target.uid}: 没有获取到回复", extra={'target_uid': target.uid})
            return {"success": True, "message": "没有获取到回复", "replies_count": 0}
        
        # 调试日志（仅 DEBUG 模式）
        if DEBUG_MODE:
            all_pids = [r.get('pid', 'N/A') for r in replies if r.get('pid')]
            logger.debug(f"[调试] 抓取到的 PID 列表: {all_pids}", extra={'target_uid': target.uid})
        
        replies.sort(key=lambda x: x.get('post_timestamp', 0), reverse=True)
        
        if DEBUG_MODE:
            sorted_pids = [(r.get('pid', 'N/A'), r.get('post_date', 'N/A')) for r in replies]
            logger.debug(f"[调试] 排序后 PID (带时间): {sorted_pids}", extra={'target_uid': target.uid})
            logger.debug(f"[调试] 已发送 PID (数据库): {list(sent_pids)}", extra={'target_uid': target.uid})
        
        logger.info(f"用户 {target.uid}: 获取到 {len(replies)} 条回复", extra={'target_uid': target.uid})
        
        if force:
            new_replies = [replies[0]]
            if DEBUG_MODE:
                logger.debug(f"[调试] 强制发送模式，选择 PID: {replies[0].get('pid')}", extra={'target_uid': target.uid})
        else:
            new_replies = [r for r in replies if r.get('pid') and r['pid'] not in sent_pids]
            if DEBUG_MODE:
                new_pids = [r.get('pid', 'N/A') for r in new_replies]
                logger.debug(f"[调试] 新回复 PID 列表: {new_pids}", extra={'target_uid': target.uid})
        
        if not new_replies:
            logger.info(f"用户 {target.uid}: 没有新回复", extra={'target_uid': target.uid})
            return {"success": True, "message": "没有新回复（都已发送过）", "replies_count": len(replies)}
        
        logger.info(f"用户 {target.uid}: 发现 {len(new_replies)} 条新回复", extra={'target_uid': target.uid})
        
        webhook = get_webhook_from_db()
        if not webhook:
            logger.error(f"Webhook 未配置", extra={'target_uid': target.uid})
            return {"success": False, "message": "Webhook 未配置"}
        
        latest = new_replies[0]
        latest['target_name'] = target.name or target.uid
        sender = DiscordSender(webhook)
        success = sender.send_reply(latest)
        
        record = SentRecord(
            target_id=target.id,
            pid=latest['pid'],
            tid=latest['tid'],
            topic_title=latest['topic_title'],
            content_preview=latest['content_full'][:500] if latest['content_full'] else '',
            success=success
        )
        db.add(record)
        db.commit()
        
        if success:
            logger.info(f"用户 {target.uid}: 发送成功 PID {latest['pid']}", extra={'target_uid': target.uid})
            return {
                "success": True,
                "message": f"已发送最新回复: {latest['topic_title'][:50]}...",
                "replies_count": len(replies),
                "pid": latest['pid'],
                "content": latest['content_full'][:200] if latest['content_full'] else ''
            }
        else:
            logger.error(f"用户 {target.uid}: 发送失败 PID {latest['pid']}", extra={'target_uid': target.uid})
            return {"success": False, "message": "发送失败"}
            
    except Exception as e:
        logger.error(f"检查用户 {target_id} 时出错: {e}", exc_info=True)
        return {"success": False, "message": str(e)}
    finally:
        db.close()

async def check_all_targets():
    """检查所有启用的目标"""
    logger.info("=" * 60)
    logger.info(f"开始定时检查 - {datetime.now(timezone.utc).isoformat()}")
    
    db = SessionLocal()
    results = []
    try:
        targets = db.query(MonitorTarget).filter(MonitorTarget.enabled == True).all()
        for target in targets:
            result = await check_and_send(target.id)
            results.append({"uid": target.uid, **result})
    finally:
        db.close()
    
    logger.info("定时检查完成")
    return results
