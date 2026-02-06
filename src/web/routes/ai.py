"""
AI 分析路由
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
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
