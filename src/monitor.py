#!/usr/bin/env python3
"""
监控任务模块 - 供 Web 和后台调度器共用
使用特定异常类型，细化错误处理
"""

import os
import json
import asyncio
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

# 全局任务锁，防止定时监控和抓取历史并发执行
task_lock = asyncio.Lock()


def get_webhook_from_db():
    """从数据库获取 webhook"""
    db = SessionLocal()
    try:
        return Config.get_webhook(db)
    finally:
        db.close()


async def check_and_send(target_id, force=False):
    """
    检查目标并发送新回复（优化版：三步流程）
    """
    db = SessionLocal()
    try:
        target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
        if not target:
            return {"success": False, "message": "目标不存在"}
        
        if not target.enabled and not force:
            return {"success": False, "message": "目标已禁用"}
        
        logger.info(f"开始检查用户 {target.uid}", extra={'target_uid': target.uid})
        
        # 获取已发送的PID
        sent_pids = {r.pid for r in db.query(SentRecord).filter(
            SentRecord.target_id == target.id
        ).all()}
        
        crawler = NgaCrawler(STORAGE_STATE_PATH)
        
        # ========== 第一步：快速获取PID列表 ==========
        logger.info(f"[Step 1] 快速获取PID列表...", extra={'target_uid': target.uid})
        try:
            pid_list = await crawler.fetch_pids_only(target.url)
        except LoginExpiredError as e:
            logger.error(f"登录过期: {e}", extra={'target_uid': target.uid})
            return {"success": False, "message": "NGA登录已过期", "fatal": True}
        except Exception as e:
            logger.error(f"获取PID失败: {e}", extra={'target_uid': target.uid})
            return {"success": False, "message": f"获取失败: {e}"}
        
        if not pid_list:
            return {"success": True, "message": "没有获取到回复", "replies_count": 0}
        
        logger.info(f"[Step 1] 获取到 {len(pid_list)} 个PID", extra={'target_uid': target.uid})
        
        # ========== 第二步：识别新PID ==========
        if force:
            new_pid_list = pid_list[:1] if pid_list else []
        else:
            new_pid_list = [p for p in pid_list if p['pid'] not in sent_pids]
        
        new_pids_count = len(new_pid_list)
        logger.info(f"[Step 2] 新PID: {new_pids_count} 个", extra={'target_uid': target.uid})
        
        if new_pids_count == 0:
            return {"success": True, "message": "没有新回复", "replies_count": 0}
        
        # ========== 第三步：获取新回复完整信息 ==========
        logger.info(f"[Step 3] 获取 {new_pids_count} 个新回复详情...", extra={'target_uid': target.uid})
        
        new_replies = []
        for idx, pid_info in enumerate(new_pid_list):
            try:
                reply = await crawler.fetch_reply_detail(pid_info['tid'], pid_info['pid'])
                if reply:
                    reply['topic_title'] = pid_info['title']
                    new_replies.append(reply)
                    logger.info(f"[Step 3] 获取 [{idx+1}/{new_pids_count}] PID={pid_info['pid']}", extra={'target_uid': target.uid})
                if idx < new_pids_count - 1:
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"获取详情失败 PID={pid_info['pid']}: {e}", extra={'target_uid': target.uid})
                continue
        
        if not new_replies:
            return {"success": False, "message": "无法获取新回复详情"}
        
        # ========== 第四步：批量发送 ==========
        webhook = get_webhook_from_db()
        if not webhook:
            return {"success": False, "message": "Webhook 未配置"}
        
        sender = DiscordSender(webhook)
        new_replies.sort(key=lambda x: x.get('post_timestamp', 0))
        
        sent_count = 0
        for idx, reply in enumerate(new_replies):
            reply['target_name'] = target.name or target.uid
            
            # 检查内容是否为空
            if not reply.get('content_full') or reply['content_full'].strip() == '':
                reply['content_full'] = '(内容获取失败)' 
                logger.warning(f"[{idx+1}/{len(new_replies)}] PID={reply['pid']} 内容为空", extra={'target_uid': target.uid})
            
            try:
                success = await sender.send_reply(reply)
                record = SentRecord(
                    target_id=target.id,
                    pid=reply['pid'],
                    tid=reply['tid'],
                    topic_title=reply['topic_title'],
                    content_preview=reply['content_full'][:500] if reply['content_full'] else '',
                    success=success
                )
                db.add(record)
                if success:
                    sent_count += 1
                    logger.info(f"发送成功 [{idx+1}/{len(new_replies)}]", extra={'target_uid': target.uid})
                if idx < len(new_replies) - 1:
                    await asyncio.sleep(1.5)
            except WebhookError as e:
                logger.error(f"发送失败 (Webhook错误): {e}", extra={'target_uid': target.uid})
            except Exception as e:
                logger.error(f"发送失败 (未知错误): {e}", extra={'target_uid': target.uid})
        
        db.commit()
        
        return {
            "success": True,
            "message": f"已发送 {sent_count}/{len(new_replies)} 条",
            "sent_count": sent_count
        }
            
    except WebhookError as e:
        logger.error(f"检查失败 (Webhook错误): {e}", extra={'target_uid': target.uid})
        return {"success": False, "message": f"Webhook错误: {e}"}
    except Exception as e:
        logger.error(f"检查失败: {e}", exc_info=True)
        return {"success": False, "message": f"错误: {type(e).__name__}"}
    finally:
        db.close()




