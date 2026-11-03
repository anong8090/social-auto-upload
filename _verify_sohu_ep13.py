# -*- coding: utf-8 -*-
"""核验搜狐号第 13 期最新草稿：正文是否有重复、内文图数量、封面/AI声明状态。"""
import asyncio, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / 'omnipost'))
from playwright.async_api import async_playwright
from account_lookup import get_platform_account

COOKIE, _ = get_platform_account(8)
SHOT = BASE / 'logs' / 'screenshots' / 'sohu_ep13_verify_editor.png'


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=False, channel='chrome',
            args=['--disable-blink-features=AutomationControlled', '--lang=zh-CN'])
        ctx = await b.new_context(storage_state=str(COOKIE), viewport={'width': 1440, 'height': 900}, locale='zh-CN')
        page = await ctx.new_page()
        await page.goto('https://mp.sohu.com/mpfe/v3/main/content/list', timeout=60000)
        await page.wait_for_timeout(5000)
        # 点第一条「设计变更」草稿的「编辑」
        row = page.locator('div:has-text("设计变更一来")').filter(has_text='草稿').first
        edit = page.locator('a:has-text("编辑"), span:has-text("编辑")').first
        await edit.click()
        await page.wait_for_timeout(6000)
        title = await page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"]').first.input_value()
        body = await page.locator('.ql-editor').first.inner_text()
        imgs = await page.locator('.ql-editor img').count()
        print('标题:', title, '字数', len(title))
        print('正文首句:', body.strip().split('\n')[0][:60])
        print('正文尾句:', body.strip().split('\n')[-1][:60])
        print('正文长度:', len(body))
        print('正文出现「设计一纸变更」次数:', body.count('设计一纸变更'))
        print('编辑器内文图数量:', imgs)
        cover = await page.evaluate("""() => {
            const pic = document.querySelector('.pic-cover');
            if (!pic) return 'no .pic-cover';
            const bg = getComputedStyle(pic).backgroundImage || '';
            return bg.slice(0, 80);
        }""")
        print('封面背景:', cover)
        await page.screenshot(path=str(SHOT), full_page=True)
        print('截图:', SHOT)
        await page.wait_for_timeout(1500)
        await b.close()

asyncio.run(main())
