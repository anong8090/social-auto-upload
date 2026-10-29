@echo off
chcp 65001 >nul
title 停止 OmniPost 服务 (Windows)

echo ==================================================
echo      🛑 正在停止 OmniPost 服务 (端口: 5409, 5173)...
echo ==================================================

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5409') do (
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5173') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo ✅ OmniPost 相关服务已全部停止。
timeout /t 3 >nul
