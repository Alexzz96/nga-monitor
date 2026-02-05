# NGA Monitor Skill

## 简介

一个完整的 NGA 论坛监控与 AI 分析系统，可作为 OpenClaw Agent 的技能使用。

---

## 功能特性

### 1. 实时监控
- 定时抓取指定 NGA 用户的最新回复
- 支持多用户同时监控
- 新回复自动推送至 Discord

### 2. AI 智能分析
- 支持 Kimi (Moonshot) 和 OpenAI
- 内置 5 种分析模板
- 支持单用户分析和多用户对比
- 分析结果自动保存

### 3. 历史归档
- 批量抓取历史回复（最多 500 条）
- 批量插入优化（性能提升 30x）
- 支持分页浏览历史回复

### 4. Web 管理
- 三页面管理界面
- 实时监控状态
- 可视化进度条

### 5. 智能调度
- 支持时间段规则
- 日间高频模式 / 夜间总结模式
- 可配置检查间隔

---

## 部署方式

### Docker 部署（推荐）

```bash
# 克隆项目
git clone https://github.com/Alexzz96/nga-monitor.git
cd nga-monitor

# 准备 NGA 登录状态
python3 export_nga_state.py
cp nga_storage_state.json data/storage_state.json

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入配置

# 启动
docker-compose up -d
```

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 初始化数据库
python3 -c "from src.db.models import init_db; init_db()"

# 启动
python3 src/main.py
```

---

## 配置说明

### 必需配置

| 配置项 | 说明 | 获取方式 |
|--------|------|----------|
| `DISCORD_WEBHOOK_URL` | Discord Webhook 地址 | Discord 频道设置 → 集成 → Webhook |
| `STORAGE_STATE_PATH` | NGA 登录状态文件 | 运行 export_nga_state.py |

### 可选配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `AI_PROVIDER` | AI 提供商 | kimi |
| `AI_BASE_URL` | API 基础地址 | https://api.moonshot.cn/v1 |
| `AI_API_KEY` | API Key | - |
| `AI_MODEL` | 模型名称 | moonshot-v1-8k |

---

## API 接口

### 监控目标管理

```
GET    /api/targets              # 获取所有监控目标
POST   /api/targets              # 创建监控目标
PUT    /api/targets/{id}          # 更新监控目标
DELETE /api/targets/{id}          # 删除监控目标
POST   /api/targets/{id}/test     # 测试发送
```

### AI 分析

```
GET    /api/ai/config            # 获取 AI 配置
POST   /api/ai/config            # 更新 AI 配置
POST   /api/ai/models            # 获取可用模型列表
POST   /api/ai/analyze/{id}      # 分析单个用户
POST   /api/ai/compare           # 对比多个用户
GET    /api/ai/reports           # 获取分析报告列表
```

### 数据归档

```
POST   /api/archive/history/{id}  # 开始抓取历史
GET    /api/archive/history/{id}  # 获取历史回复列表
GET    /api/archive/status/{id}   # 获取归档状态
GET    /api/archive/tasks         # 获取归档任务列表
POST   /api/archive/cleanup       # 清理旧数据
```

---

## 数据库模型

### MonitorTarget
监控目标表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键 |
| uid | String | NGA 用户 ID |
| name | String | 显示名称 |
| url | String | 监控 URL |
| enabled | Boolean | 是否启用 |
| check_interval | Integer | 检查间隔（秒） |

### ReplyArchive
回复存档表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键 |
| target_id | Integer | 关联监控目标 |
| pid | String | 回复 ID |
| topic_title | String | 主题标题 |
| content_full | Text | 完整内容 |
| quote_content | Text | 引用内容 |
| main_content | Text | 主内容 |
| post_date | String | 发帖时间 |

### AIAnalysisReport
AI 分析报告表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键 |
| target_id | Integer | 关联监控目标 |
| analysis_type | String | 分析类型 (single/compare) |
| time_range | String | 时间范围 |
| report_content | Text | 完整报告（JSON） |
| summary | Text | 摘要 |
| sentiment_score | Integer | 情感分数 |

---

## 开发指南

### 项目结构

```
src/
├── web/
│   ├── app.py              # FastAPI 应用
│   └── templates/          # HTML 模板
├── ai_analyzer.py          # AI 分析
├── discord_sender.py       # Discord 推送
├── monitor.py              # 监控调度
├── nga_crawler.py          # NGA 爬虫
├── db/
│   └── models.py           # 数据库模型
└── schedule_manager.py     # 调度管理
```

### 添加新的 AI 提供商

1. 在 `ai_analyzer.py` 的 `_call_api` 方法中添加支持
2. 在 Web 界面的 AI 配置页面添加选项
3. 更新提示词模板（如需要）

### 自定义提示词模板

编辑 `app.py` 中的 `AI_PROMPT_TEMPLATES` 字典，或在前端界面直接修改。

---

## 常见问题

### Q: 如何获取 NGA 登录状态？

运行项目根目录的 `export_nga_state.py` 脚本，按提示登录 NGA 即可。

### Q: Discord 推送不工作？

1. 检查 Webhook URL 是否正确
2. 检查容器是否能访问 Discord（网络问题）
3. 查看日志 `docker-compose logs -f`

### Q: AI 分析失败？

1. 检查 API Key 是否配置正确
2. 检查是否有足够的存档数据（至少 3 条）
3. 查看日志获取详细错误信息

### Q: 历史归档很慢？

正常现象，NGA 有反爬机制，每页间隔 2 秒。如需更快请调整 `delay` 参数（可能被封）。

---

## 更新日志

### v1.0 (2025-02-05)
- 初始版本发布
- 完整功能实现
- Docker 化部署

---

## 联系方式

- GitHub: https://github.com/Alexzz96/nga-monitor
- Issues: https://github.com/Alexzz96/nga-monitor/issues

---

## 许可证

MIT License
