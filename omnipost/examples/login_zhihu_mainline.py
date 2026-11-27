"""知乎主线账号重登：保存到数据库 user_info(type=9) 指向的 cookie 文件。

用法（omnipost 目录下）：
  .venv/bin/python examples/login_zhihu_mainline.py
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

OMNIPOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OMNIPOST))

from conf import BASE_DIR  # noqa: E402
from uploader.zhihu_uploader.main import zhihu_setup  # noqa: E402

DB_PATH = Path(BASE_DIR) / "db" / "database.db"


def mainline_cookie_path() -> Path:
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT filePath FROM user_info WHERE type=9 AND status=1 LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit("数据库未找到启用的知乎账号(type=9)")
    return Path(BASE_DIR) / "cookiesFile" / Path(row[0]).name


if __name__ == "__main__":
    account_file = mainline_cookie_path()
    print(f"目标 cookie 文件：{account_file}")
    ok = asyncio.run(zhihu_setup(str(account_file), handle=True))
    print(f"知乎主线登录完成：{ok}, path={account_file}")
