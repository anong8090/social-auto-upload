#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打开持久化浏览器窗口并直接定位到小红书创作服务平台「草稿箱 -> 图文笔记(5)」。
由于小红书网页端草稿存储在浏览器本地 IndexedDB 中（不存云端），
必须使用同一持久化 user_data_dir 打开才能看到并操作这 5 篇草稿。
"""
import asyncio
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))
from patchright.async_api import async_playwright
from utils.base_social_media import set_init_script
from account_lookup import get_platform_account

PERSISTENT_PROFILE_DIR = BASE_DIR / 'omnipost' / 'browser_profiles' / 'xiaohongshu'
COOKIE_PATH, _ = get_platform_account(1)


async def main():
    PERSISTENT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PERSISTENT_PROFILE_DIR),
            headless=False,
            channel="chrome",
            permissions=["geolocation", "clipboard-read", "clipboard-write"],
            viewport={"width": 1440, "height": 900},
        )
        if COOKIE_PATH and COOKIE_PATH.exists():
            state = json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
            if state.get("cookies"):
                await context.add_cookies(state["cookies"])
        context = await set_init_script(context)
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto(
            "https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch&target=image",
            timeout=60000,
        )
        await page.wait_for_timeout(3000)

        try:
            draft_box_btn = page.locator('text=/草稿箱/').first
            if await draft_box_btn.count() and await draft_box_btn.is_visible():
                await draft_box_btn.click()
                await page.wait_for_timeout(1500)
                img_note_tab = page.locator('text=/图文笔记/').first
                if await img_note_tab.count() and await img_note_tab.is_visible():
                    await img_note_tab.click()
                    await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"打开草稿箱提示: {e}")

        print("✅ 已打开小红书持久化浏览器窗口并定位至「草稿箱 -> 图文笔记」，窗口将保持打开供您查看与发布。")
        # 保持窗口常驻，直到用户手动关闭浏览器
        try:
            while context.pages:
                await asyncio.sleep(2)
        except Exception:
            pass


if __name__ == '__main__':
    asyncio.run(main())
