#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大鱼号扫码登录：保存 cookie 到既有账号文件，供 publish_episode.py 复用。

大鱼号会话 cookie 有效期约 10 分钟，登录后要尽快跑存草稿。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from playwright.async_api import async_playwright  # noqa: E402

from utils.base_social_media import set_init_script  # noqa: E402

ACCOUNT_FILE = BASE / "cookiesFile" / "f4a67884-d9b3-11f1-9251-95caa5137ef6.json"
DB = BASE / "db" / "database.db"


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto("https://mp.dayu.com/", timeout=60000, wait_until="domcontentloaded")
        print("🟢 请在弹出的浏览器窗口完成大鱼号登录（扫码）…", flush=True)

        deadline = 900
        logged = False
        for _ in range(deadline):
            await page.wait_for_timeout(1000)
            url = (page.url or "").lower()
            if "dashboard" in url or "publish" in url:
                logged = True
                break
        if not logged:
            print("❌ 大鱼号登录超时")
            await browser.close()
            return 1

        await page.wait_for_timeout(3000)
        state = await context.storage_state()
        ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNT_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ 大鱼号 cookie 已保存: {ACCOUNT_FILE}", flush=True)

        import sqlite3

        with sqlite3.connect(DB) as conn:
            conn.execute(
                "UPDATE user_info SET filePath = ? WHERE type = 14",
                (ACCOUNT_FILE.name,),
            )
            conn.commit()
        print("✅ user_info 已关联账号文件", flush=True)

        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
