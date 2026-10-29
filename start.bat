@echo off
chcp 65001 >nul
title 启动 OmniPost 自媒体分发系统 (Windows)

echo ==================================================
echo       🚀 正在启动 OmniPost 自媒体分发系统 (Windows)
echo ==================================================

if not exist "omnipost\.venv" (
    echo [错误] 未检测到虚拟环境，请先双击运行 setup.bat 进行环境初始化！
    pause
    exit /b 1
)

echo [1/2] 启动后端 API 服务 (端口: 5409)...
start "OmniPost-Backend" /B omnipost\.venv\Scripts\python.exe omnipost\sau_backend.py > omnipost\backend.log 2>&1

echo [2/2] 启动前端 Web 界面 (端口: 5173)...
if exist "omnipost\sau_frontend\dist" (
    start "OmniPost-Frontend" /B omnipost\.venv\Scripts\python.exe -m http.server 5173 --directory omnipost\sau_frontend\dist > omnipost\frontend.log 2>&1
) else (
    echo [警告] 未找到前端 dist 静态目录，请检查 omnipost\sau_frontend
)

timeout /t 2 >nul
echo.
echo ==================================================
echo    ✅ OmniPost 服务已在后台启动！
echo    👉 正在为您在浏览器中打开控制台...
echo ==================================================
echo.

start http://localhost:5173
