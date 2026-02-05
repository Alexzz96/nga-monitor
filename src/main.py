#!/usr/bin/env python3
"""
NGA Monitor - 主入口
整合 Web 服务和后台调度器
"""

import os
import sys
import asyncio
from datetime import datetime

from uvicorn.config import Config as UvicornConfig
from uvicorn.server import Server
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from logger import setup_logging
from db.models import init_db
from monitor import check_all_targets
from schedule_manager import ScheduleManager

init_db()

logger = setup_logging()
SCHEDULER = None
last_check_time = None

async def scheduled_job():
    """定时任务 - 根据调度规则检查目标"""
    global last_check_time
    
    manager = ScheduleManager()
    should_check, interval = manager.should_check_now(last_check_time)
    
    if should_check:
        logger.info(f"执行检查 (规则间隔: {interval}s)")
        await check_all_targets()
        last_check_time = datetime.now()
    else:
        status = manager.get_current_status()
        if status['current_rule']:
            logger.debug(f"跳过检查 - 当前规则: {status['current_rule']['name']}, 状态: {status['status']}")

async def start_scheduler():
    """启动后台调度器"""
    global SCHEDULER
    SCHEDULER = AsyncIOScheduler()
    
    # 使用 30 秒间隔检查调度规则（避免任务重叠）
    SCHEDULER.add_job(
        scheduled_job,
        trigger=IntervalTrigger(seconds=30),
        id='nga_check',
        replace_existing=True,
        max_instances=1  # 确保只有一个实例在运行
    )
    
    SCHEDULER.start()
    logger.info("后台调度器已启动")
    
    # 显示当前调度状态
    manager = ScheduleManager()
    status = manager.get_current_status()
    if status['current_rule']:
        logger.info(f"当前调度规则: {status['current_rule']['name']} - {status['status']}")
    else:
        logger.info(f"等待调度规则生效，下次检查: {status['next_check']}")
    
    logger.info("执行首次检查...")
    await scheduled_job()

async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("NGA Monitor 启动")
    logger.info("=" * 60)
    
    storage_path = os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json')
    if not os.path.exists(storage_path):
        logger.error(f"错误: Storage state 文件不存在: {storage_path}")
        logger.error("请先运行 export_nga_state.py 生成登录状态")
        sys.exit(1)
    
    await start_scheduler()
    
    from web.app import app
    port = int(os.getenv('WEB_PORT', '12306'))
    logger.info(f"启动 Web 服务器，端口: {port}")
    
    config = UvicornConfig(app, host="0.0.0.0", port=port, log_level="warning")
    server = Server(config)
    await server.serve()

if __name__ == '__main__':
    asyncio.run(main())
