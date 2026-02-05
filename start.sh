#!/bin/bash
# 快速启动脚本

cd "$(dirname "$0")"

echo "================================"
echo "NGA Monitor 快速启动"
echo "================================"
echo ""

# 检查 .env 文件
if [ ! -f .env ]; then
    echo "⚠️  .env 文件不存在，从示例创建..."
    cp .env.example .env
    echo "请编辑 .env 文件，填入你的 Discord Webhook URL"
    exit 1
fi

# 检查 storage state
if [ ! -f data/storage_state.json ]; then
    echo "❌ 错误: data/storage_state.json 不存在"
    echo "请先运行 export_nga_state.py 生成登录状态，然后复制到 data/ 目录"
    exit 1
fi

echo "启动 Docker 容器..."
docker-compose up -d

echo ""
echo "✅ 容器已启动"
echo "查看日志: docker-compose logs -f"
echo "停止: docker-compose down"
