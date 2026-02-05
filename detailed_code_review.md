# NGA Monitor 项目 - 详细代码审查报告

**审查日期**: 2026-02-05  
**审查范围**: src/web/app.py, src/monitor.py, src/nga_crawler.py, src/discord_sender.py, src/ai_analyzer.py, src/db/models.py, src/schedule_manager.py  
**审查重点**: 代码重复、性能、错误处理、资源泄漏、异步/同步混用、硬编码、日志、安全风险、可维护性

---

## 目录
1. [严重问题（需立即修复）](#1-严重问题需立即修复)
2. [高优先级问题](#2-高优先级问题)
3. [中优先级问题](#3-中优先级问题)
4. [低优先级问题（优化建议）](#4-低优先级问题优化建议)
5. [代码异味](#5-代码异味)
6. [安全审计结果](#6-安全审计结果)

---

## 1. 严重问题（需立即修复）

### 1.1 API Key 泄露到日志（安全风险）

**文件**: `src/ai_analyzer.py`  
**行号**: 34-35

**问题描述**:  
API Key 的部分内容被记录到日志文件中，即使只记录了前缀和后缀，仍存在安全风险。攻击者获取日志后可能更容易猜测或社工获取完整密钥。

**当前代码**:
```python
if self.api_key:
    logger.info(f"[AIAnalyzer] API Key 已配置: {bool(self.api_key)}")
    logger.info(f"[AIAnalyzer] API Key: {self.api_key[:10]}...{self.api_key[-4:]}")  # 泄露风险！
```

**修复代码**:
```python
if self.api_key:
    logger.info(f"[AIAnalyzer] API Key 已配置: {bool(self.api_key)}")
    # 不要记录任何 API Key 内容
    logger.debug(f"[AIAnalyzer] API Key 长度: {len(self.api_key)}")  # 仅记录长度用于调试
```

**风险等级**: 🔴 高危  
**修复优先级**: 立即

---

### 1.2 同步 HTTP 调用阻塞异步事件循环（性能问题）

**文件**: 
- `src/discord_sender.py` 第 88-92 行
- `src/ai_analyzer.py` 第 53-84 行  
- `src/web/app.py` 第 412-420 行

**问题描述**:  
在异步函数中使用 `requests` 库进行 HTTP 调用会阻塞整个事件循环。这意味着当等待网络响应时，其他所有异步任务都无法执行。

**当前代码** (`discord_sender.py`):
```python
def send_reply(self, reply):
    # ...
    response = requests.post(
        self.webhook_url,
        json={"embeds": [embed]},
        timeout=30,  # 这30秒内整个应用被阻塞！
        headers={'Content-Type': 'application/json'}
    )
```

**修复代码**:
```python
import aiohttp

class DiscordSender:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
    
    async def send_reply(self, reply):
        """异步发送回复"""
        # ... 构建 embed ...
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.webhook_url,
                json={"embeds": [embed]},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                return response.status == 204
    
    # 保持同步版本用于非异步上下文
    def send_reply_sync(self, reply):
        """同步发送回复（向后兼容）"""
        # ... 原代码 ...
```

**修复代码** (`ai_analyzer.py`):
```python
import httpx  # httpx 支持同步和异步

class AIAnalyzer:
    async def _call_api_async(self, messages: List[Dict]) -> Optional[str]:
        """异步调用 AI API"""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f'{self.api_base}/chat/completions',
                    headers={'Authorization': f'Bearer {self.api_key}'},
                    json={
                        'model': self.model,
                        'messages': messages,
                        'max_tokens': 2000
                    }
                )
                # ... 处理响应 ...
        except httpx.TimeoutException:
            logger.error("[AI API] 请求超时")
            return None
        except httpx.HTTPError as e:
            logger.error(f"[AI API] HTTP 错误: {e}")
            return None
```

**风险等级**: 🔴 高危  
**修复优先级**: 立即

---

### 1.3 SSRF（服务器端请求伪造）漏洞

**文件**: `src/web/app.py`  
**行号**: 412-420

**问题描述**:  
`/api/ai/models` 端点直接使用用户提供的 `base_url` 发起 HTTP 请求，没有验证 URL 是否指向内部服务。攻击者可能利用此漏洞访问内部网络资源。

**当前代码**:
```python
@app.post("/api/ai/models")
async def get_ai_models(data: dict, db: Session = Depends(get_db)):
    base_url = data.get('base_url', '').rstrip('/')
    api_key = data.get('api_key', '')
    
    # 没有验证 base_url！
    response = requests.get(
        f'{base_url}/models',  # 可能是 http://localhost:8080/secret
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=10
    )
```

**修复代码**:
```python
from urllib.parse import urlparse
import ipaddress

ALLOWED_HOSTS = ['api.moonshot.cn', 'api.openai.com', 'api.anthropic.com']
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('::1/128'),
]

def is_safe_url(url: str) -> bool:
    """验证 URL 是否安全"""
    try:
        parsed = urlparse(url)
        
        # 只允许 HTTPS
        if parsed.scheme != 'https':
            return False
        
        # 检查是否在允许的主机列表中
        if parsed.hostname not in ALLOWED_HOSTS:
            return False
        
        # 解析 IP 检查是否在私有网络
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            for network in BLOCKED_IP_NETWORKS:
                if ip in network:
                    return False
        except ValueError:
            # 是域名不是 IP，继续检查
            pass
        
        return True
    except Exception:
        return False

@app.post("/api/ai/models")
async def get_ai_models(data: dict, db: Session = Depends(get_db)):
    base_url = data.get('base_url', '').rstrip('/')
    
    if not is_safe_url(base_url):
        raise HTTPException(status_code=400, detail="不安全的 URL")
    
    # 继续处理...
```

**风险等级**: 🔴 高危  
**修复优先级**: 立即

---

## 2. 高优先级问题

### 2.1 数据库连接资源泄漏

**文件**: `src/schedule_manager.py`  
**行号**: 17, 20-22

**问题描述**:  
在 `__init__` 中创建数据库会话并在 `__del__` 中关闭，但 Python 的析构函数不保证调用时机和顺序，可能导致连接泄漏。

**当前代码**:
```python
class ScheduleManager:
    def __init__(self):
        self.db = SessionLocal()  # 长期持有连接
    
    def __del__(self):
        if hasattr(self, 'db'):
            self.db.close()  # 不保证调用！
```

**修复代码**:
```python
from contextlib import contextmanager

class ScheduleManager:
    """调度管理器 - 使用上下文管理器模式"""
    
    @contextmanager
    def _get_db(self):
        """获取数据库会话的上下文管理器"""
        db = SessionLocal()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    
    def get_active_rules(self) -> List[ScheduleRule]:
        """获取所有启用的调度规则"""
        with self._get_db() as db:
            return db.query(ScheduleRule).filter(
                ScheduleRule.enabled == True
            ).order_by(ScheduleRule.priority.desc()).all()
    
    # 移除 __del__ 方法
```

**风险等级**: 🟠 高  
**修复优先级**: 高

---

### 2.2 批量操作缺失导致性能问题（N+1查询）

**文件**: `src/monitor.py`  
**行号**: 177-196

**问题描述**:  
历史存档功能逐条查询数据库检查记录是否存在，然后逐条插入。当处理大量数据时，这会导致 N+1 查询问题和大量单条插入操作。

**当前代码**:
```python
# 存档到数据库
archived_count = 0
skipped_count = 0

for i, reply in enumerate(replies):
    existing = db.query(ReplyArchive).filter(
        ReplyArchive.pid == reply['pid']
    ).first()  # N 次查询！
    
    if existing:
        skipped_count += 1
        continue
    
    archive = ReplyArchive(...)
    db.add(archive)  # 单条添加
    archived_count += 1
    
    if archived_count % 50 == 0:
        db.commit()  # 频繁提交

db.commit()
```

**修复代码**:
```python
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

def archive_replies_batch(db, target_id: int, replies: List[Dict]) -> Tuple[int, int]:
    """批量存档回复，使用 INSERT OR IGNORE 优化"""
    
    # 1. 一次性查询所有已存在的 PID
    reply_pids = [r['pid'] for r in replies if r.get('pid')]
    existing_pids = {
        r.pid for r in db.query(ReplyArchive.pid).filter(
            ReplyArchive.pid.in_(reply_pids)
        ).all()
    }
    
    # 2. 过滤出新记录
    new_replies = [
        {
            'target_id': target_id,
            'pid': r['pid'],
            'tid': r.get('tid', ''),
            'topic_title': r.get('topic_title', ''),
            'content_full': r.get('content_full', ''),
            'quote_content': r.get('quote_content', ''),
            'main_content': r.get('main_content', ''),
            'forum': r.get('forum', ''),
            'post_date': r.get('post_date', ''),
            'url': r.get('url', '')
        }
        for r in replies
        if r.get('pid') and r['pid'] not in existing_pids
    ]
    
    # 3. 批量插入
    if new_replies:
        # SQLite 使用 INSERT OR IGNORE 避免重复
        stmt = sqlite_insert(ReplyArchive).values(new_replies)
        stmt = stmt.on_conflict_do_nothing(index_elements=['pid'])
        db.execute(stmt)
        db.commit()
    
    return len(new_replies), len(existing_pids)

# 使用
archived_count, skipped_count = archive_replies_batch(db, target.id, replies)
```

**性能影响**: 从 O(N) 次查询减少到 O(1) 次查询  
**修复优先级**: 高

---

### 2.3 异常静默处理掩盖错误

**文件**: `src/nga_crawler.py`  
**行号**: 44-46, 136-137, 140-141, 144-145, 150-151, 158-159

**问题描述**:  
多处使用 `except Exception: continue` 或 `except: pass` 静默忽略所有异常，这会导致错误被隐藏，难以调试。

**当前代码**:
```python
for row in rows:
    try:
        reply = await self._extract_reply(row)
        if reply:
            replies.append(reply)
    except Exception:  # 捕获所有异常
        continue  # 静默忽略 - 错误永远不会被发现！
```

**修复代码**:
```python
import logging

logger = logging.getLogger(__name__)

for row in rows:
    try:
        reply = await self._extract_reply(row)
        if reply:
            replies.append(reply)
    except Exception as e:
        # 记录错误但不中断处理
        logger.warning(f"提取回复失败: {e}", exc_info=True)
        continue
```

同样的问题存在于 `_extract_reply` 方法的多个 try-except 块中。

**修复优先级**: 高

---

### 2.4 查询参数缺乏上限限制（DoS风险）

**文件**: `src/web/app.py`  
**行号**: 231-242, 661-671

**问题描述**:  
多个 API 端点接受 `limit` 参数但没有设置上限，攻击者可以请求超大数量的数据导致内存耗尽或数据库负载过高。

**当前代码**:
```python
@app.get("/api/logs")
async def get_logs(
    level: str = None,
    target_uid: str = None,
    limit: int = 100,  # 没有最大值限制！
    db: Session = Depends(get_db)
):
    logs = query.order_by(SystemLog.created_at.desc()).limit(limit).all()
    return {"logs": [log.to_dict() for log in logs]}
```

**修复代码**:
```python
from fastapi import Query

MAX_LOG_LIMIT = 1000
MAX_ARCHIVE_LIMIT = 100

@app.get("/api/logs")
async def get_logs(
    level: str = None,
    target_uid: str = None,
    limit: int = Query(default=100, ge=1, le=MAX_LOG_LIMIT, description="返回的最大记录数"),
    db: Session = Depends(get_db)
):
    logs = query.order_by(SystemLog.created_at.desc()).limit(limit).all()
    return {"logs": [log.to_dict() for log in logs]}
```

**风险等级**: 🟠 高  
**修复优先级**: 高

---

### 2.5 未验证查询结果是否为 None

**文件**: `src/web/app.py` 多处

**问题描述**:  
多处查询数据库后直接使用结果，未检查是否为 None，可能导致 AttributeError。

**当前代码** (第 597-605 行):
```python
@app.get("/api/ai/reports/{report_id}")
async def get_report_detail(report_id: int, db: Session = Depends(get_db)):
    report = db.query(AIAnalysisReport).filter(AIAnalysisReport.id == report_id).first()
    # 没有检查 report 是否为 None！
    result = report.to_dict()  # 如果 report 是 None，这里会抛出 AttributeError
    target = db.query(MonitorTarget).filter(MonitorTarget.id == report.target_id).first()
    result['target_name'] = target.name  # 如果 target 是 None，这里也会出错
```

**修复代码**:
```python
@app.get("/api/ai/reports/{report_id}")
async def get_report_detail(report_id: int, db: Session = Depends(get_db)):
    report = db.query(AIAnalysisReport).filter(AIAnalysisReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    
    result = report.to_dict()
    target = db.query(MonitorTarget).filter(MonitorTarget.id == report.target_id).first()
    result['target_name'] = target.name if target else '未知用户'
    
    return result
```

**修复优先级**: 高

---

## 3. 中优先级问题

### 3.1 数据库连接重复创建

**文件**: `src/monitor.py`  
**行号**: 26-30, 79

**问题描述**:  
`get_webhook_from_db` 函数每次调用都创建新的数据库连接，而 `check_and_send` 中已经有一个数据库会话，应该复用。

**当前代码**:
```python
def get_webhook_from_db():
    db = SessionLocal()
    try:
        return Config.get_webhook(db)
    finally:
        db.close()

async def check_and_send(target_id, force=False):
    db = SessionLocal()
    try:
        # ...
        webhook = get_webhook_from_db()  # 又创建了一个连接！
```

**修复代码**:
```python
async def check_and_send(target_id, force=False):
    db = SessionLocal()
    try:
        # ...
        webhook = Config.get_webhook(db)  # 复用已有连接
```

**修复优先级**: 中

---

### 3.2 异常捕获过于宽泛

**文件**: `src/monitor.py`  
**行号**: 115-117

**问题描述**:  
使用 `except Exception as e` 捕获所有异常，会隐藏编程错误，使调试困难。

**当前代码**:
```python
try:
    # 50+ 行代码 ...
except Exception as e:  # 捕获一切，包括 AttributeError、TypeError 等编程错误
    logger.error(f"检查用户 {target_id} 时出错: {e}", exc_info=True)
    return {"success": False, "message": str(e)}
```

**修复代码**:
```python
from sqlalchemy.exc import SQLAlchemyError
from playwright.async_api import TimeoutError as PlaywrightTimeout

async def check_and_send(target_id, force=False):
    db = SessionLocal()
    try:
        # ... 业务逻辑 ...
        pass
    except SQLAlchemyError as e:
        logger.error(f"数据库错误: {e}", exc_info=True)
        db.rollback()
        return {"success": False, "message": f"数据库错误: {str(e)}"}
    except PlaywrightTimeout:
        logger.error("爬取超时")
        return {"success": False, "message": "爬取网页超时"}
    except ValueError as e:
        logger.error(f"数据格式错误: {e}")
        return {"success": False, "message": f"数据格式错误: {str(e)}"}
    except Exception as e:
        # 未知错误，记录完整堆栈
        logger.critical(f"未预期的错误: {e}", exc_info=True)
        return {"success": False, "message": "内部服务器错误"}
    finally:
        db.close()
```

**修复优先级**: 中

---

### 3.3 正则表达式未预编译

**文件**: `src/nga_crawler.py`  
**行号**: 123, 127

**问题描述**:  
每次调用 `_extract_reply` 都重新编译正则表达式，造成不必要的性能开销。

**当前代码**:
```python
async def _extract_reply(self, row):
    # ...
    img_pattern = r'<img[^\u003e]*data-srcorg="([^"]+)"[^\u003e]*\u003e'
    img_matches = re.findall(img_pattern, postcontent_html)
```

**修复代码**:
```python
class NgaCrawler:
    # 类级别预编译正则表达式
    IMG_PATTERN = re.compile(r'<img[^\u003e]*data-srcorg="([^"]+)"[^\u003e]*\u003e')
    IMG_PATTERN2 = re.compile(r'<img[^\u003e]*src="(https?://[^"]+)"[^\u003e]*\u003e')
    PID_PATTERN = re.compile(r'(\d+)$')
    TID_PATTERN = re.compile(r"tid=(\d+)")
    
    async def _extract_reply(self, row):
        # ...
        img_matches = self.IMG_PATTERN.findall(postcontent_html)
```

**修复优先级**: 中

---

### 3.4 重复代码 - JSON 提取逻辑

**文件**: `src/ai_analyzer.py`  
**行号**: 163-175, 272-281

**问题描述**:  
两处相同的 JSON 提取逻辑重复。

**当前代码**:
```python
# 第一次
json_start = response.find('{')
json_end = response.rfind('}') + 1
if json_start >= 0 and json_end > json_start:
    json_str = response[json_start:json_end]
    analysis_result = json.loads(json_str)
else:
    analysis_result = json.loads(response)

# 第二次（compare_users 方法中）
json_start = response.find('{')
json_end = response.rfind('}') + 1
if json_start >= 0 and json_end > json_start:
    json_str = response[json_start:json_end]
    analysis_result = json.loads(json_str)
else:
    analysis_result = {"summary": response[:500]}
```

**修复代码**:
```python
import json
from typing import Any

def extract_json_from_response(response: str, default_key: str = "summary") -> Any:
    """从 AI 响应中提取 JSON 内容"""
    response = response.strip()
    
    # 尝试找到 JSON 块
    json_start = response.find('{')
    json_end = response.rfind('}') + 1
    
    if json_start >= 0 and json_end > json_start:
        json_str = response[json_start:json_end]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    # 尝试解析整个响应
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        # 返回默认值
        return {default_key: response[:500]}

# 使用
analysis_result = extract_json_from_response(response, default_key="summary")
```

**修复优先级**: 中

---

### 3.5 重复导入

**文件**: `src/nga_crawler.py`  
**行号**: 第 7 行和第 57 行

**问题描述**:  
`asyncio` 被导入了两次。

**当前代码**:
```python
import asyncio  # 第 7 行

# ...

    async def fetch_history(self, target_url, max_pages=25, delay=2, progress_callback=None):
        import asyncio  # 第 57 行 - 重复导入
```

**修复**: 删除第 57 行的导入语句。

**修复优先级**: 中

---

### 3.6 Playwright 浏览器资源管理

**文件**: `src/nga_crawler.py`  
**行号**: 28-53

**问题描述**:  
浏览器实例创建后，如果在 `try` 块内发生异常，`browser.close()` 可能无法执行。

**当前代码**:
```python
async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True)
    context = await browser.new_context(storage_state=state)
    page = await context.new_page()
    
    try:
        # ... 操作 ...
    finally:
        await browser.close()
```

**修复代码**:
```python
async with async_playwright() as p:
    browser = await p.chromium.launch(headless=True)
    try:
        context = await browser.new_context(storage_state=state)
        page = await context.new_page()
        # ... 操作 ...
    finally:
        await browser.close()
```

**修复优先级**: 中

---

### 3.7 存储状态文件异常处理不足

**文件**: `src/nga_crawler.py`  
**行号**: 25

**问题描述**:  
读取 storage state 文件时缺乏异常处理。

**当前代码**:
```python
with open(self.storage_state_path, "r") as f:
    state = json.load(f)
```

**修复代码**:
```python
try:
    with open(self.storage_state_path, "r", encoding='utf-8') as f:
        state = json.load(f)
except FileNotFoundError:
    logger.error(f"Storage state 文件不存在: {self.storage_state_path}")
    raise RuntimeError(f"请先配置 NGA 登录状态文件: {self.storage_state_path}")
except json.JSONDecodeError as e:
    logger.error(f"Storage state 文件格式错误: {e}")
    raise RuntimeError("登录状态文件已损坏，请重新导出")
except Exception as e:
    logger.error(f"读取 storage state 失败: {e}")
    raise
```

**修复优先级**: 中

---

### 3.8 数据库会话上下文管理器缺失

**文件**: `src/db/models.py` 多处

**问题描述**:  
多处手动管理数据库会话，容易出错。

**修复代码**:
```python
from contextlib import contextmanager

@contextmanager
def get_db_session():
    """数据库会话上下文管理器"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# 使用示例
def cleanup_old_logs(days=7):
    with get_db_session() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return db.query(SystemLog).filter(SystemLog.created_at < cutoff).delete()
```

**修复优先级**: 中

---

## 4. 低优先级问题（优化建议）

### 4.1 硬编码配置值

**位置**: 
- `src/nga_crawler.py`: timeout=30000, wait_for_timeout=5000, delay=2
- `src/discord_sender.py`: 字符截断长度
- `src/ai_analyzer.py`: max_tokens=2000, 各种字符串长度限制

**建议**: 提取到配置文件或类常量。

```python
# config.py
class CrawlerConfig:
    PAGE_TIMEOUT = 30000  # ms
    WAIT_TIMEOUT = 5000   # ms
    PAGE_DELAY = 2        # seconds
    MAX_PAGES = 25

class DiscordConfig:
    MAX_TITLE_LENGTH = 256
    MAX_DESCRIPTION_LENGTH = 4096
    MAX_FIELD_LENGTH = 1024
    MAX_CONTENT_PREVIEW = 500
```

**优先级**: 低

---

### 4.2 模块内导入语句

**文件**: `src/web/app.py`  
**行号**: 409, 564

**问题描述**:  
在函数内部导入模块，不符合 PEP 8 规范。

**当前代码**:
```python
@app.post("/api/ai/models")
async def get_ai_models(...):
    import requests  # 应该在文件顶部导入
```

**修复**: 将所有导入移到文件顶部。

**优先级**: 低

---

### 4.3 提示词模板硬编码过长

**文件**: `src/web/app.py`  
**行号**: 244-400

**问题描述**:  
大量提示词模板硬编码在代码中，占用大量行数且难以维护。

**建议**: 移到单独的 YAML 或 JSON 配置文件。

```yaml
# prompts/standard.yaml
name: "标准分析（推荐）"
system_prompt: |
  你是一位资深的金融市场研究员...
analysis_prompt: |
  请分析以下用户...
```

```python
# 加载
import yaml

def load_prompt_templates():
    templates = {}
    prompts_dir = Path(__file__).parent / "prompts"
    for file in prompts_dir.glob("*.yaml"):
        with open(file) as f:
            template_id = file.stem
            templates[template_id] = yaml.safe_load(f)
    return templates
```

**优先级**: 低

---

### 4.4 魔法数字缺乏说明

**文件**: `src/ai_analyzer.py`  
**行号**: 143, 182, 228

**问题描述**:  
多处硬编码数字没有说明含义。

```python
analysis_text += f"内容: {reply.main_content[:500]}\n"  # 为什么是 500?

for i, reply in enumerate(replies[:20]):  # 为什么是 20?

logger.debug(f"[AI API] Payload: {json.dumps(payload, ensure_ascii=False)[:500]}...")
```

**建议**: 使用命名常量。

```python
MAX_CONTENT_LENGTH_PER_REPLY = 500  # 单条回复最大字符数，控制 token 使用量
MAX_REPLIES_FOR_ANALYSIS = 20       # 分析时最多使用的回复数量
MAX_LOG_LENGTH = 500                # 日志截断长度
```

**优先级**: 低

---

### 4.5 日期时间默认值使用 lambda

**文件**: `src/db/models.py`  
**行号**: 16-17

**问题描述**:  
使用 `lambda: datetime.now(timezone.utc)` 作为默认值。

**当前代码**:
```python
created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

**建议**: 使用 SQLAlchemy 的 `func.now()` 或确保时区一致。

```python
from sqlalchemy.sql import func

created_at = Column(DateTime, default=func.now())
# 或
created_at = Column(DateTime(timezone=True), default=func.now())
```

**优先级**: 低

---

### 4.6 Webhook URL 缺乏验证

**文件**: `src/discord_sender.py`  
**行号**: 14

**问题描述**:  
构造函数直接存储 URL，没有验证格式。

**建议**:
```python
from urllib.parse import urlparse

class DiscordSender:
    def __init__(self, webhook_url: str):
        parsed = urlparse(webhook_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("无效的 webhook URL")
        if not webhook_url.startswith('https://discord.com/api/webhooks/'):
            logger.warning("Webhook URL 不是标准的 Discord webhook 格式")
        self.webhook_url = webhook_url
```

**优先级**: 低

---

## 5. 代码异味

### 5.1 函数过长

**文件**: `src/web/app.py`  
**问题**: `app.py` 文件超过 700 行，包含大量端点定义。建议按功能拆分到多个路由文件。

```
src/web/
├── __init__.py
├── main.py          # FastAPI 应用初始化
├── routes/
│   ├── __init__.py
│   ├── targets.py   # 监控目标相关端点
│   ├── ai.py        # AI 分析相关端点
│   ├── archive.py   # 归档相关端点
│   ├── schedule.py  # 调度规则相关端点
│   └── config.py    # 配置相关端点
└── templates/
```

---

### 5.2 类职责不单一

**文件**: `src/web/app.py`  
**问题**: `AI_PROMPT_TEMPLATES` 字典包含在 web 模块中，应该属于 AI 分析模块。

---

### 5.3 混合关注点

**文件**: `src/monitor.py`  
**问题**: `check_and_send` 函数同时处理爬虫、数据库、Discord 发送，职责过重。建议拆分为多个函数。

---

## 6. 安全审计结果

| 检查项 | 状态 | 说明 |
|--------|------|------|
| SQL 注入 | ✅ 安全 | 使用 SQLAlchemy ORM，参数化查询 |
| XSS | ⚠️ 需注意 | 输出到 Discord 的内容需要转义 |
| SSRF | 🔴 漏洞 | `/api/ai/models` 端点存在漏洞 |
| 敏感信息泄露 | 🔴 漏洞 | API Key 泄露到日志 |
| DoS | ⚠️ 需注意 | limit 参数无上限 |
| 路径遍历 | ✅ 安全 | 使用 Path 对象，相对安全 |
| CSRF | ✅ 不适用 | 无状态 API，使用 Token |

---

## 附录：推荐的依赖更新

```txt
# requirements.txt
# 现有依赖
fastapi>=0.100.0
sqlalchemy>=2.0.0
playwright>=1.40.0
requests>=2.31.0
jinja2>=3.1.0

# 新增依赖 - 用于异步 HTTP
aiohttp>=3.9.0
httpx>=0.25.0

# 新增依赖 - 用于配置管理
pydantic-settings>=2.0.0
pyyaml>=6.0.0

# 新增依赖 - 用于安全
python-ipware>=2.0.0  # IP 地址验证
```

---

## 附录：重构优先级路线图

### Phase 1: 安全修复（1-2天）
1. 移除 API Key 日志记录
2. 修复 SSRF 漏洞
3. 添加 limit 参数上限

### Phase 2: 性能优化（2-3天）
1. 替换 requests 为 aiohttp/httpx
2. 实现批量数据库操作
3. 预编译正则表达式

### Phase 3: 代码质量（3-5天）
1. 统一数据库会话管理
2. 修复异常处理
3. 提取重复代码
4. 拆分大型模块

### Phase 4: 架构优化（1-2周）
1. 配置文件外部化
2. 添加单元测试
3. 实现依赖注入
4. 完善监控和日志
