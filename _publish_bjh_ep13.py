# -*- coding: utf-8 -*-
"""无头发布百家号第 13 期草稿（不弹窗）。

流程：草稿箱列表 -> 打开最新一条本期草稿 -> 点击「发布」。
用户已明确要求直接发布，故此处允许点击发布按钮。
"""
import asyncio, os, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / 'omnipost'))
from playwright.async_api import async_playwright
from account_lookup import get_platform_account

COOKIE, _ = get_platform_account(5)
TITLE_KEY = '设计变更一来'
SHOT_DIR = BASE / 'logs' / 'screenshots'
LIST_URL = ("https://baijiahao.baidu.com/builder/rc/content"
            "?currentPage=1&pageSize=10&search=&type=news&collection=draft")


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, channel='chrome',
                                    args=['--disable-blink-features=AutomationControlled', '--lang=zh-CN'])
        ctx = await b.new_context(storage_state=str(COOKIE), viewport={'width': 1440, 'height': 900}, locale='zh-CN')
        page = await ctx.new_page()
        await page.goto(LIST_URL, timeout=60000)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=str(SHOT_DIR / '_pub_bjh_list.png'), full_page=True)

        rows = await page.locator(f'div:has-text("{TITLE_KEY}")').count()
        print(f'草稿箱中含「{TITLE_KEY}」的元素数: {rows}', flush=True)

        # 草稿列表按时间倒序，第一条即最新；操作按钮文案是「修改」
        edit = page.locator('a:has-text("修改"), span:has-text("修改"), button:has-text("修改")').first
        if not await edit.count():
            edit = page.get_by_text('修改', exact=True).first
        print('点击「修改」进入编辑页...', flush=True)
        before = len(ctx.pages)
        await edit.click(timeout=15000)
        await page.wait_for_timeout(6000)
        if len(ctx.pages) > before:
            page = ctx.pages[-1]
            await page.wait_for_load_state('domcontentloaded')
            print('  已切换到新标签页', flush=True)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=str(SHOT_DIR / '_pub_bjh_editor.png'))
        print('编辑页 URL:', page.url, flush=True)

        # 找发布按钮（排除「存草稿」「预览」）
        candidates = page.locator('button:has-text("发布")')
        n = await candidates.count()
        print(f'含「发布」的按钮数: {n}', flush=True)
        for i in range(n):
            btn = candidates.nth(i)
            try:
                txt = (await btn.inner_text()).strip()
                vis = await btn.is_visible()
                print(f'  [{i}] text={txt!r} visible={vis}', flush=True)
            except Exception:
                pass

        if os.getenv('DRY') == '1':
            print('🧪 DRY=1：只探查不发布', flush=True)
            await b.close()
            return

        clicked = False
        for i in range(n):
            btn = candidates.nth(i)
            txt = (await btn.inner_text()).strip()
            if txt in ('发布', '确认发布', '立即发布') and await btn.is_visible():
                await btn.scroll_into_view_if_needed()
                await btn.click(timeout=10000)
                clicked = True
                print(f'✅ 已点击「{txt}」按钮', flush=True)
                break

        if clicked:
            await page.wait_for_timeout(4000)
            # 可能弹出二次确认
            for _ in range(6):
                conf = page.locator('button:has-text("确定"), button:has-text("确认发布")').last
                try:
                    if await conf.count() and await conf.is_visible():
                        await conf.click(force=True, timeout=5000)
                        print('  已确认二次弹窗', flush=True)
                        await page.wait_for_timeout(3000)
                        break
                except Exception:
                    pass
                await page.wait_for_timeout(1000)

            await page.wait_for_timeout(5000)
            print('发布后 URL:', page.url, flush=True)

        await page.screenshot(path=str(SHOT_DIR / 'bjh_ep13_published.png'), full_page=True)
        await ctx.storage_state(path=str(COOKIE))
        await b.close()
        print('截图: bjh_ep13_published.png', flush=True)


asyncio.run(main())
