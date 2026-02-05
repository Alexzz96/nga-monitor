# NGA Monitor

NGA 论坛用户回复监控与 AI 分析系统

实时监控指定用户的论坛回复，支持 Discord 推送、AI 智能分析和历史数据归档。

---

## ✨ 核心功能

| 功能 | 说明 |
|------|------|
| 📡 **实时监控** | 定时抓取指定用户的最新回复，推送至 Discord |
| 🤖 **AI 分析** | 基于 Kimi/OpenAI 分析用户投资风格和行为模式 |
| 📊 **历史归档** | 批量抓取历史回复（最多 500 条），支持分页浏览 |
| ⏰ **智能调度** | 支持时间段规则（日间高频检查 / 夜间总结模式） |
| 🌐 **Web 管理** | FastAPI + Jinja2 三页面管理界面 |

---

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/Alexzz96/nga-monitor.git
cd nga-monitor
```

### 2. 准备 NGA 登录状态

在宿主机登录 NGA 并导出 Playwright storage state：

```bash
# 安装 playwright
pip install playwright
playwright install chromium

# 运行导出脚本（项目中已包含）
python3 export_nga_state.py

# 复制到项目目录
cp nga_storage_state.json data/storage_state.json
```

### 3. 配置环境

```bash
cp .env.example .env
# 编辑 .env 文件，填入以下配置：
# - DISCORD_WEBHOOK_URL: Discord Webhook 地址
# - DEBUG: 是否开启调试模式 (true/false)
```

### 4. 启动服务

```bash
docker-compose up -d --build

# 查看日志
docker-compose logs -f
```

访问 Web 界面：http://localhost:12306

---

## 📖 使用指南

### Web 界面导航

| 页面 | 地址 | 功能 |
|------|------|------|
| 📡 监控管理 | `/` | 查看/添加/编辑监控目标，测试发送 |
| 🤖 AI 分析 | `/ai` | 配置 AI，单用户分析，多用户对比 |
| 📊 数据管理 | `/data` | 历史归档，查看用户历史回复，数据清理 |

### AI 分析配置

1. 进入 **AI 分析** 页面
2. 配置 AI 提供商（Kimi/OpenAI）
3. 选择**提示词模板**或自定义
4. 选择用户和时间段，点击分析

**内置模板：**
- 标准分析（推荐）- 14 个分析维度
- 深度价值投资 - 专注基本面分析
- 短线交易分析 - 专注交易行为
- 极简快速版 - 快速筛查
- 用户对比分析 - 多人对比

### 历史数据归档

1. 进入 **数据管理** 页面
2. 选择用户和抓取页数（每页约 20 条）
3. 点击"开始抓取历史"
4. 实时查看进度，完成后可浏览历史回复

---

## ⚙️ 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL | - |
| `DEBUG` | 调试模式 | false |
| `STORAGE_STATE_PATH` | NGA 登录状态文件路径 | /app/data/storage_state.json |
| `TZ` | 时区 | Asia/Shanghai |

### 调度规则

默认配置：
- **日间模式** (08:00-23:59)：每 60 秒检查
- **夜间模式** (00:00-08:00)：总结模式（时段结束推送汇总）

可在 Web 界面的 **监控管理** 页面修改规则。

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────────────────┐
│  Web 界面 (Jinja2 Templates)                              │
│  - 监控管理 / AI 分析 / 数据管理                           │
├─────────────────────────────────────────────────────────┤
│  FastAPI (Python 3.11)                                    │
│  - RESTful API                                            │
│  - 后台任务 (APScheduler)                                  │
├─────────────────────────────────────────────────────────┤
│  核心模块                                                  │
│  - monitor.py: 监控调度                                    │
│  - nga_crawler.py: NGA 爬虫 (Playwright)                   │
│  - discord_sender.py: Discord 推送 (httpx)                 │
│  - ai_analyzer.py: AI 分析引擎                             │
├─────────────────────────────────────────────────────────┤
│  数据层                                                    │
│  - SQLite (SQLAlchemy ORM)                                │
│  - 表: MonitorTarget, ReplyArchive, AIAnalysisReport...    │
└─────────────────────────────────────────────────────────┘
```

---

## 📁 项目结构

```
ga-monitor/
├── src/
│   ├── web/
│   │   ├── app.py              # FastAPI 主应用
│   │   └── templates/          # HTML 模板
│   │       ├── index.html      # 监控管理
│   │       ├── ai.html         # AI 分析
│   │       └── data.html       # 数据管理
│   ├── ai_analyzer.py          # AI 分析引擎
│   ├── discord_sender.py       # Discord 推送
│   ├── monitor.py              # 监控调度
│   ├── nga_crawler.py          # NGA 爬虫
│   ├── db/
│   │   └── models.py           # 数据库模型
│   └── schedule_manager.py     # 调度管理
├── docs/
│   ├── ai_prompt_optimization.md  # AI 提示词优化指南
│   └── prompt_templates.md        # 提示词模板库
├── data/                       # 数据目录（挂载卷）
├── docker-compose.yml
├── Dockerfile
└── README.md
```

---

## 🔒 安全注意事项

1. **API Key 保护**：AI 配置中的 API Key 不会记录到日志
2. **Cookie 安全**：NGA 登录状态文件请妥善保管
3. **Webhook 安全**：Discord Webhook URL 相当于密码，勿泄露

---

## 📝 许可证

MIT License

---

## 🤝 贡献

欢迎提交 Issue 和 PR！

---

## 🙏 致谢

- [Playwright](https://playwright.dev/) - 浏览器自动化
- [FastAPI](https://fastapi.tiangolo.com/) - Web 框架
- [Kimi](https://kimi.moonshot.cn/) / [OpenAI](https://openai.com/) - AI 能力
