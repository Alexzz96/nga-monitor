#!/usr/bin/env python3
"""
异常分类模块 - 定义项目中使用的特定异常类型
避免使用裸 except Exception
"""


class NgaMonitorError(Exception):
    """基础异常类"""
    pass


# ========== 网络/爬取相关异常 ==========

class CrawlerError(NgaMonitorError):
    """爬虫基础异常"""
    pass


class LoginExpiredError(CrawlerError):
    """NGA 登录过期"""
    pass


class PageNotFoundError(CrawlerError):
    """页面不存在"""
    pass


class RateLimitError(CrawlerError):
    """触发网站限流"""
    pass


class NetworkError(CrawlerError):
    """网络连接错误"""
    pass


class ParseError(CrawlerError):
    """页面解析错误"""
    pass


# ========== API/发送相关异常 ==========

class SenderError(NgaMonitorError):
    """发送器基础异常"""
    pass


class WebhookError(SenderError):
    """Webhook 发送失败"""
    pass


class AIAPIError(SenderError):
    """AI API 调用失败"""
    pass


# ========== 数据库相关异常 ==========

class DatabaseError(NgaMonitorError):
    """数据库操作错误"""
    pass


# ========== 配置相关异常 ==========

class ConfigError(NgaMonitorError):
    """配置错误"""
    pass


class ValidationError(ConfigError):
    """数据验证错误"""
    pass


# ========== 任务执行相关异常 ==========

class TaskError(NgaMonitorError):
    """任务执行错误"""
    pass


# ========== 异常处理工具 ==========

import logging
import traceback

logger = logging.getLogger(__name__)


def handle_exception(e: Exception, context: str = "", reraise: bool = False, 
                     critical: bool = False) -> bool:
    """
    统一异常处理
    
    Args:
        e: 捕获的异常
        context: 异常发生的上下文描述
        reraise: 是否重新抛出异常
        critical: 是否为致命错误（记录为 ERROR）
        
    Returns:
        bool: 是否继续执行（True=可继续，False=应停止）
    """
    # 如果要重新抛出，直接抛出
    if reraise and not isinstance(e, (KeyboardInterrupt, SystemExit)):
        raise
    
    # 分类处理
    if isinstance(e, LoginExpiredError):
        logger.error(f"[{context}] 登录已过期，需要重新导出 storage state: {e}")
        return False  # 致命错误，需要人工介入
    
    elif isinstance(e, RateLimitError):
        logger.warning(f"[{context}] 触发网站限流，等待后重试: {e}")
        return True  # 可继续，但需要等待
    
    elif isinstance(e, NetworkError):
        logger.warning(f"[{context}] 网络错误，可能是临时问题: {e}")
        return True  # 可继续，重试可能成功
    
    elif isinstance(e, ParseError):
        logger.warning(f"[{context}] 页面解析失败，页面结构可能改变: {e}")
        return True  # 可继续，但可能需要修复代码
    
    elif isinstance(e, WebhookError):
        logger.error(f"[{context}] Webhook 发送失败: {e}")
        return True  # 可继续，但通知可能丢失
    
    elif isinstance(e, AIAPIError):
        logger.error(f"[{context}] AI API 调用失败: {e}")
        return True  # 可继续，但分析未完成
    
    elif isinstance(e, ValidationError):
        logger.warning(f"[{context}] 数据验证失败: {e}")
        return True  # 可继续，但本条数据无效
    
    elif isinstance(e, DatabaseError):
        logger.error(f"[{context}] 数据库错误: {e}\n{traceback.format_exc()}")
        return False  # 可能是严重问题
    
    elif isinstance(e, (KeyboardInterrupt, SystemExit)):
        raise  # 不要拦截系统信号
    
    else:
        # 未知异常
        level = logging.ERROR if critical else logging.WARNING
        logger.log(level, f"[{context}] 未预期异常: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return not critical
