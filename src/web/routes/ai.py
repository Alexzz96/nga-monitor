"""
AI 分析路由
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from typing import Optional

from db.models import get_db, MonitorTarget, AIAnalysisReport
from ai_analyzer import AIAnalyzer
from config_manager import list_prompt_templates, get_prompt_template

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/templates")
async def get_prompt_templates():
    """获取所有可用的提示词模板"""
    templates = list_prompt_templates()
    return {"templates": templates}


@router.get("/templates/{template_id}")
async def get_prompt_template_endpoint(template_id: str):
    """获取指定模板的详细内容"""
    template = get_prompt_template(template_id)
    
    if template is None or (template['id'] == 'default' and template_id != 'default'):
        raise HTTPException(status_code=404, detail="模板不存在")
    
    return {
        "id": template['id'],
        "name": template['name'],
        "system_prompt": template['system_prompt'],
        "analysis_prompt": template['analysis_prompt']
    }


@router.get("/config")
async def get_ai_config(db: Session = Depends(get_db)):
    """获取 AI 配置"""
    from db.models import Config
    config = Config.get_ai_config(db)
    # 隐藏 API Key
    if config.get('api_key'):
        config['api_key_masked'] = config['api_key'][:10] + '...' + config['api_key'][-4:]
    return config


@router.post("/config")
async def update_ai_config(data: dict, db: Session = Depends(get_db)):
    """更新 AI 配置"""
    from db.models import Config
    allowed_keys = ['provider', 'base_url', 'api_key', 'model', 'system_prompt', 'analysis_prompt']
    config = {k: v for k, v in data.items() if k in allowed_keys}
    Config.set_ai_config(db, config)
    return {"success": True}


@router.post("/models")
async def get_ai_models(data: dict, db: Session = Depends(get_db)):
    """获取可用的 AI 模型列表"""
    import httpx
    from db.models import Config
    
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
            
            # 过滤出聊天模型
            chat_models = []
            for model in models:
                model_id = model.get('id', '')
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


@router.post("/analyze/{target_id}")
async def analyze_target(target_id: int, data: dict, db: Session = Depends(get_db)):
    """对单个用户进行 AI 分析"""
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 加载 AI 配置
    from db.models import Config
    ai_config = Config.get_ai_config(db)
    
    if not ai_config.get('api_key'):
        raise HTTPException(status_code=400, detail="AI API Key 未配置")
    
    time_range = data.get('time_range', 'week')
    
    # 检查是否有足够数据
    from db.models import ReplyArchive
    reply_count = db.query(ReplyArchive).filter(
        ReplyArchive.target_id == target_id
    ).count()
    
    if reply_count < 3:
        raise HTTPException(status_code=400, detail=f"存档数据不足，仅 {reply_count} 条回复，需要至少 3 条")
    
    # 执行分析
    analyzer = AIAnalyzer(ai_config)
    result = await analyzer.analyze_user_style(target_id, time_range)
    
    if not result:
        raise HTTPException(status_code=500, detail="AI 分析失败")
    
    return result


@router.post("/compare")
async def compare_targets(data: dict, db: Session = Depends(get_db)):
    """对比分析多个用户"""
    target_ids = data.get('target_ids', [])
    if len(target_ids) < 2:
        raise HTTPException(status_code=400, detail="至少需要选择 2 个用户")
    
    # 加载 AI 配置
    from db.models import Config
    ai_config = Config.get_ai_config(db)
    if not ai_config.get('api_key'):
        raise HTTPException(status_code=400, detail="AI API Key 未配置")
    
    time_range = data.get('time_range', 'week')
    
    analyzer = AIAnalyzer(ai_config)
    result = await analyzer.compare_users(target_ids, time_range)
    
    if not result:
        raise HTTPException(status_code=500, detail="对比分析失败")
    
    return result


@router.get("/reports")
async def get_analysis_reports(
    target_id: Optional[int] = None,
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


@router.get("/reports/{report_id}")
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


@router.delete("/reports/{report_id}")
async def delete_report(report_id: int, db: Session = Depends(get_db)):
    """删除报告"""
    report = db.query(AIAnalysisReport).filter(AIAnalysisReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    
    db.delete(report)
    db.commit()
    return {"success": True}


@router.post("/sentiment-cycle/{target_id}")
async def analyze_sentiment_cycle(
    target_id: int,
    days: int = Query(default=30, ge=7, le=90),
    db: Session = Depends(get_db)
):
    """
    使用 AI 分析用户情绪周期
    """
    from datetime import datetime, timezone
    from db.models import ReplyArchive, MonitorTarget
    from ai_analyzer import AIAnalyzer
    from .analytics import _get_cycle_data_core
    
    # 获取用户信息
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 获取周期数据（使用核心函数，不是API端点）
    try:
        cycle_data = _get_cycle_data_core(target_id, days, db)
    except Exception as e:
        import logging
        logging.error(f"[SentimentCycle] 获取周期数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取周期数据失败: {str(e)}")
    
    if not cycle_data["daily_data"]:
        raise HTTPException(status_code=400, detail="该时间段没有足够的数据进行分析")
    
    # 准备 AI 分析提示词
    daily_summary = "\n".join([
        f"{d['date']}: 指数{d['index']}, 发帖{d['reply_count']}条, 关键词: {', '.join(d['keywords'][:5])}"
        for d in cycle_data["daily_data"][-14:]
    ])
    
    stats = cycle_data["statistics"]
    
    prompt = f"""请分析以下用户的情绪周期数据，给出专业的投资情绪周期分析。

用户: {cycle_data['target_name']}
时间范围: {cycle_data['date_range']['start']} 至 {cycle_data['date_range']['end']}

