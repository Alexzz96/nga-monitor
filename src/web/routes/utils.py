"""
工具路由
"""
import json
import os
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/utils", tags=["utils"])

STORAGE_STATE_PATH = Path(os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json'))


async def fetch_username(uid: str) -> str:
    """
    从 NGA 获取用户名
    
    策略：
    1. 先从数据库已有监控目标中查找
    2. 尝试访问用户主页获取用户名
    
    Args:
        uid: 用户 UID
        
    Returns:
        str: 用户名，获取失败返回空字符串
    """
    try:
        import sys
        import re
        sys.path.insert(0, '/app/src')
        from browser_pool import ManagedBrowserContext
        from db.models import MonitorTarget, SessionLocal
        
        # 方式1: 从数据库已有监控目标中查找
        try:
            db = SessionLocal()
            # 查找该 UID 的监控目标
            target = db.query(MonitorTarget).filter(
                MonitorTarget.uid == uid
            ).first()
            
            if target and target.name:
                print(f"[fetch_username] UID {uid}: 从数据库找到用户名 = {target.name}")
                db.close()
                return target.name
            db.close()
        except Exception as e:
            print(f"[fetch_username] UID {uid}: 数据库查询失败: {e}")
        
        # 方式2: 尝试访问用户主页获取用户名
        profile_url = f"https://nga.178.com/nuke.php?func=ucp&uid={uid}"
        
        async with ManagedBrowserContext(STORAGE_STATE_PATH, save_state_on_exit=False) as context:
            page = await context.new_page()
            try:
                await page.goto(profile_url, wait_until="networkidle", timeout=10000)
                await page.wait_for_timeout(1000)
                
                html = await page.content()
                
                # 检查是否被拦截
                if "ERROR:2048" in html or "必须登录" in html:
                    print(f"[fetch_username] UID {uid}: 登录过期或无权访问")
                    return ""
                
                # 尝试从页面标题提取
                title = await page.title()
                print(f"[fetch_username] UID {uid}: 用户主页标题 = {title}")
                
                # 如果标题不是默认的，尝试提取
                if title and title != "NGA玩家社区" and " - " in title:
                    username = title.split(" - ")[0].strip()
                    # 验证用户名合理性
                    if (username and 
                        len(username) >= 2 and 
                        len(username) <= 20 and
                        not any(word in username for word in ['NGA', '错误', '提示', '登录', '178'])):
                        print(f"[fetch_username] UID {uid}: 从主页标题提取到用户名 = {username}")
                        return username
                
                # 尝试从页面特定元素提取用户名
                selectors = [
                    'h1',  # 页面主标题
                    '.username',
                    '#username',
                    'table.userinfo td:first-child',
                ]
                
                for selector in selectors:
                    try:
                        elem = await page.locator(selector).first
                        if elem:
                            text = await elem.text_content()
                            text = text.strip() if text else ""
                            if (text and 
                                2 <= len(text) <= 20 and 
                                not text.isdigit() and
                                not any(word in text for word in ['UID', 'NGA', '错误', '提示', '178'])):
                                print(f"[fetch_username] UID {uid}: 从选择器 {selector} 提取到用户名 = {text}")
                                return text
                    except:
                        continue
                
            finally:
                await page.close()
                
    except Exception as e:
        print(f"[fetch_username] UID {uid}: 获取用户名失败: {e}")
        
    return ""


@router.post("/parse-url")
async def parse_url(data: dict):
    """
    从 NGA URL 解析 UID 和用户名
    
    支持的格式：
    - https://nga.178.com/nuke.php?func=ucp&uid=557398
    - https://nga.178.com/thread.php?searchpost=1&authorid=557398
    - https://bbs.nga.cn/nuke.php?func=ucp&uid=557398
    - https://nga.178.com/read.php?tid=1234567&authorid=557398
    """
    url = data.get('url', '').strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    import re
    
    # 清理 URL（移除空格、常见前缀）
    url = url.replace(' ', '')
    
    # 支持多种 URL 格式（按优先级排序）
    patterns = [
        # nuke.php?func=ucp&uid=xxx (用户主页)
        (r'[?&]uid=(\d+)', '用户主页'),
        # thread.php?searchpost=1&authorid=xxx (搜索页面)
        (r'[?&]authorid=(\d+)', '搜索页面'),
        # read.php?tid=xxx&authorid=xxx (帖子内)
        (r'read\.php.*[?&]authorid=(\d+)', '帖子页面'),
    ]
    
    matched_patterns = []
    
    for pattern, desc in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            uid = match.group(1)
            matched_patterns.append({
                "uid": uid,
                "type": desc,
                "pattern": pattern
            })
    
    # 如果有多个匹配，优先使用 uid= 的（更精确）
    uid = None
    match_type = None
    
    if matched_patterns:
        # 优先返回 uid= 的匹配（用户主页更精确）
        for match in matched_patterns:
            if 'uid=' in url.lower() and match["type"] == "用户主页":
                uid = match["uid"]
                match_type = match["type"]
                break
        
        # 否则返回第一个匹配
        if not uid:
            uid = matched_patterns[0]["uid"]
            match_type = matched_patterns[0]["type"]
    
    # 如果没有匹配，尝试更宽松的匹配
    # 纯数字提取（作为后备方案）
    if not uid:
        loose_match = re.search(r'(?:[^/]*/)*(\d{5,})', url)
        if loose_match:
            potential_uid = loose_match.group(1)
            # 验证 UID 合理性（NGA UID 通常是 5-10 位数字）
            if 10000 <= int(potential_uid) <= 9999999999:
                uid = potential_uid
                match_type = "数字提取"
    
    if not uid:
        raise HTTPException(status_code=400, detail="无法从 URL 解析 UID，请确保链接包含 uid= 或 authorid= 参数")
    
    # 尝试获取用户名
    username = await fetch_username(uid)
    
    result = {
        "success": True, 
        "uid": uid,
        "type": match_type
    }
    
    if username:
        result["username"] = username
    elif match_type == "数字提取":
        result["warning"] = "URL 格式不标准，请确认 UID 正确"
    
    return result


@router.get("/cookie-status")
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