async def check_all_targets():
    """检查所有启用的目标（带全局锁，防止与抓取历史并发）"""
    # 如果锁被占用（抓取历史正在运行），直接跳过
    if task_lock.locked():
        logger.info("[Check] 历史抓取任务正在运行，跳过本次定时检查")
        return []
    
    async with task_lock:
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
    后台任务：抓取用户历史回复并存档（带任务追踪和全局锁）
    
    Args:
        target_id: 监控目标 ID
        max_pages: 最大抓取页数
    """
    # 使用全局锁防止与定时监控并发
    async with task_lock:
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
            
            async def progress_callback(page_num, total_pages, replies_count, stage="抓取中", detail=""):
                """实时更新进度到数据库并记录详细日志"""
                task.completed_pages = page_num
                task.total_replies = replies_count
                # 将详细进度信息存入 error_message 字段用于前端显示
                progress_info = f"{stage}|{detail}"
                task.error_message = progress_info
                db.commit()
                logger.info(f"[Archive Task] 第 {page_num}/{total_pages} 页 - {stage}: {detail}", extra={'target_uid': target.uid})
            
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
            
            # 保存到数据库 - 批量查询优化 N+1 问题
            new_count = 0
            skip_count = 0
            total = len(replies)
            
            # 批量查询已存在的 PID (优化: 一次查询替代多次)
            reply_pids = [r['pid'] for r in replies if r.get('pid')]
            existing_pids = set()
            if reply_pids:
                existing_pids = {
                    row[0] for row in db.query(ReplyArchive.pid).filter(
                        ReplyArchive.pid.in_(reply_pids)
                    ).all()
                }
                logger.info(f"[Archive Task] 批量查询: {len(reply_pids)} 个PID中已存在 {len(existing_pids)} 个", 
                           extra={'target_uid': target.uid})
            
            # 批量添加新回复
            archives_to_add = []
            for reply_data in replies:
                if reply_data.get('pid') in existing_pids:
                    skip_count += 1
                    continue
                
                archives_to_add.append({
                    'target_id': target_id,
                    'pid': reply_data['pid'],
                    'tid': reply_data['tid'],
                    'url': reply_data['url'],
                    'topic_title': reply_data['topic_title'],
                    'main_content': reply_data['main_content'],
                    'quote_content': reply_data.get('quote_content', ''),
                    'post_date': reply_data['post_date'],
                    'forum': reply_data.get('forum', '')
                })
            
            # 批量插入 (比逐条插入快 10-50 倍)
            if archives_to_add:
                db.bulk_insert_mappings(ReplyArchive, archives_to_add)
                new_count = len(archives_to_add)
            
            logger.info(f"[Archive Task] 保存完成: 新增 {new_count} 条, 跳过 {skip_count} 条", extra={'target_uid': target.uid})
            
            # 更新任务状态
            task.status = 'completed'
            task.archived_count = new_count
            task.skipped_count = skip_count
            task.completed_at = datetime.now(timezone.utc)
            db.commit()
            
            logger.info(f"[Archive Task] 任务完成: ID={task.id}, 共 {len(replies)} 条回复")
            
        except (LoginExpiredError, RateLimitError) as e:
            logger.error(f"[Archive Task] 任务被中断: {e}")
            if task:
                task.status = 'failed'
                task.error_message = str(e)
                task.completed_at = datetime.now(timezone.utc)
                db.commit()
            raise
        except Exception as e:
            logger.error(f"[Archive Task] 任务失败: {e}")
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