统计数据:
- 平均情绪指数: {stats['avg_index']:.1f} (0-100, 50为中性)
- 最高指数: {stats['max_index']:.1f}
- 最低指数: {stats['min_index']:.1f}
- 波动率: {stats['volatility']:.3f}

每日数据(最近14天):
{daily_summary}

请输出 JSON 格式分析结果:
{{
    "current_phase": "当前阶段(底部/上升/顶部/下降/震荡)",
    "current_index": {stats['avg_index']:.0f},
    "summary": "100字以内的情绪周期分析总结",
    "turning_points": [
        {{"date": "日期", "type": "peak/bottom", "description": "转折点描述"}}
    ],
    "prediction": "对未来走势的预测和投资建议",
    "confidence": 0.85
}}

分析要点:
1. 当前处于情绪周期的哪个阶段
2. 是否有明显的转折点
3. 结合发帖量和关键词分析情绪变化原因
4. 给出投资建议(仅作为情绪参考,不构成投资建议)"""
    
    try:
        analyzer = AIAnalyzer()
        messages = [
            {"role": "system", "content": "你是一位专业的投资情绪分析师,擅长分析投资者情绪周期。"},
            {"role": "user", "content": prompt}
        ]
        
        import json
        response = await analyzer._call_api(messages)
        
        if not response:
            raise HTTPException(status_code=500, detail="AI 分析失败")
        
        content = response['choices'][0]['message']['content']
        
        # 记录 AI 原始响应以便调试
        import logging
        logging.info(f"[SentimentCycle] AI 原始响应: {content[:500]}...")
        
        try:
            analysis = json.loads(content)
        except json.JSONDecodeError as e1:
            # 尝试提取 JSON
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                try:
                    analysis = json.loads(json_match.group())
                except json.JSONDecodeError as e2:
                    logging.error(f"[SentimentCycle] JSON 提取失败: {e2}, 内容: {json_match.group()[:200]}")
                    # 返回一个默认结构，避免前端崩溃
                    analysis = {
                        "current_phase": "未知",
                        "current_index": stats['avg_index'],
                        "summary": f"AI 响应解析失败，原始响应: {content[:200]}...",
                        "turning_points": [],
                        "prediction": "请检查 AI 配置或稍后重试",
                        "confidence": 0.0
                    }
            else:
                logging.error(f"[SentimentCycle] 无法从响应中提取 JSON: {content[:200]}")
                analysis = {
                    "current_phase": "未知",
                    "current_index": stats['avg_index'],
                    "summary": "AI 未返回有效 JSON 格式",
                    "turning_points": [],
                    "prediction": "请检查 AI 配置或稍后重试",
                    "confidence": 0.0
                }
        
        return {
            "target_name": cycle_data["target_name"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cycle_analysis": analysis
        }
        
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.error(f"[SentimentCycle] 情绪周期分析失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")


@router.post("/daily-sentiment/{target_id}")
async def analyze_daily_sentiment_endpoint(
    target_id: int,
    background_tasks: BackgroundTasks,
    days: int = Query(default=30, ge=7, le=90),
    db: Session = Depends(get_db)
):
    """
    分析每日情绪指数 - 后台任务
    
    Args:
        target_id: 监控目标 ID
        days: 分析天数 (7-90)
        
    Returns:
        启动状态
    """
    # 检查用户是否存在
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 检查AI配置
    from db.models import Config
    ai_config = Config.get_ai_config(db)
    if not ai_config.get('api_key'):
        raise HTTPException(status_code=400, detail="AI API Key 未配置")
    
    # 启动后台任务
    background_tasks.add_task(_run_daily_sentiment_analysis, target_id, days)
    
    return {
        "success": True,
        "message": f"已开始分析 {target.name} 的每日情绪 ({days}天)",
        "target_id": target_id,
        "days": days
    }


async def _run_daily_sentiment_analysis(target_id: int, days: int):
    """后台任务：运行每日情绪分析"""
    from ai_analyzer import analyze_daily_sentiment
    import logging
    
    logger = logging.getLogger(__name__)
    logger.info(f"[后台任务] 开始每日情绪分析: target_id={target_id}, days={days}")
    
    try:
        result = await analyze_daily_sentiment(target_id, days)
        if result:
            logger.info(f"[后台任务] 每日情绪分析完成: {result['days_analyzed']} 天")
        else:
            logger.warning("[后台任务] 每日情绪分析返回空结果")
    except Exception as e:
        logger.error(f"[后台任务] 每日情绪分析失败: {e}", exc_info=True)


@router.get("/daily-sentiment/{target_id}/status")
async def get_daily_sentiment_status(
    target_id: int,
    days: int = Query(default=30, ge=7, le=90),
    db: Session = Depends(get_db)
):
    """
    获取每日情绪分析结果
    
    Args:
        target_id: 监控目标 ID
        days: 查询天数
        
    Returns:
        每日情绪数据列表
    """
    from db.models import SentimentAnalysis, MonitorTarget
    from datetime import datetime, timedelta
    
    # 检查用户是否存在
    target = db.query(MonitorTarget).filter(MonitorTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标不存在")
    
    # 计算日期范围
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # 查询已有的情绪分析数据
    records = db.query(SentimentAnalysis).filter(
        SentimentAnalysis.target_id == target_id,
        SentimentAnalysis.date >= start_date.strftime('%Y-%m-%d'),
        SentimentAnalysis.date <= end_date.strftime('%Y-%m-%d')
    ).order_by(SentimentAnalysis.date.asc()).all()
    
    return {
        "target_id": target_id,
        "target_name": target.name,
        "days_requested": days,
        "days_analyzed": len(records),
        "data": [r.to_dict() for r in records]
    }
