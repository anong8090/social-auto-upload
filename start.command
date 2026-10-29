#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================="
echo "      🚀 正在启动 OmniPost 自媒体分发系统...      "
echo "=================================================="

# 0. 检查虚拟环境
if [ ! -d "omnipost/.venv" ]; then
    echo "❌ 未检测到虚拟环境，请先双击运行 setup.command 进行环境初始化！"
    if [ -t 0 ]; then
        read -n 1 -s -r -p "按任意键退出..."
    fi
    exit 1
fi

# 1. 检查并启动后端服务 (5409 端口)
if lsof -Pi :5409 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo "ℹ️ 后端服务已在运行 (端口: 5409)"
else
    echo "▶️ 启动后端 API 服务..."
    nohup omnipost/.venv/bin/python omnipost/sau_backend.py </dev/null > omnipost/backend.log 2>&1 &
    disown $! 2>/dev/null || true
    # 最多等待 10 秒，每秒检查一次端口是否监听
    for i in $(seq 1 10); do
        if lsof -Pi :5409 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
            echo "✅ 后端服务启动成功 (PID: $(lsof -ti :5409))"
            break
        fi
        if [ "$i" -eq 10 ]; then
            echo "⚠️ 后端启动可能遇到问题，请检查 omnipost/backend.log"
        fi
        sleep 1
    done
fi

# 2. 检查并启动前端服务 (5173 端口)
if lsof -Pi :5173 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
    echo "ℹ️ 前端服务已在运行 (端口: 5173)"
else
    echo "▶️ 启动前端 Web 界面..."
    if [ -d "omnipost/sau_frontend/node_modules" ]; then
        (cd omnipost/sau_frontend && nohup ./node_modules/.bin/vite --host 0.0.0.0 --port 5173 </dev/null > ../frontend.log 2>&1 & disown)
    elif [ -d "omnipost/sau_frontend/dist" ]; then
        echo "ℹ️ 使用内置静态服务运行前端 (端口: 5173)..."
        nohup omnipost/.venv/bin/python -m http.server 5173 --directory omnipost/sau_frontend/dist </dev/null > omnipost/frontend.log 2>&1 &
        disown $! 2>/dev/null || true
    else
        echo "⚠️ 未找到前端 dist 目录或 node_modules，请检查 omnipost/sau_frontend"
    fi
    sleep 2
    if lsof -Pi :5173 -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        echo "✅ 前端服务启动成功 (PID: $(lsof -ti :5173 | head -n 1))"
    else
        echo "⚠️ 前端启动可能遇到问题，请检查 omnipost/frontend.log"
    fi
fi

echo ""
echo "=================================================="
echo "   🎉 全部服务已就绪！"
echo "   👉 前端面板: http://localhost:5173"
echo "   👉 后端接口: http://localhost:5409"
echo "=================================================="
echo ""

# 自动在默认浏览器中打开控制台
open "http://localhost:5173"

echo ""
echo "💡 提示: 需要停止项目时，请双击运行旁边的 stop.command"
echo ""

if [ -t 0 ]; then
    read -t 10 -n 1 -s -r -p "按任意键即可关闭此窗口 (10秒后自动关闭)..."
    echo ""
fi
