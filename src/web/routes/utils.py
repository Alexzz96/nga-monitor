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
    """从 NGA URL 解析 UID"""
    url = data.get('url', '').strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    # 支持多种 URL 格式
    import re
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
