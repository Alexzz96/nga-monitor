"""
Web 路由模块
"""
from fastapi import APIRouter

from .targets import router as targets_router
from .schedule import router as schedule_router
from .ai import router as ai_router
from .archive import router as archive_router
from .stats import router as stats_router
from .webhook import router as webhook_router
from .utils import router as utils_router

# 主 API 路由
api_router = APIRouter()

# 注册各模块路由
api_router.include_router(targets_router)
api_router.include_router(schedule_router)
api_router.include_router(ai_router)
api_router.include_router(archive_router)
api_router.include_router(stats_router)
api_router.include_router(webhook_router)
api_router.include_router(utils_router)

__all__ = ['api_router']
