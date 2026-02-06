# NGA Monitor 优化记录 - 第四批：代码拆分

## 已完成优化

### 代码拆分 ✅

**问题**：`web/app.py` 800+ 行，包含所有路由，维护困难

**解决方案**：按功能拆分为多个路由文件

```
src/web/
├── app.py              # 主应用入口（~100行）
├── routes/
│   ├── __init__.py     # 路由聚合
│   ├── targets.py      # 监控目标管理
│   ├── schedule.py     # 调度规则
│   ├── ai.py           # AI 分析
│   ├── archive.py      # 数据归档
│   ├── stats.py        # 统计信息
│   ├── webhook.py      # Webhook 管理
│   └── utils.py        # 工具接口
```

---

## 路由模块说明

### targets.py
- `GET /api/targets` - 获取所有目标
- `POST /api/targets` - 创建目标
- `PUT /api/targets/{id}` - 更新目标
- `DELETE /api/targets/{id}` - 删除目标
- `POST /api/targets/{id}/test` - 测试目标
- `POST /api/targets/{id}/force-send` - 强制发送
- `GET /api/targets/{id}/stats` - 目标统计

### schedule.py
- `GET /api/schedule/rules` - 获取规则
- `POST /api/schedule/rules` - 创建规则
- `PUT /api/schedule/rules/{id}` - 更新规则
- `DELETE /api/schedule/rules/{id}` - 删除规则
- `GET /api/schedule/status` - 获取状态

### ai.py
- `GET /api/ai/templates` - 提示词模板列表
- `GET /api/ai/templates/{id}` - 模板详情
- `GET /api/ai/config` - AI 配置
- `POST /api/ai/config` - 更新配置
- `POST /api/ai/models` - 获取模型列表
- `POST /api/ai/analyze/{id}` - 分析用户
- `POST /api/ai/compare` - 对比用户
- `GET /api/ai/reports` - 报告列表
- `GET /api/ai/reports/{id}` - 报告详情
- `DELETE /api/ai/reports/{id}` - 删除报告

### archive.py
- `GET /api/archive/history/{id}` - 历史回复
- `POST /api/archive/history/{id}` - 抓取历史
- `GET /api/archive/status/{id}` - 归档状态
- `GET /api/archive/tasks` - 任务列表
- `GET /api/archive/stats` - 总体统计
- `POST /api/archive/cleanup` - 清理数据
- `POST /api/archive/export/{id}` - 导出数据

### stats.py
- `GET /api/stats/` - 总体统计
- `GET /api/stats/logs` - 日志查询
- `POST /api/stats/logs/cleanup` - 清理日志
- `GET /api/stats/browser` - 浏览器池统计
- `GET /api/stats/rate-limiter` - 限流器统计

### webhook.py
- `GET /api/webhook/` - 获取配置
- `POST /api/webhook/` - 更新配置
- `POST /api/webhook/test` - 测试 webhook

### utils.py
- `POST /api/utils/parse-url` - 解析 NGA URL
- `GET /api/utils/cookie-status` - Cookie 状态

---

## 新增文件

| 文件 | 说明 |
|-----|------|
| `web/routes/__init__.py` | 路由聚合器 |
| `web/routes/targets.py` | 监控目标路由 |
| `web/routes/schedule.py` | 调度规则路由 |
| `web/routes/ai.py` | AI 分析路由 |
| `web/routes/archive.py` | 数据归档路由 |
| `web/routes/stats.py` | 统计信息路由 |
| `web/routes/webhook.py` | Webhook 路由 |
| `web/routes/utils.py` | 工具路由 |

## 修改文件

| 文件 | 修改内容 |
|-----|---------|
| `web/app.py` | 从 800+ 行简化为 ~100 行，只保留主应用入口 |

---

## 代码行数对比

| 文件 | 拆分前 | 拆分后 |
|-----|-------|-------|
| web/app.py | ~1100 行 | ~100 行 |
| web/routes/*.py | - | ~800 行（分散） |
| **总计** | **~1100 行** | **~900 行** |

**收益**：
- 每个文件职责单一
- 更容易定位和修改
- 新增功能只需修改对应文件
- 可以多人同时开发不同模块

---

## API 兼容性

**所有 API 端点路径保持不变**，无需修改前端代码。

---

## 后续扩展

如需新增功能，只需：

1. 在 `routes/` 下创建新路由文件
2. 在 `routes/__init__.py` 中注册
3. 主应用自动包含新路由

例如添加 "用户管理" 模块：

```python
# routes/users.py
from fastapi import APIRouter

router = APIRouter(prefix="/api/users", tags=["users"])

@router.get("/")
async def get_users():
    return {"users": []}

# routes/__init__.py
from .users import router as users_router
api_router.include_router(users_router)
```

---

## 全部优化完成总结

| 批次 | 优化项 | 文件变化 |
|-----|-------|---------|
| 1 | Playwright 浏览器复用 | +3, ~5 |
| 2 | 日志异步化 + DB连接 + 限流 | +2, ~5 |
| 3 | 异常细化 + 配置集中化 | +3, ~4 |
| 4 | 代码拆分 | +8, ~1 |
| **总计** | | **+16 文件, ~15 修改** |

**项目结构**：
```
src/
├── main.py
├── browser_pool.py       # 浏览器复用
├── async_logger.py       # 异步日志
├── rate_limiter.py       # 限流器
├── exceptions.py         # 异常定义
├── config_manager.py     # 配置管理
├── nga_crawler.py
├── discord_sender.py
├── ai_analyzer.py
├── monitor.py
├── schedule_manager.py
├── logger.py
├── db/
│   └── models.py
└── web/
    ├── app.py           # 主入口（~100行）
    ├── routes/          # 路由模块
    │   ├── __init__.py
    │   ├── targets.py
    │   ├── schedule.py
    │   ├── ai.py
    │   ├── archive.py
    │   ├── stats.py
    │   ├── webhook.py
    │   └── utils.py
    └── templates/

config/
└── prompts.yaml         # 提示词配置
```
