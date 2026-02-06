#!/bin/bash
# NGA Monitor 检查脚本
# 用于健康检查和调试

echo "=== NGA Monitor 状态检查 ==="
echo

# 检查服务是否运行
echo "1. 检查服务状态..."
if curl -sf http://localhost:12306/health > /dev/null 2>&1; then
    echo "   ✓ 服务运行正常"
    curl -s http://localhost:12306/health | python3 -m json.tool 2>/dev/null || curl -s http://localhost:12306/health
else
    echo "   ✗ 服务未响应"
fi
echo

# 检查浏览器池
echo "2. 浏览器连接池状态..."
curl -s http://localhost:12306/api/stats/browser | python3 -m json.tool 2>/dev/null || echo "   无法获取状态"
echo

# 检查限流器
echo "3. 限流器状态..."
curl -s http://localhost:12306/api/stats/rate-limiter | python3 -m json.tool 2>/dev/null || echo "   无法获取状态"
echo

# 检查数据库大小
echo "4. 数据库状态..."
if [ -f /app/data/nga_monitor.db ]; then
    DB_SIZE=$(du -h /app/data/nga_monitor.db | cut -f1)
    echo "   数据库大小: $DB_SIZE"
else
    echo "   数据库文件不存在"
fi
echo

# 检查 storage state
echo "5. Storage State 状态..."
if [ -f /app/data/storage_state.json ]; then
    echo "   ✓ 文件存在"
    echo "   修改时间: $(stat -c %y /app/data/storage_state.json 2>/dev/null || stat -f %Sm /app/data/storage_state.json 2>/dev/null)"
else
    echo "   ✗ 文件不存在"
fi
echo

# 检查日志目录
echo "6. 日志目录..."
if [ -d /app/logs ]; then
    LOG_COUNT=$(ls -1 /app/logs/*.log 2>/dev/null | wc -l)
    echo "   日志文件数: $LOG_COUNT"
else
    echo "   日志目录不存在"
fi
echo

echo "=== 检查完成 ==="
