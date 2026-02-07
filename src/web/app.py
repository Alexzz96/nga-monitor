"""
Web API 模块 - 主应用入口
路由已拆分到 routes/ 目录
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.models import (
    get_db, MonitorTarget, Config,
    init_db
)
from web.routes import api_router

init_db()

app = FastAPI(title="NGA Monitor")
templates = Jinja2Templates(directory="/app/src/web/templates")

# 注册 API 路由
app.include_router(api_router)

# 向后兼容：旧版解析 URL 端点
from web.routes.utils import parse_url
app.post("/api/parse-url")(parse_url)

# 向后兼容：日志端点别名
from web.routes.stats import get_logs, cleanup_logs, get_stats
app.get("/api/logs")(get_logs)
app.post("/api/logs/cleanup")(cleanup_logs)
app.get("/api/stats")(get_stats)

# 向后兼容：目标端点别名
from web.routes.targets import get_targets, create_target
app.get("/api/targets")(get_targets)
app.post("/api/targets")(create_target)

# 向后兼容：Cookie 状态端点
from web.routes.utils import get_cookie_status
app.get("/api/cookie-status")(get_cookie_status)

# 向后兼容：Webhook 端点别名
from web.routes.webhook import get_webhook, update_webhook, test_webhook
app.get("/api/webhook")(get_webhook)
app.post("/api/webhook")(update_webhook)
app.post("/api/webhook/test")(test_webhook)

STORAGE_STATE_PATH = Path(os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json'))


# ========== 页面路由 ==========

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    """首页 - 监控目标列表"""
    targets = db.query(MonitorTarget).all()
    webhook = Config.get_webhook(db)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "targets": targets,
        "webhook": webhook[:50] + "..." if webhook else "未配置"
    })


@app.get("/ai", response_class=HTMLResponse)
async def ai_page(request: Request):
    """AI 分析页面"""
    return templates.TemplateResponse("ai.html", {"request": request})


@app.get("/data", response_class=HTMLResponse)
async def data_page(request: Request):
    """数据管理页面"""
    return templates.TemplateResponse("data.html", {"request": request})


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    """情绪分析可视化页面"""
    return templates.TemplateResponse("analytics.html", {"request": request})


# ========== 健康检查 ==========

@app.get("/health")
async def health_check():
    """健康检查端点"""
    from browser_pool import BrowserPool
    from rate_limiter import get_limiter_stats
    
    pool = BrowserPool.get_instance()
    pool_stats = pool.get_stats()
    limiter_stats = get_limiter_stats()
    
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "storage_state": STORAGE_STATE_PATH.exists(),
        "browser_pool": pool_stats,
        "rate_limiters": limiter_stats
    }
