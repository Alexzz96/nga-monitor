# NGA Monitor 优化记录 - 第三批

## 已完成优化

### 1. 异常处理细化 ✅

**问题**：大量 `except Exception: pass` 会吞掉关键错误

**解决方案**：
- 新建 `exceptions.py` - 定义特定的异常类型
- 异常分类：LoginExpiredError, RateLimitError, NetworkError, ParseError 等
- 异常处理工具函数 `handle_exception()`

**异常层次结构**：
```
NgaMonitorError (基类)
├── CrawlerError
│   ├── LoginExpiredError  # 需要人工介入
│   ├── RateLimitError     # 可重试
│   ├── NetworkError       # 可重试
│   └── ParseError         # 代码可能需要修复
├── SenderError
│   ├── WebhookError       # 通知可能丢失
│   └── AIAPIError         # 分析未完成
├── DatabaseError          # 严重问题
└── ConfigError
    └── ValidationError    # 数据无效
```

**使用方式**：
```python
# 旧代码
try:
    replies = await crawler.fetch_replies(url)
except Exception as e:
    logger.error(f"失败: {e}")
    continue  # 吞掉了所有错误

# 新代码
try:
    replies = await crawler.fetch_replies(url)
except LoginExpiredError as e:
    logger.error(f"登录过期: {e}")
    return {"success": False, "fatal": True}  # 致命错误
except RateLimitError as e:
    logger.warning(f"限流: {e}")
    return {"success": False, "retry_after": 60}  # 可重试
except NetworkError as e:
    logger.warning(f"网络错误: {e}")  # 临时问题
except ParseError as e:
    logger.warning(f"解析失败: {e}")  # 页面结构可能改变
```

---

### 2. 配置集中化 ✅

**问题**：AI Prompt 模板硬编码在 `web/app.py` 中（200+ 行）

**解决方案**：
- 新建 `config/prompts.yaml` - 集中管理所有提示词
- 新建 `config_manager.py` - 配置加载器

**文件结构**：
```
config/
└── prompts.yaml          # 提示词配置
```

**配置格式**：
```yaml
templates:
  standard:
    name: "标准分析"
    system_prompt: "..."
    analysis_prompt: "..."
  
  value:
    name: "价值投资分析"
    system_prompt: "..."
    analysis_prompt: "..."

defaults:
  system_prompt: "..."
  analysis_prompt: "..."
```

**API 不变**：
```bash
# 仍然可用
GET /api/ai/templates
GET /api/ai/templates/{template_id}
```

---

## 新增文件

| 文件 | 说明 |
|-----|------|
| `src/exceptions.py` | 异常类型定义 |
| `src/config_manager.py` | 配置管理器 |
| `config/prompts.yaml` | 提示词配置 |

## 修改文件

| 文件 | 修改内容 |
|-----|---------|
| `src/nga_crawler.py` | 使用特定异常类型 |
| `src/monitor.py` | 细化异常处理 |
| `src/web/app.py` | 使用配置管理器加载 prompt |
| `requirements.txt` | 添加 pyyaml |

## 剩余优化项（可选）

1. **代码拆分** - `web/app.py` 800+ 行，可以按功能拆分为多个路由文件
   - `web/routes/targets.py` - 监控目标管理
   - `web/routes/ai.py` - AI 分析
   - `web/routes/archive.py` - 数据归档
   - `web/routes/schedule.py` - 调度规则

2. **Docker 内存限制** - 添加 mem_limit 到 docker-compose.yml

---

## 如何扩展配置

添加新的提示词模板：

1. 编辑 `config/prompts.yaml`：
```yaml
templates:
  my_template:
    name: "我的分析模板"
    system_prompt: "..."
    analysis_prompt: "..."
```

2. 重启应用即可生效，无需修改代码

---

## 测试建议

```bash
# 测试配置加载
curl http://localhost:12306/api/ai/templates

# 测试特定模板
curl http://localhost:12306/api/ai/templates/standard
```
