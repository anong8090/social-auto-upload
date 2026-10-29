@echo off
chcp 65001 >nul
title 初始化 OmniPost 自媒体自动化环境 (Windows)

echo ==================================================
echo    🛠️ 正在初始化 OmniPost 自媒体自动化环境 (Windows)
echo ==================================================

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到系统 Python，请先安装 Python 3.10+ (安装时务必勾选 "Add python.exe to PATH")
    pause
    exit /b 1
)

if not exist "omnipost\.venv" (
    echo [1/3] 正在创建虚拟环境: omnipost\.venv ...
    python -m venv omnipost\.venv
) else (
    echo [1/3] 虚拟环境 omnipost\.venv 已存在，跳过创建
)

echo [2/3] 正在安装依赖包 (清华镜像加速)...
omnipost\.venv\Scripts\python.exe -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
omnipost\.venv\Scripts\pip.exe install -r omnipost\requirements_utf8.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo [3/3] 正在安装自动化浏览器内核 (Chromium)...
omnipost\.venv\Scripts\playwright.exe install chromium
omnipost\.venv\Scripts\patchright.exe install chromium >nul 2>&1

echo.
echo ==================================================
echo    🎉 环境初始化已完成！
echo    👉 下一步：双击运行 start.bat 启动控制台
echo    👉 在控制台 (http://localhost:5173) 扫码登录账号
echo ==================================================
echo.
pause
