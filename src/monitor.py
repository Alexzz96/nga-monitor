#!/usr/bin/env python3
"""
监控任务模块 - 供 Web 和后台调度器共用
使用特定异常类型，细化错误处理
"""

import os
import re
import logging
from datetime import datetime, timezone

from db.models import SessionLocal, MonitorTarget, SentRecord, Config, ReplyArchive, ArchiveTask
from nga_crawler import NgaCrawler
from discord_sender import DiscordSender
from exceptions import (
    LoginExpiredError, NetworkError, ParseError, 
    RateLimitError, WebhookError, handle_exception
)

STORAGE_STATE_PATH = os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json')
DEBUG_MODE = os.getenv('DEBUG', 'false').lower() == 'true'
logger = logging.getLogger(__name__)


def check_keywords(content: str, keywords: str, mode: str = 'ANY') -> bool:
    """
    检查内容是否匹配关键词
    
    Args:
        content: 回复内容
        keywords: 关键词列表，逗号分隔
        mode: ANY(匹配任一), ALL(匹配全部), REGEX(正则匹配)
        
    Returns:
        bool: 是否匹配
    """
    if not keywords or not keywords.strip():
        return True  # 未设置关键词，全部通过
    
    if not content:
        content = ''
    
    keyword_list = [k.strip() for k in keywords.split(',') if k.strip()]
    
    if not keyword_list:
        return True
    
    content_lower = content.lower()
    
    if mode == 'REGEX':
        # 正则模式
        for pattern in keyword_list:
            try:
                if re.search(pattern, content, re.IGNORECASE):
                    return True
            except re.error:
                logger.warning(f"无效的正则表达式: {pattern}")
                continue
        return False
    
    elif mode == 'ALL':
        # 全部匹配模式
        for keyword in keyword_list:
            if keyword.lower() not in content_lower:
                return False
        return True
    
    else:  # ANY 模式（默认）
        # 任一匹配模式
        for keyword in keyword_list:
            if keyword.lower() in content_lower:
                return True
        return False


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
        
        try:
            replies = await crawler.fetch_replies(target.url)
        except LoginExpiredError as e:
            logger.error(f"登录过期: {e}", extra={'target_uid': target.uid})
            return {"success": False, "message": "NGA登录已过期，请重新导出storage state", "fatal": True}
        except RateLimitError as e:
            logger.warning(f"触发限流: {e}", extra={'target_uid': target.uid})
            return {"success": False, "message": "触发网站限流，稍后重试"}
        except NetworkError as e:
            logger.warning(f"网络错误: {e}", extra={'target_uid': target.uid})
            return {"success": False, "message": f"网络错误: {e}"}
        except ParseError as e:
            logger.warning(f"解析错误: {e}", extra={'target_uid': target.uid})
            return {"success": False, "message": f"页面解析失败: {e}"}
        
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
        
        # ===== 关键词过滤 =====
        if target.keywords and target.keywords.strip():
            filtered_replies = []
            for reply in new_replies:
                content = reply.get('content_full', '')
                topic_title = reply.get('topic_title', '')
                check_content = f"{topic_title} {content}"  # 标题+内容一起检查
                
                if check_keywords(check_content, target.keywords, target.keyword_mode or 'ANY'):
                    filtered_replies.append(reply)
                    logger.info(f"用户 {target.uid}: 关键词匹配 PID {reply.get('pid')}", extra={'target_uid': target.uid})
                else:
                    logger.debug(f"用户 {target.uid}: 关键词过滤跳过 PID {reply.get('pid')}", extra={'target_uid': target.uid})
            
            # 记录过滤结果
            if len(filtered_replies) < len(new_replies):
                logger.info(f"用户 {target.uid}: 关键词过滤 {len(new_replies)} -> {len(filtered_replies)} 条", extra={'target_uid': target.uid})
            
            new_replies = filtered_replies
        
        # 批量保存抓取到的回复到存档
        try:
            archived_count = await _bulk_archive_replies(db, target.id, replies)
            logger.debug(f"用户 {target.uid}: 已存档 {archived_count} 条回复", extra={'target_uid': target.uid})
        except Exception as e:
            logger.error(f"存档失败: {e}", extra={'target_uid': target.uid})
            # 存档失败不影响主流程
        
        if not new_replies:
            logger.info(f"用户 {target.uid}: 没有符合关键词的新回复", extra={'target_uid': target.uid})
            return {"success": True, "message": "没有符合关键词条件的新回复", "replies_count": len(replies), "filtered": True}
        
        logger.info(f"用户 {target.uid}: 发现 {len(new_replies)} 条符合关键词的新回复", extra={'target_uid': target.uid})
        
        webhook = get_webhook_from_db()
        if not webhook:
            logger.error(f"Webhook 未配置", extra={'target_uid': target.uid})
            return {"success": False, "message": "Webhook 未配置"}
        
        latest = new_replies[0]
        latest['target_name'] = target.name or target.uid
        sender = DiscordSender(webhook)
        
        try:
            success = await sender.send_reply(latest)
        except WebhookError as e:
            logger.error(f"Webhook 发送失败: {e}", extra={'target_uid': target.uid})
            success = False
        
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
        # 未预期的异常，记录详细信息
        logger.error(f"检查用户 {target_id} 时出错: {e}", exc_info=True)
        return {"success": False, "message": f"内部错误: {type(e).__name__}"}
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


