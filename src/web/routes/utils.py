"""
工具路由
"""
import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/utils", tags=["utils"])

STORAGE_STATE_PATH = Path(os.getenv('STORAGE_STATE_PATH', '/app/data/storage_state.json'))


@router.post("/parse-url")
async def parse_url(data: dict):
    """
    从 NGA URL 解析 UID
    
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
    if matched_patterns:
        # 优先返回 uid= 的匹配（用户主页更精确）
        for match in matched_patterns:
            if 'uid=' in url.lower() and match["type"] == "用户主页":
                return {
                    "success": True, 
                    "uid": match["uid"],
                    "type": match["type"]
                }
        
        # 否则返回第一个匹配
        return {
            "success": True, 
            "uid": matched_patterns[0]["uid"],
            "type": matched_patterns[0]["type"]
        }
    
    # 如果没有匹配，尝试更宽松的匹配
    # 纯数字提取（作为后备方案）
    loose_match = re.search(r'(?:[^/]*/)*(\d{5,})', url)
    if loose_match:
        uid = loose_match.group(1)
        # 验证 UID 合理性（NGA UID 通常是 5-10 位数字）
        if 10000 <= int(uid) <= 9999999999:
            return {
                "success": True, 
                "uid": uid,
                "type": "数字提取",
                "warning": "URL 格式不标准，请确认 UID 正确"
            }
    
    raise HTTPException(status_code=400, detail="无法从 URL 解析 UID，请确保链接包含 uid= 或 authorid= 参数")


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
