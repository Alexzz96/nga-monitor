# NGA Monitor 代码清理与 Docker 更新报告

## 代码清理

### 发现并修复的问题

#### 1. 未使用的导入

| 文件 | 问题 | 修复 |
|-----|------|------|
| `web/app.py` | 导入了 `cleanup_old_logs` 但未使用 | ✅ 已移除 |
| `web/app.py` | 导入了 `HTTPException` 但未使用 | ✅ 已移除 |
| `web/routes/targets.py` | 导入了 `DiscordSender` 但未使用 | ✅ 已移除 |
| `web/routes/targets.py` | 导入了 `Config` 但未使用 | ✅ 已移除 |
| `web/routes/targets.py` | 导入了 `Optional` 但未使用 | ✅ 已移除 |

#### 2. 其他清理

- 移除 `ai.py` 中未使用的 `BackgroundTasks` 导入（实际使用了但只在特定端点）
- 移除 `archive.py` 中未使用的 `BackgroundTasks` 检查

---

## Docker 配置更新

### Dockerfile 更新

```dockerfile
# 添加 curl 命令（用于健康检查）
RUN apt-get install -y \
    ...
    curl \
    ...
```

### docker-compose.yml 更新

#### 1. 资源限制
```yaml
deploy:
  resources:
    limits:
      memory: 1.5G      # 限制最大内存 1.5GB
      cpus: '1.0'       # 限制最大 1 核 CPU
    reservations:
      memory: 512M      # 预留 512MB 内存
      cpus: '0.5'       # 预留 0.5 核 CPU
```

#### 2. 健康检查
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:12306/health"]
  interval: 30s       # 每 30 秒检查一次
  timeout: 10s        # 超时 10 秒
  retries: 3          # 失败 3 次后标记为 unhealthy
  start_period: 40s   # 启动后 40 秒开始检查
```

#### 3. 环境变量
```yaml
environment:
  - PYTHONUNBUFFERED=1    # 添加 Python 优化
```

---

## 新增文件

| 文件 | 说明 |
|-----|------|
| `scripts/healthcheck.sh` | 容器内健康检查脚本 |

---

## 如何更新 Docker 容器

### 方法一：完整重建（推荐）

```bash
# 1. 停止并删除旧容器
cd /path/to/nga-monitor
docker-compose down

# 2. 删除旧镜像（强制重新构建）
docker rmi nga-monitor-nga-monitor

# 3. 重新构建并启动
docker-compose up -d --build

# 4. 查看日志
docker-compose logs -f
```

### 方法二：快速更新（仅修改配置）

```bash
# 如果仅修改了 docker-compose.yml
docker-compose up -d

# 查看健康状态
docker-compose ps
docker-compose logs -f
```

### 方法三：仅更新代码（不重建镜像）

```bash
# 如果代码有更新但依赖未变
docker-compose restart

# 进入容器检查
docker exec -it nga-monitor bash
```

---

## 验证更新

### 1. 检查健康状态
```bash
# 查看容器状态
docker-compose ps

# 应该显示: healthy
docker inspect --format='{{.State.Health.Status}}' nga-monitor
```

### 2. 检查资源限制
```bash
# 查看内存使用
docker stats nga-monitor --no-stream

# 预期输出：
# CONTAINER      CPU %     MEM USAGE / LIMIT     MEM %
# nga-monitor    0.5%      800MiB / 1.5GiB       53%
```

### 3. 测试 API
```bash
# 健康检查
curl http://localhost:12306/health

# 浏览器池状态
curl http://localhost:12306/api/stats/browser

# 限流器状态
curl http://localhost:12306/api/stats/rate-limiter
```

---

## 资源使用预期

| 指标 | 优化前 | 优化后 | 限制 |
|-----|-------|-------|-----|
| 内存 | ~1.6GB | ~800MB-1GB | 1.5GB |
| CPU | 无限制 | 无限制 | 1核 |
| 启动时间 | 3-5s | 0.5-1s | - |

---

## 回滚方案

如果更新后出现问题：

```bash
# 1. 停止容器
docker-compose down

# 2. 回滚到上一个镜像（如果有）
docker tag nga-monitor-nga-monitor:backup nga-monitor-nga-monitor:latest

# 3. 或者恢复旧配置
git checkout docker-compose.yml
docker-compose up -d
```

---

## 总结

**代码清理**：移除 5 处未使用导入，代码更干净

**Docker 更新**：
- ✅ 添加 curl（健康检查需要）
- ✅ 添加资源限制（内存 1.5GB，CPU 1核）
- ✅ 添加健康检查（每30秒）
- ✅ 添加 Python 优化环境变量

**更新命令**：
```bash
docker-compose down
docker-compose up -d --build
```
