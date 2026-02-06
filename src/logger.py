import logging
import sys
from async_logger import get_async_handler, close_async_handler


def setup_logging():
    """配置日志系统（异步版本）"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 异步数据库写入（批量，不阻塞）
    db_handler = get_async_handler()
    db_handler.setLevel(logging.INFO)
    db_handler.setFormatter(formatter)
    logger.addHandler(db_handler)
    
    return logger


def shutdown_logging():
    """关闭日志系统，确保所有日志写入完成"""
    close_async_handler()