async def archive_history_task(target_id: int, max_pages: int = 25):
    """
    后台任务：抓取用户历史回复并存档（带任务追踪）
    
    Args:
        target_id: 监控目标 ID
        max_pages: 最大抓取页数
    """
    db = SessionLocal()
    task = None
    
    try:
        # 检查是否有进行中的任务
        existing_task = db.query(ArchiveTask).filter(
            ArchiveTask.target_id == target_id,
            ArchiveTask.status == 'running'
        ).first()
        
        if existing_task:
            logger.warning(f"[Archive Task] 目标 {target_id} 已有进行中的归档任务")
            return
        
        # 创建任务记录
        task = ArchiveTask(
            target_id=target_id,
            status='running',
            total_pages=max_pages,
            completed_pages=0
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        
        logger.info(f"[Archive Task] 任务创建: ID={task.id}, target_id={target_id}, max_pages={max_pages}")
        
        target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
        if not target:
            logger.error(f"[Archive Task] 目标不存在: {target_id}")
            task.status = 'failed'
            task.error_message = '目标不存在'
            task.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        
        logger.info(f"[Archive Task] 目标用户: {target.name} ({target.uid})")
        
        # 抓取历史，带进度回调
        crawler = NgaCrawler(STORAGE_STATE_PATH)
        
        async def progress_callback(page_num, total_pages, replies_count):
            """实时更新进度到数据库"""
            task.completed_pages = page_num
            task.total_replies = replies_count
            db.commit()
            logger.info(f"[Archive Task] 进度更新: {page_num}/{total_pages} 页, {replies_count} 条回复")
        
        replies = await crawler.fetch_history(
            target.url, 
            max_pages=max_pages, 
            delay=2,
            progress_callback=progress_callback
        )
        
        task.total_replies = len(replies)
        db.commit()
        
        if not replies:
            logger.warning(f"[Archive Task] 未抓取到任何回复")
            task.status = 'completed'
            task.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        
        logger.info(f"[Archive Task] 抓取完成，共 {len(replies)} 条，开始存档...")
        
        # 更新最终抓取数量
        task.total_replies = len(replies)
        db.commit()
        
        # 使用批量插入存档到数据库
        archived_count, skipped_count = await _bulk_archive_replies_with_stats(db, target.id, replies)
        
        # 更新任务状态
        task.status = 'completed'
        task.completed_pages = max_pages
        task.archived_count = archived_count
        task.skipped_count = skipped_count
        task.completed_at = datetime.now(timezone.utc)
        db.commit()
        
        logger.info(f"[Archive Task] 历史存档完成!")
        logger.info(f"[Archive Task] 总计: {len(replies)} 条, 新增: {archived_count} 条, 跳过: {skipped_count} 条")
        
    except Exception as e:
        logger.error(f"[Archive Task] 抓取历史失败: {e}", exc_info=True)
        if task:
            task.status = 'failed'
            task.error_message = str(e)[:500]
            task.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


async def _bulk_archive_replies(db, target_id: int, replies: list, return_stats: bool = False):
    """
    批量存档回复到数据库（统一版本）
    
    Args:
        db: 数据库会话
        target_id: 目标用户ID
        replies: 回复列表
        return_stats: 是否返回统计信息
        
    Returns:
        int: 实际插入的新记录数（return_stats=False）
        tuple: (插入数量, 跳过数量)（return_stats=True）
    """
    if not replies:
        return (0, 0) if return_stats else 0
    
    try:
        # 1. 批量查询所有已存在的PID（单次查询）
        reply_pids = [r['pid'] for r in replies if r.get('pid')]
        if not reply_pids:
            logger.warning("[Bulk Archive] 没有有效的PID需要处理")
            return (0, 0) if return_stats else 0
        
        existing_pids = {
            row[0] for row in db.query(ReplyArchive.pid).filter(
                ReplyArchive.pid.in_(reply_pids)
            ).all()
        }
        
        # 2. 过滤出需要插入的新记录
        new_replies = [
            r for r in replies 
            if r.get('pid') and r['pid'] not in existing_pids
        ]
        
        skipped_count = len(replies) - len(new_replies)
        
        if not new_replies:
            log_msg = f"[Bulk Archive] 所有 {len(replies)} 条记录已存在，跳过插入"
            logger.info(log_msg) if return_stats else logger.debug(log_msg)
            return (0, skipped_count) if return_stats else 0
        
        # 3. 准备批量插入的数据映射
        archive_mappings = [
            {
                'target_id': target_id,
                'pid': r['pid'],
                'tid': r.get('tid', ''),
                'topic_title': r.get('topic_title', ''),
                'content_full': r.get('content_full', ''),
                'quote_content': r.get('quote_content', ''),
                'main_content': r.get('main_content', ''),
                'forum': r.get('forum', ''),
                'post_date': r.get('post_date', ''),
                'url': r.get('url', '')
            }
            for r in new_replies
        ]
        
        # 4. 使用SQLAlchemy批量插入
        db.bulk_insert_mappings(ReplyArchive, archive_mappings)
        db.commit()
        
        logger.info(f"[Bulk Archive] 批量插入完成: 新增 {len(new_replies)} 条, 跳过已存在 {skipped_count} 条")
        return (len(new_replies), skipped_count) if return_stats else len(new_replies)
        
    except Exception as e:
        db.rollback()
        logger.error(f"[Bulk Archive] 批量存档失败: {e}", exc_info=True)
        raise


# 向后兼容的别名
async def _bulk_archive_replies_with_stats(db, target_id: int, replies: list) -> tuple:
    """批量存档（返回统计信息）- 兼容旧代码"""
    return await _bulk_archive_replies(db, target_id, replies, return_stats=True)

