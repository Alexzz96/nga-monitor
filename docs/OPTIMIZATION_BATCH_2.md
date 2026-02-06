# NGA Monitor 优化记录 - 第二批

## 已完成优化

### 1. 日志异步化 ✅

**问题**：`DatabaseLogHandler.emit` 同步写入数据库，会阻塞主线程

**解决方案**：
- 新建 `async_logger.py` - 异步日志处理器
- 使用独立线程 + 队列批量写入
- 队列满了自动丢弃最旧日志
- 批量写入（每5秒/每10条）

**关键配置**：
```python
flush_interval=5      # 5秒批量写入一次
max_queue_size=1000   # 队列最多1000条
batch_size=10         # 每批最多10条
```

**效果**：日志写入不再阻塞主线程

---

### 2. 数据库连接管理修复 ✅

**问题**：`ScheduleManager.__del__` 不可靠，可能导致连接泄漏

**解决方案**：
- 使用 `contextmanager` 管理会话生命周期
- 每次方法调用新建连接，用完立即关闭
- 自动回滚异常事务

```python
@contextmanager
def get_db_session():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

**效果**：消除连接泄漏风险

---

### 3. API 限流保护 ✅

**问题**：Discord Webhook 和 AI API 缺少 rate limiting

**解决方案**：
- 新建 `rate_limiter.py` - 令牌桶限流器
- 支持瞬时限流（每秒）+ 长期限流（每分钟）

**配置**：
```python
# Discord Webhook
requests_per_second=0.5   # 每2秒1个
requests_per_minute=30    # 每分钟30个
burst_size=2              # 最多突发2个

# AI API  
requests_per_second=0.2   # 每5秒1个
requests_per_minute=20    # 每分钟20个
burst_size=1              # 不突发
```

**效果**：防止被封禁/限流

---

### 4. 重复代码合并 ✅

**问题**：`_bulk_archive_replies` 和 `_bulk_archive_replies_with_stats` 90%重复

**解决方案**：
- 合并为统一函数，通过 `return_stats` 参数控制
- 第二个函数作为兼容别名保留

```python
async def _bulk_archive_replies(db, target_id, replies, return_stats=False):
    # 统一实现
    ...
    return (len(new_replies), skipped_count) if return_stats else len(new_replies)

# 向后兼容
async def _bulk_archive_replies_with_stats(...):
    return await _bulk_archive_replies(..., return_stats=True)
```

---

## 新增文件

| 文件 | 说明 |
|-----|------|
| `src/async_logger.py` | 异步日志处理器 |
| `src/rate_limiter.py` | 令牌桶限流器 |

## 修改文件

| 文件 | 修改内容 |
|-----|---------|
| `src/logger.py` | 使用异步处理器 |
| `src/schedule_manager.py` | 修复连接管理 |
| `src/discord_sender.py` | 添加限流保护 |
| `src/ai_analyzer.py` | 添加限流保护 |
| `src/monitor.py` | 合并重复函数 |
| `src/main.py` | 添加日志关闭逻辑 |
| `src/web/app.py` | 添加限流统计端点 |

## 新增 API 端点

- `GET /api/stats/rate-limiter` - 限流器统计

## 下一批优化建议

1. **异常处理细化** - 区分可忽略异常和致命错误
2. **配置集中化** - Prompt 模板移到配置文件
3. **代码结构优化** - 拆分 web/app.py

---

## 测试建议

```bash
# 查看限流统计
curl http://localhost:12306/api/stats/rate-limiter

# 查看浏览器池状态
curl http://localhost:12306/api/stats/browser

# 查看整体健康状态
curl http://localhost:12306/health
```
