#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================="
echo "      🛑 正在停止 OmniPost 服务...               "
echo "=================================================="

# 1. 查找并停止后端服务 (5409 端口)
BACKEND_PID=$(lsof -ti :5409 2>/dev/null)
if [ -n "$BACKEND_PID" ]; then
    echo "⏹️ 停止后端服务 (PID: $BACKEND_PID)..."
    kill -9 $BACKEND_PID 2>/dev/null
    echo "✅ 后端服务已成功停止"
else
    echo "ℹ️ 后端服务未在运行"
fi

# 2. 查找并停止前端服务 (5173 端口)
FRONTEND_PID=$(lsof -ti :5173 2>/dev/null)
if [ -n "$FRONTEND_PID" ]; then
    echo "⏹️ 停止前端服务 (PID: $FRONTEND_PID)..."
    kill -9 $FRONTEND_PID 2>/dev/null
    echo "✅ 前端服务已成功停止"
else
    echo "ℹ️ 前端服务未在运行"
fi

echo ""
echo "=================================================="
echo "   🎉 全部服务已安全退出！"
echo "=================================================="
echo ""

if [ -t 0 ]; then
    read -t 5 -n 1 -s -r -p "按任意键即可关闭此窗口 (5秒后自动关闭)..."
    echo ""
fi
