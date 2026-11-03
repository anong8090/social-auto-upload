# -*- coding: utf-8 -*-
"""核验今日头条第 13 期是否已发布上线。"""
import asyncio, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / 'omnipost'))
from playwright.async_api import async_playwright
from utils.base_social_media import set_init_script
from account_lookup import get_platform_account

COOKIE, _ = get_platform_account(7)
SHOT = BASE / 'logs' / 'screenshots' / 'toutiao_ep13_verified.png'


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, channel='chrome',
                                    args=['--disable-blink-features=AutomationControlled', '--lang=zh-CN'])
        ctx = await b.new_context(storage_state=str(COOKIE), viewport={'width': 1440, 'height': 1000})
        ctx = await set_init_script(ctx)
        page = await ctx.new_page()
        await page.goto('https://mp.toutiao.com/profile_v4/graphic/articles', timeout=60000)
        await page.wait_for_timeout(8000)
        txt = await page.evaluate("() => document.body.innerText || ''")
        hit = '设计变更一来' in txt
        print('文章列表含本期标题:', hit)
        # 打印前 8 行含标题/时间的行
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        for l in lines[:12]:
            print('  ', l[:60])
        await page.screenshot(path=str(SHOT), full_page=True)
        print('截图:', SHOT.name)
        await b.close()


asyncio.run(main())
