"""
Web API 模块
"""
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import (
    get_db, MonitorTarget, SentRecord, SystemLog, Config, ScheduleRule, ReplyArchive, ArchiveTask, AIAnalysisReport,
    init_db, cleanup_old_logs
)
from discord_sender import DiscordSender
from monitor import check_and_send
from schedule_manager import ScheduleManager
from ai_analyzer import AIAnalyzer, analyze_user, compare_users

init_db()

app = FastAPI(title="NGA Monitor")
templates = Jinja2Templates(directory="/app/src/web/templates")

STORAGE_STATE_PATH = Path(os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json'))

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

@app.get("/api/targets")
async def get_targets(db: Session = Depends(get_db)):
    """获取所有监控目标"""
    targets = db.query(MonitorTarget).all()
    return {"targets": [t.to_dict() for t in targets]}

@app.post("/api/targets")
async def create_target(data: dict, db: Session = Depends(get_db)):
    """创建监控目标"""
    uid = data.get('uid', '').strip()
    if not uid:
        raise HTTPException(status_code=400, detail="UID 不能为空")
    
    # 检查是否已存在
    existing = db.query(MonitorTarget).filter(MonitorTarget.uid == uid).first()
    if existing:
        raise HTTPException(status_code=400, detail="该 UID 已存在")
    
    target = MonitorTarget(
        uid=uid,
        name=data.get('name', f'用户 {uid}'),
        url=f'https://nga.178.com/thread.php?searchpost=1&authorid={uid}',
        enabled=data.get('enabled', True),
        check_interval=data.get('check_interval', 60)
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return {"success": True, "target": target.to_dict()}

@app.put("/api/targets/{target_id}")
async def update_target(target_id: int, data: dict, db: Session = Depends(get_db)):
    """更新监控目标"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    if 'name' in data:
        target.name = data['name']
    if 'enabled' in data:
        target.enabled = data['enabled']
    if 'check_interval' in data:
        target.check_interval = data['check_interval']
    
    db.commit()
    db.refresh(target)
    return {"success": True, "target": target.to_dict()}

@app.delete("/api/targets/{target_id}")
async def delete_target(target_id: int, db: Session = Depends(get_db)):
    """删除监控目标"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    db.delete(target)
    db.commit()
    return {"success": True}

@app.get("/api/webhook")
async def get_webhook(db: Session = Depends(get_db)):
    """获取 webhook URL"""
    webhook = Config.get_webhook(db)
    return {"webhook": webhook}

@app.post("/api/webhook")
async def update_webhook(data: dict, db: Session = Depends(get_db)):
    """更新 webhook URL"""
    url = data.get('url', '').strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    Config.set_webhook(db, url)
    return {"success": True}

@app.post("/api/webhook/test")
async def test_webhook(db: Session = Depends(get_db)):
    """测试 webhook"""
    webhook = Config.get_webhook(db)
    if not webhook:
        raise HTTPException(status_code=400, detail="Webhook 未配置")
    
    sender = DiscordSender(webhook)
    test_data = {
        "topic_title": "[测试] Webhook 连接测试",
        "url": "https://nga.178.com",
        "forum": "[测试版块]",
        "post_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
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

@app.post("/api/targets/{target_id}/test")
async def test_target(target_id: int, force: bool = False, db: Session = Depends(get_db)):
    """
    测试单个监控目标
    
    Args:
        force: 是否强制发送（即使已发送过）
    """
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    storage_path = os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json')
    if not os.path.exists(storage_path):
        raise HTTPException(status_code=400, detail="Storage state 文件不存在")
    
    # 调用统一的监控逻辑
    result = await check_and_send(target_id, force=force)
    
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result["message"])

@app.post("/api/targets/{target_id}/force-send")
async def force_send_target(target_id: int, db: Session = Depends(get_db)):
    """强制发送最新回复（不管是否已发送过）"""
    return await test_target(target_id, force=True, db=db)

@app.get("/api/logs")
async def get_logs(
    level: str = None,
    target_uid: str = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """获取日志"""
    query = db.query(SystemLog)
    
    if level:
        query = query.filter(SystemLog.level == level.upper())
    if target_uid:
        query = query.filter(SystemLog.target_uid == target_uid)
    
    logs = query.order_by(SystemLog.created_at.desc()).limit(limit).all()
    return {"logs": [log.to_dict() for log in logs]}

@app.post("/api/logs/cleanup")
async def cleanup_logs(days: int = 7, db: Session = Depends(get_db)):
    """清理旧日志"""
    deleted = cleanup_old_logs(days)
    return {"success": True, "deleted": deleted}

@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    """获取详细统计信息"""
    targets_count = db.query(MonitorTarget).count()
    enabled_count = db.query(MonitorTarget).filter(MonitorTarget.enabled == True).count()
    total_sent = db.query(SentRecord).count()
    success_sent = db.query(SentRecord).filter(SentRecord.success == True).count()
    
    # 最近 24 小时发送数
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_sent = db.query(SentRecord).filter(SentRecord.sent_at >= day_ago).count()
    
    # 按目标统计发送数
    target_stats = db.query(
        MonitorTarget.id,
        MonitorTarget.name,
        MonitorTarget.uid,
        func.count(SentRecord.id).label('sent_count')
    ).outerjoin(
        SentRecord, MonitorTarget.id == SentRecord.target_id
    ).group_by(MonitorTarget.id).all()
    
    target_stats_list = [{
        "id": t.id,
        "name": t.name,
        "uid": t.uid,
        "sent_count": t.sent_count
    } for t in target_stats]
    
    return {
        "targets": {"total": targets_count, "enabled": enabled_count},
        "sent": {"total": total_sent, "success": success_sent, "recent_24h": recent_sent},
        "target_stats": target_stats_list
    }

@app.post("/api/parse-url")
async def parse_url(data: dict):
    """从 NGA URL 解析 UID"""
    url = data.get('url', '').strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    # 支持多种 URL 格式
    patterns = [
        r'authorid=(\d+)',  # thread.php?searchpost=1&authorid=xxx
        r'uid=(\d+)',       # nuke.php?func=ucp&uid=xxx
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            uid = match.group(1)
            return {"success": True, "uid": uid}
    
    raise HTTPException(status_code=400, detail="无法从 URL 解析 UID")

@app.get("/api/cookie-status")
async def get_cookie_status():
    """获取 Cookie 登录状态"""
    try:
        if not STORAGE_STATE_PATH.exists():
            return {
                "exists": False,
                "message": "Cookie 文件不存在"
            }
        
        with open(STORAGE_STATE_PATH, 'r') as f:
            state = json.load(f)
        
        cookies = state.get('cookies', [])
        
        # 查找关键 Cookie
        nga_cookies = {}
        for cookie in cookies:
            name = cookie.get('name', '')
            if 'nga' in name.lower() or name in ['ngaPassportUid', 'ngacn0comUserInfo', '_178c']:
                nga_cookies[name] = {
                    "expires": cookie.get('expires', 'N/A'),
                    "has_value": bool(cookie.get('value'))
                }
        
        return {
            "exists": True,
            "cookie_count": len(cookies),
            "nga_cookies": nga_cookies,
            "last_modified": datetime.fromtimestamp(
                STORAGE_STATE_PATH.stat().st_mtime
            ).isoformat()
        }
    except Exception as e:
        return {
            "exists": True,
            "error": str(e)
        }

# ========== 调度规则管理 ==========

@app.get("/api/schedule/rules")
async def get_schedule_rules(db: Session = Depends(get_db)):
    """获取所有调度规则"""
    rules = db.query(ScheduleRule).order_by(ScheduleRule.priority.desc()).all()
    return {"rules": [r.to_dict() for r in rules]}

@app.post("/api/schedule/rules")
async def create_schedule_rule(data: dict, db: Session = Depends(get_db)):
    """创建调度规则"""
    name = data.get('name', '').strip()
    start_time = data.get('start_time', '').strip()
    end_time = data.get('end_time', '').strip()
    
    if not name:
        raise HTTPException(status_code=400, detail="规则名称不能为空")
    if not start_time or not end_time:
        raise HTTPException(status_code=400, detail="开始时间和结束时间不能为空")
    
    # 验证时间格式
    try:
        datetime.strptime(start_time, '%H:%M')
        datetime.strptime(end_time, '%H:%M')
    except ValueError:
        raise HTTPException(status_code=400, detail="时间格式错误，请使用 HH:MM 格式")
    
    rule = ScheduleRule(
        name=name,
        start_time=start_time,
        end_time=end_time,
        interval_seconds=data.get('interval_seconds', 60),
        is_summary=data.get('is_summary', False),
        enabled=data.get('enabled', True),
        priority=data.get('priority', 0)
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {"success": True, "rule": rule.to_dict()}

@app.put("/api/schedule/rules/{rule_id}")
async def update_schedule_rule(rule_id: int, data: dict, db: Session = Depends(get_db)):
    """更新调度规则"""
    rule = db.query(ScheduleRule).filter(ScheduleRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    
    if 'name' in data:
        rule.name = data['name']
    if 'start_time' in data:
        try:
            datetime.strptime(data['start_time'], '%H:%M')
            rule.start_time = data['start_time']
        except ValueError:
            raise HTTPException(status_code=400, detail="开始时间格式错误")
    if 'end_time' in data:
        try:
            datetime.strptime(data['end_time'], '%H:%M')
            rule.end_time = data['end_time']
        except ValueError:
            raise HTTPException(status_code=400, detail="结束时间格式错误")
    if 'interval_seconds' in data:
        rule.interval_seconds = data['interval_seconds']
    if 'is_summary' in data:
        rule.is_summary = data['is_summary']
    if 'enabled' in data:
        rule.enabled = data['enabled']
    if 'priority' in data:
        rule.priority = data['priority']
    
    db.commit()
    db.refresh(rule)
    return {"success": True, "rule": rule.to_dict()}

@app.delete("/api/schedule/rules/{rule_id}")
async def delete_schedule_rule(rule_id: int, db: Session = Depends(get_db)):
    """删除调度规则"""
    rule = db.query(ScheduleRule).filter(ScheduleRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    
    db.delete(rule)
    db.commit()
    return {"success": True}

@app.get("/api/schedule/status")
async def get_schedule_status():
    """获取当前调度状态"""
    manager = ScheduleManager()
    return manager.get_current_status()

# ========== AI 提示词模板 ==========

AI_PROMPT_TEMPLATES = {
    "standard": {
        "name": "标准分析（推荐）",
        "system_prompt": """你是一位资深的金融市场研究员，专注于分析个人投资者的行为模式、投资逻辑和市场观点。

分析原则：
1. 基于事实发言，不做主观臆测
2. 区分观点表达和实际操作建议
3. 识别用户的决策逻辑而非只看结论
4. 注意用户认知的变化和一致性

输出要求：
- 必须返回有效的 JSON 格式
- 所有字符串字段不能为空
- 数值字段使用整数或字符串""",
        "analysis_prompt": """请分析以下用户 "{user_name}" 的投资行为和言论特征：

{content}

分析任务：
1. 识别用户的投资方法论（价值/成长/技术/量化等）
2. 判断用户的信息来源和验证能力
3. 评估用户的风险意识和仓位管理能力
4. 分析用户的市场认知层次

请严格按照以下 JSON 格式返回，确保字段完整：
{
    "summary": "50字以内的核心画像",
    "investment_style": "具体投资风格（如：深度价值型/趋势跟踪型/事件驱动型）",
    "methodology": "投资方法论描述（30字以内）",
    "risk_preference": "风险偏好（保守/稳健/平衡/进取/激进）",
    "position_style": "仓位特征（重仓集中/均衡分散/轻仓试错）",
    "focus_areas": ["板块1", "板块2"],
    "key_stocks": ["代码1", "代码2"],
    "sentiment": "当前市场态度（极度乐观/乐观/谨慎乐观/中性/谨慎悲观/悲观/极度悲观）",
    "reasoning_quality": "逻辑质量评分（1-10分）",
    "information_sources": ["信息来源1", "信息来源2"],
    "behavior_patterns": ["行为特征1", "行为特征2"],
    "cognitive_bias": ["可能存在的认知偏差1"],
    "consistency_score": "观点一致性评分（1-10分）",
    "recommendation": "基于以上分析的简要建议（50字以内）"
}

注意：
- 如果某些字段无法判断，填写 "未明确" 或空数组 []
- sentiment 必须从给定选项中选择
- 所有评分使用数字字符串（如 "7"）"""
    },
    "value": {
        "name": "深度价值投资",
        "system_prompt": """你是一位价值投资领域的资深分析师，专注于识别和评估价值投资者的思维方式和决策框架。

分析重点：
1. 对企业基本面的理解深度
2. 估值方法的运用能力
3. 安全边际意识
4. 长期持有的定力
5. 逆向投资的勇气

分析原则：客观、严谨、关注逻辑而非结果""",
        "analysis_prompt": """请深度分析价值投资者 "{user_name}" 的投资体系和行为特征：

{content}

请从价值投资的角度分析以下方面：

1. 估值能力：用户如何评估企业价值？使用什么估值方法？
2. 企业分析：对商业模式、护城河、财务报表的理解程度
3. 安全边际：是否有明确的买入标准和安全边际要求
4. 持仓定力：面对波动时的反应，是否能坚持长期持有
5. 逆向思维：是否能在市场恐慌时发现机会

输出 JSON 格式：
{
    "summary": "价值投资画像",
    "investment_style": "价值型（深度/质量/周期等细分）",
    "valuation_method": "主要估值方法",
    "moat_understanding": "护城河理解评分（1-10）",
    "financial_analysis": "财务分析能力评分（1-10）",
    "margin_of_safety": "安全边际意识评分（1-10）",
    "patience_score": "持仓耐心评分（1-10）",
    "contrarian_tendency": "逆向倾向评分（1-10）",
    "typical_holding_period": "典型持仓周期估计",
    "focus_areas": ["关注的行业"],
    "key_stocks": ["重仓股代码"],
    "red_flags": ["可能的风险信号"],
    "strengths": ["核心优势"],
    "recommendation": "改进建议"
}"""
    },
    "trading": {
        "name": "短线交易分析",
        "system_prompt": """你是一位短线交易专家，擅长分析交易者的择时能力、纪律性和情绪控制。

分析重点：
1. 买卖点选择的逻辑
2. 止损止盈纪律
3. 热点把握能力
4. 仓位管理
5. 情绪控制能力

分析原则：关注过程而非结果，识别系统性的交易模式""",
        "analysis_prompt": """请分析短线交易者 "{user_name}" 的交易风格和执行力：

{content}

交易分析维度：
1. 择时信号：用户依据什么信号进出场？（技术/消息/情绪/其他）
2. 止损纪律：是否有明确的止损规则？执行力如何？
3. 热点敏感度：对题材、概念的把握速度和准确度
4. 盈亏比意识：是否有截断亏损、让利润奔跑的理念
5. 交易频率：从发言推断的交易活跃度
6. 情绪控制：面对亏损和踏空时的反应

输出 JSON 格式：
{
    "summary": "交易者画像",
    "investment_style": "交易风格（打板/趋势/低吸/接力等）",
    "entry_signals": ["进场信号类型"],
    "exit_signals": ["出场信号类型"],
    "stop_loss_discipline": "止损纪律评分（1-10）",
    "hot_spot_sensitivity": "热点敏感度评分（1-10）",
    "risk_reward_awareness": "盈亏比意识评分（1-10）",
    "position_management": "仓位管理评分（1-10）",
    "emotional_control": "情绪控制评分（1-10）",
    "estimated_trade_freq": "估计交易频率（高频/中频/低频）",
    "focus_areas": ["常做板块"],
    "typical_patterns": ["常用手法"],
    "common_mistakes": ["常见错误"],
    "recommendation": "提升建议"
}"""
    },
    "minimal": {
        "name": "极简快速版",
        "system_prompt": "你是投资行为分析师，用简洁的语言概括用户的投资特征。",
        "analysis_prompt": """快速分析用户 "{user_name}":

{content}

一句话总结投资风格，并给出：
{
    "summary": "一句话画像",
    "investment_style": "投资风格",
    "risk_preference": "风险偏好",
    "focus_areas": [],
    "key_stocks": [],
    "sentiment": "市场情绪",
    "characteristics": [],
    "recommendation": "建议"
}"""
    },
    "compare": {
        "name": "用户对比分析",
        "system_prompt": """你是一位专业的投资者对比分析师，擅长识别不同投资者之间的差异和共同点。

分析原则：
1. 客观比较，不做价值判断
2. 关注方法论差异而非收益差异
3. 识别互补性和可学习性
4. 发现群体特征""",
        "analysis_prompt": """请对比分析以下投资者的风格差异：

{content}

对比维度：
1. 投资理念的异同
2. 信息处理方式的差异
3. 风险控制能力的比较
4. 市场敏感度的对比
5. 情绪稳定性的差异

输出格式：
{
    "summary": "整体对比结论（80字以内）",
    "rankings": {
        "risk_management": ["用户排名"],
        "reasoning_quality": ["用户排名"],
        "information_breadth": ["用户排名"]
    },
    "comparisons": [
        {
            "user": "用户名",
            "dominant_trait": "最突出特征",
            "style_summary": "一句话总结",
            "strengths": [],
            "weaknesses": [],
            "reference_value": "对他人的参考价值"
        }
    ],
    "similarities": ["共同点"],
    "differences": ["核心差异"],
    "group_characteristics": "群体特征",
    "learning_points": "可互相学习的点"
}"""
    }
}


@app.get("/api/ai/templates")
async def get_prompt_templates():
    """获取所有可用的提示词模板"""
    return {
        "templates": [
            {"id": key, "name": value["name"]} 
            for key, value in AI_PROMPT_TEMPLATES.items()
        ]
    }


@app.get("/api/ai/templates/{template_id}")
async def get_prompt_template(template_id: str):
    """获取指定模板的详细内容"""
    if template_id not in AI_PROMPT_TEMPLATES:
        raise HTTPException(status_code=404, detail="模板不存在")
    
    template = AI_PROMPT_TEMPLATES[template_id]
    return {
        "id": template_id,
        "name": template["name"],
        "system_prompt": template["system_prompt"],
        "analysis_prompt": template["analysis_prompt"]
    }


# ========== AI 分析 ==========

@app.get("/api/ai/config")
async def get_ai_config(db: Session = Depends(get_db)):
    """获取 AI 配置"""
    config = Config.get_ai_config(db)
    # 隐藏 API Key 只显示前 10 位
    if config.get('api_key'):
        config['api_key_masked'] = config['api_key'][:10] + '...' + config['api_key'][-4:]
    return config

@app.post("/api/ai/models")
async def get_ai_models(data: dict, db: Session = Depends(get_db)):
    """获取可用的 AI 模型列表"""
    import httpx
    
    provider = data.get('provider', 'kimi')
    base_url = data.get('base_url', '').rstrip('/')
    api_key = data.get('api_key', '')
    
    # 如果没有提供配置，尝试从数据库读取
    if not base_url or not api_key:
        config = Config.get_ai_config(db)
        base_url = base_url or config.get('base_url', '')
        api_key = api_key or config.get('api_key', '')
    
    if not base_url or not api_key:
        raise HTTPException(status_code=400, detail="API Base URL 和 API Key 未配置")
    
    try:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f'{base_url}/models',
                headers=headers
            )
        
        if response.status_code == 200:
            result = response.json()
            models = result.get('data', [])
            
            # 过滤出常用的聊天模型
            chat_models = []
            for model in models:
                model_id = model.get('id', '')
                # 过滤掉嵌入模型等
                if any(x in model_id.lower() for x in ['embedding', 'tts', 'whisper', 'dall']):
                    continue
                chat_models.append({
                    'id': model_id,
                    'owned_by': model.get('owned_by', 'unknown')
                })
            
            return {
                "success": True,
                "models": chat_models,
                "count": len(chat_models)
            }
        else:
            error_msg = f"API 请求失败: {response.status_code}"
            try:
                error_data = response.json()
                if 'error' in error_data:
                    error_msg = error_data['error'].get('message', error_msg)
            except:
                pass
            raise HTTPException(status_code=400, detail=error_msg)
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=500, detail="请求超时")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"请求异常: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}")

@app.post("/api/ai/config")
async def update_ai_config(data: dict, db: Session = Depends(get_db)):
    """更新 AI 配置"""
    allowed_keys = ['provider', 'base_url', 'api_key', 'model', 'system_prompt', 'analysis_prompt']
    config = {k: v for k, v in data.items() if k in allowed_keys}
    Config.set_ai_config(db, config)
    return {"success": True}

@app.post("/api/ai/analyze/{target_id}")
async def analyze_target(target_id: int, data: dict, db: Session = Depends(get_db)):
    """
    对单个用户进行 AI 分析
    
    Args:
        target_id: 监控目标 ID
        data: {"time_range": "week" | "month" | "all"}
    """
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 加载 AI 配置
    ai_config = Config.get_ai_config(db)
    
    # 检查 API Key 是否配置
    if not ai_config.get('api_key'):
        raise HTTPException(status_code=400, detail="AI API Key 未配置，请在 AI 配置页面设置")
    
    time_range = data.get('time_range', 'week')
    
    # 检查是否有足够数据
    reply_count = db.query(ReplyArchive).filter(
        ReplyArchive.target_id == target_id
    ).count()
    
    if reply_count < 3:
        raise HTTPException(status_code=400, detail=f"存档数据不足，仅 {reply_count} 条回复，需要至少 3 条")
    
    # 执行分析
    analyzer = AIAnalyzer(ai_config)
    result = await analyzer.analyze_user_style(target_id, time_range)
    
    if not result:
        raise HTTPException(status_code=500, detail="AI 分析失败，请检查日志")
    
    return result

@app.post("/api/ai/compare")
async def compare_targets(data: dict, db: Session = Depends(get_db)):
    """
    对比分析多个用户
    
    Args:
        data: {"target_ids": [1, 2, 3], "time_range": "week" | "month" | "all"}
    """
    target_ids = data.get('target_ids', [])
    if len(target_ids) < 2:
        raise HTTPException(status_code=400, detail="至少需要选择 2 个用户进行对比")
    
    # 加载 AI 配置
    ai_config = Config.get_ai_config(db)
    if not ai_config.get('api_key'):
        raise HTTPException(status_code=400, detail="AI API Key 未配置")
    
    time_range = data.get('time_range', 'week')
    
    analyzer = AIAnalyzer(ai_config)
    result = await analyzer.compare_users(target_ids, time_range)
    
    if not result:
        raise HTTPException(status_code=500, detail="对比分析失败")
    
    return result

@app.get("/api/ai/reports")
async def get_analysis_reports(
    target_id: int = None,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    """获取历史分析报告"""
    query = db.query(AIAnalysisReport)
    
    if target_id:
        query = query.filter(AIAnalysisReport.target_id == target_id)
    
    reports = query.order_by(AIAnalysisReport.created_at.desc()).limit(limit).all()
    
    result = []
    for report in reports:
        r = report.to_dict()
        target = db.query(MonitorTarget).filter(MonitorTarget.id == report.target_id).first()
        r['target_name'] = target.name if target else '未知'
        result.append(r)
    
    return {"reports": result}

@app.get("/api/ai/reports/{report_id}")
async def get_report_detail(report_id: int, db: Session = Depends(get_db)):
    """获取报告详情"""
    report = db.query(AIAnalysisReport).filter(AIAnalysisReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    
    import json
    result = report.to_dict()
    result['report_content'] = json.loads(report.report_content) if report.report_content else {}
    
    target = db.query(MonitorTarget).filter(MonitorTarget.id == report.target_id).first()
    result['target_name'] = target.name if target else '未知'
    
    return result

@app.delete("/api/ai/reports/{report_id}")
async def delete_report(report_id: int, db: Session = Depends(get_db)):
    """删除报告"""
    report = db.query(AIAnalysisReport).filter(AIAnalysisReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    
    db.delete(report)
    db.commit()
    return {"success": True}

# ========== 历史数据归档 ==========

@app.get("/api/archive/history/{target_id}")
async def get_user_history(
    target_id: int,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """
    获取用户的历史回复列表（分页）
    
    Args:
        target_id: 监控目标 ID
        page: 页码，从1开始
        limit: 每页数量
    """
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 获取总数
    total = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id).count()
    
    # 分页查询，按发布时间倒序
    records = db.query(ReplyArchive).filter(
        ReplyArchive.target_id == target_id
    ).order_by(ReplyArchive.post_date.desc()).offset((page - 1) * limit).limit(limit).all()
    
    return {
        "target_id": target_id,
        "target_name": target.name,
        "total": total,
        "page": page,
        "limit": limit,
        "records": [r.to_dict() for r in records]
    }


@app.post("/api/archive/history/{target_id}")
async def archive_history(target_id: int, data: dict, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    抓取用户历史回复并存档
    
    Args:
        target_id: 监控目标 ID
        data: {"max_pages": 25} 默认抓取25页(500条)
    """
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    max_pages = data.get('max_pages', 25)
    
    # 使用后台任务执行，避免请求超时
    from monitor import archive_history_task
    background_tasks.add_task(archive_history_task, target_id, max_pages)
    
    return {
        "success": True,
        "message": f"已开始抓取 {target.name} 的历史数据（{max_pages}页）",
        "target_id": target_id,
        "max_pages": max_pages
    }

@app.get("/api/archive/status/{target_id}")
async def get_archive_status(target_id: int, db: Session = Depends(get_db)):
    """获取目标的历史归档状态"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 统计存档数量
    total_count = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id).count()
    
    # 获取最新和最早的存档
    latest = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id).order_by(ReplyArchive.created_at.desc()).first()
    earliest = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id).order_by(ReplyArchive.created_at.asc()).first()
    
    # 获取进行中的任务
    running_task = db.query(ArchiveTask).filter(
        ArchiveTask.target_id == target_id,
        ArchiveTask.status == 'running'
    ).first()
    
    return {
        "target_id": target_id,
        "target_name": target.name,
        "total_archived": total_count,
        "latest_post_date": latest.post_date if latest else None,
        "earliest_post_date": earliest.post_date if earliest else None,
        "running_task": running_task.to_dict() if running_task else None
    }


@app.get("/api/archive/tasks")
async def get_archive_tasks(
    target_id: int = None,
    status: str = None,
    limit: int = 20,
    db: Session = Depends(get_db)
):
    """获取归档任务列表"""
    query = db.query(ArchiveTask)
    
    if target_id:
        query = query.filter(ArchiveTask.target_id == target_id)
    if status:
        query = query.filter(ArchiveTask.status == status)
    
    tasks = query.order_by(ArchiveTask.started_at.desc()).limit(limit).all()
    
    return {
        "tasks": [t.to_dict() for t in tasks],
        "count": len(tasks)
    }


@app.get("/api/archive/stats")
async def get_archive_overall_stats(db: Session = Depends(get_db)):
    """获取归档总体统计"""
    from sqlalchemy import func
    import os
    
    # 总存档数
    total_archived = db.query(ReplyArchive).count()
    
    # 数据库文件大小
    db_path = os.getenv('DB_PATH', '/app/data/nga_monitor.db')
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    
    # 每个用户的存档统计
    user_stats = db.query(
        MonitorTarget.id,
        MonitorTarget.name,
        func.count(ReplyArchive.id).label('count')
    ).outerjoin(
        ReplyArchive, MonitorTarget.id == ReplyArchive.target_id
    ).group_by(MonitorTarget.id).all()
    
    return {
        "total_archived": total_archived,
        "db_size": db_size,
        "user_stats": [{"id": u.id, "name": u.name, "count": u.count} for u in user_stats]
    }


@app.post("/api/archive/cleanup")
async def cleanup_archive(
    data: dict,
    db: Session = Depends(get_db)
):
    """
    清理旧归档数据
    
    Args:
        data: {"days": 90, "dry_run": false} - 保留最近 N 天，dry_run 为 true 只统计不删除
    """
    days = data.get('days', 90)
    dry_run = data.get('dry_run', False)
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    # 统计要删除的记录
    old_records = db.query(ReplyArchive).filter(ReplyArchive.created_at < cutoff)
    count = old_records.count()
    
    if dry_run:
        return {
            "dry_run": True,
            "would_delete": count,
            "cutoff_date": cutoff.isoformat()
        }
    
    # 执行删除
    deleted = old_records.delete()
    db.commit()
    
    return {
        "deleted": deleted,
        "cutoff_date": cutoff.isoformat()
    }


@app.post("/api/archive/export/{target_id}")
async def export_archive(
    target_id: int,
    data: dict,
    db: Session = Depends(get_db)
):
    """
    导出归档数据为 JSON
    
    Args:
        data: {"format": "json", "days": 30}
    """
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    days = data.get('days')
    
    query = db.query(ReplyArchive).filter(ReplyArchive.target_id == target_id)
    
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(ReplyArchive.created_at >= cutoff)
    
    records = query.order_by(ReplyArchive.post_date.desc()).all()
    
    export_data = {
        "target_id": target_id,
        "target_name": target.name,
        "export_time": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": [r.to_dict() for r in records]
    }
    
    return export_data
