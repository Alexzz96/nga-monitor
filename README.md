# NGA Monitor

Docker 化 NGA 用户回复监控工具

## 功能

- 自动监控 NGA 指定用户的最新回复
- 新回复自动推送到 Discord Webhook
- 容器化部署，支持 OrbStack

## 快速开始

### 1. 准备 NGA 登录状态

在宿主机登录 NGA 并导出 storage state：

```bash
cd /Users/alex/.openclaw/workspace-code
python3 export_nga_state.py
```

将生成的 `nga_storage_state.json` 复制到项目目录：

```bash
cp nga_storage_state.json nga-monitor/data/storage_state.json
```

### 2. 配置环境变量

复制示例文件并修改：

```bash
cd nga-monitor
cp .env.example .env
# 编辑 .env，填入你的 Discord Webhook URL
```

### 3. 启动容器

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

## 配置说明

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL | 必填 |
| `CHECK_INTERVAL` | 检查间隔（秒） | 60 |
| `TZ` | 时区 | Asia/Shanghai |

## 数据持久化

- `./data/` - SQLite 数据库和 NGA 登录状态
- `./logs/` - 运行日志

## 第二阶段预告

- Web 管理界面（增删监控目标、启停开关）
- SQLite 数据库存储监控配置
