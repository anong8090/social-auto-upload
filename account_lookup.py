#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多平台账号与 Cookie 动态查找工具模块 (Account Lookup Helper)
根据平台类型 (1=小红书, 5=百家号, 7=今日头条, 8=搜狐, 9=知乎) 从 SQLite 数据库或默认约定中查找有效 Cookie 文件路径。
让代码完全脱离特定用户的硬编码，适配任何同事的新电脑与新账号。
"""
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

CURRENT_DIR = Path(__file__).resolve().parent
DB_PATH = CURRENT_DIR / "omnipost" / "db" / "database.db"
COOKIES_DIR = CURRENT_DIR / "omnipost" / "cookiesFile"

PLATFORM_DEFAULT_COOKIES = {
    1: ["xiaohongshu_default.json", "xiaohongshu.json"],
    5: ["baijiahao_account.json", "baijiahao_default.json", "baijiahao.json"],
    7: ["toutiao_default.json", "toutiao.json"],
    8: ["sohu_account.json", "sohu_default.json", "sohu.json"],
    9: ["zhihu_default.json", "zhihu.json"],
}

PLATFORM_NAMES = {
    1: "小红书",
    2: "视频号",
    3: "抖音",
    4: "快手",
    5: "百家号",
    6: "B站",
    7: "今日头条",
    8: "搜狐号",
    9: "知乎",
}


def get_platform_account(platform_type: int) -> Tuple[Optional[Path], str]:
    """
    根据平台类型获取 (Cookie文件绝对路径, 账号名称)。
    1. 优先从 omnipost/db/database.db 中的 user_info 表查找已绑定的账号；
    2. 若数据库无记录或文件不存在，则在 omnipost/cookiesFile/ 中查找常规命名；
    3. 若仍未找到，返回 (None, "未登录")。
    """
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. 尝试数据库查询
    if DB_PATH.exists():
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT filePath, userName, status FROM user_info WHERE type = ? ORDER BY status DESC, id DESC LIMIT 1",
                    (platform_type,)
                )
                row = cur.fetchone()
                if row:
                    file_path = row["filePath"]
                    user_name = row["userName"] or "已登录用户"
                    target = COOKIES_DIR / file_path
                    if target.exists():
                        return target, user_name
                    # 容错：有些直接写了绝对路径或包含 cookiesFile
                    if Path(file_path).exists():
                        return Path(file_path), user_name
        except Exception:
            pass

    # 2. 尝试常规预置命名
    defaults = PLATFORM_DEFAULT_COOKIES.get(platform_type, [])
    for def_name in defaults:
        target = COOKIES_DIR / def_name
        if target.exists():
            return target, "默认账号"

    # 3. 扫描目录中匹配平台前缀的 json 文件
    p_name_en = {1: "xiaohongshu", 5: "baijiahao", 7: "toutiao", 8: "sohu", 9: "zhihu"}.get(platform_type)
    if p_name_en:
        for f in COOKIES_DIR.glob(f"*{p_name_en}*.json"):
            return f, "默认账号"

    # 4. 如果目录下只有一个 json 文件且匹配请求（或通用 uuid 格式）
    json_files = list(COOKIES_DIR.glob("*.json"))
    if len(json_files) == 1 and platform_type == 1:
        return json_files[0], "默认账号"

    return None, f"未登录 ({PLATFORM_NAMES.get(platform_type, f'平台{platform_type}')})"


if __name__ == "__main__":
    print("=== 平台账号 Cookie 探测状态 ===")
    for pt in [1, 5, 7, 8, 9]:
        path, user = get_platform_account(pt)
        pname = PLATFORM_NAMES.get(pt)
        status_icon = "✅" if path else "❌"
        print(f"{status_icon} [{pname} (type={pt})]: 账号={user}, 文件={path.name if path else '未找到'}")
