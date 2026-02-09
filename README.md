# NGA Monitor

NGA 论坛用户回复监控与 Discord 推送系统

---

## ✨ 核心功能

| 功能 | 说明 |
|------|------|
| 📡 **实时监控** | 定时抓取指定用户的最新回复，推送至 Discord |
| 🤖 **AI 分析** | 分析用户投资风格、情绪趋势 |
| 📊 **历史归档** | 批量抓取历史回复，支持分页浏览 |
| ⏰ **智能调度** | 支持时间段规则（日间高频 / 夜间总结）|
| 🌐 **Web 管理** | FastAPI 管理界面 |

---

## 🚀 快速开始

### 1. 准备 NGA 登录状态

```bash
python3 export_nga_state.py
```

### 2. 配置环境

```bash
cp .env.example .env
# 编辑 .env，填入 DISCORD_WEBHOOK_URL
```

### 3. 启动服务

```bash
docker-compose up -d --build
```

访问：http://localhost:12306

---

## 📖 使用指南

### Web 界面

| 页面 | 地址 | 功能 |
|------|------|------|
| 监控管理 | `/` | 添加监控目标、测试推送 |
| AI 分析 | `/ai` | 配置 AI、分析用户风格 |
| 数据管理 | `/data` | 历史归档、数据清理 |

### Discord Webhook 配置

支持两种配置方式：
1. **首次**: 输入完整的 Webhook URL
2. **后续**: 只输入 ID 数字（自动复用已保存的 Token）

---

## ⚙️ 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL | - |
| `DEBUG` | 调试模式 | false |
| `TZ` | 时区 | Asia/Shanghai |

---

## 🏗️ 技术架构

```
Web 界面 (Jinja2)
       ↓
FastAPI + APScheduler
       ↓
核心模块: monitor / crawler / sender / analyzer
       ↓
SQLite (SQLAlchemy)
```

### 关键优化

- **浏览器连接池**: 复用 Playwright 实例，减少内存占用
- **批量操作**: N+1 查询优化，批量插入提升 50-100x
- **异步文件IO**: 非阻塞读取 storage state

---

## 📁 项目结构

```
├── src/
│   ├── web/           # FastAPI + 模板
│   ├── db/models.py   # 数据模型
│   ├── monitor.py     # 监控调度
│   ├── nga_crawler.py # NGA 爬虫
│   ├── discord_sender.py
│   ├── ai_analyzer.py
│   └── browser_pool.py
├── docker-compose.yml
└── README.md
```

---

## 🐳 Docker

```bash
# 启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 重建
docker-compose up -d --build
```

---

## 📝 许可证

MIT License
