#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================="
echo "   🛠️ 正在初始化 OmniPost 自媒体自动化环境...    "
echo "=================================================="

# 1. 检查 Python 3 环境（项目要求 >=3.10,<3.13，优先用 3.10~3.12 解释器）
PY_BIN=""
for v in 3.12 3.11 3.10; do
    p=$(command -v python$v 2>/dev/null)
    if [ -n "$p" ]; then PY_BIN="$p"; break; fi
done
if [ -z "$PY_BIN" ]; then
    if command -v python3 &>/dev/null; then
        PY_VER3=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PY_MAJOR_MINOR=$(echo "$PY_VER3" | cut -d. -f1-2)
        if python3 -c "import sys; exit(0 if (sys.version_info >= (3,10) and sys.version_info < (3,13)) else 1)" 2>/dev/null; then
            PY_BIN=$(command -v python3)
        else
            echo "❌ 系统 python3 版本为 $PY_VER3，但本项目需要 Python 3.10 ~ 3.12，请先安装（如：brew install python@3.12）"
            exit 1
        fi
    else
        echo "❌ 未检测到系统 Python3，请先安装 Python 3.10+ (推荐使用 brew install python3 或官网下载)"
        exit 1
    fi
fi

PY_VER=$("$PY_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "ℹ️ 使用 Python 版本: $PY_VER ($PY_BIN)"

# 2. 创建 Python 虚拟环境
if [ ! -d "omnipost/.venv" ]; then
    echo "▶️ 正在创建虚拟环境: omnipost/.venv ..."
    "$PY_BIN" -m venv omnipost/.venv
else
    echo "ℹ️ 虚拟环境 omnipost/.venv 已存在，跳过创建"
fi

# 3. 安装依赖包
echo "▶️ 正在安装项目依赖包 (使用清华镜像加速)..."
if ! omnipost/.venv/bin/pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet; then
    echo "❌ pip 升级失败，请检查网络或清华源可用性"
    exit 1
fi
if ! omnipost/.venv/bin/pip install -r omnipost/requirements_utf8.txt -i https://pypi.tuna.tsinghua.edu.cn/simple; then
    echo "❌ 依赖包安装失败，请检查 omnipost/requirements_utf8.txt 中的版本是否在 PyPI 上存在"
    exit 1
fi

# 4. 安装 Playwright / Patchright 浏览器内核
echo "▶️ 正在安装自动化浏览器内核 (Chromium)..."
if ! omnipost/.venv/bin/playwright install chromium; then
    echo "❌ Playwright Chromium 内核安装失败"
    exit 1
fi
if ! omnipost/.venv/bin/patchright install chromium; then
    echo "❌ Patchright Chromium 内核安装失败"
    exit 1
fi

echo ""
echo "=================================================="
echo "   🎉 环境初始化已完成！"
echo "   👉 下一步：双击运行 start.command 启动控制台"
echo "   👉 在控制台 (http://localhost:5173) 扫码登录账号"
echo "=================================================="
echo ""

if [ -t 0 ]; then
    read -t 10 -n 1 -s -r -p "按任意键即可退出此窗口 (10秒后自动关闭)..."
    echo ""
fi
