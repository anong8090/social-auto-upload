# -*- coding: utf-8 -*-
"""无头发布知乎第 13 期专栏文章（不弹窗）。

知乎的自动保存草稿在浏览器快速关闭时会丢失，故这里重新注入全文后再点「发布」。
"""
import asyncio, os, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / 'omnipost'))
from playwright.async_api import async_playwright
from account_lookup import get_platform_account
from draft_content_parser import parse_body_blocks
from save_ep10_zhihu_draft import (
    TITLE, COVER, INLINE, MD_FILE, ARTIFACT_DIR,
    paste_text, insert_inline_image, upload_topic_cover,
    close_blocking_modal, confirm_cover_dialog,
)

COOKIE, _ = get_platform_account(9)


async def main():
    blocks = parse_body_blocks(MD_FILE, INLINE, skip_cover=True)
    print(f'解析块数: {len(blocks)}', flush=True)

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, channel='chrome')
        ctx = await b.new_context(storage_state=str(COOKIE), viewport={'width': 1440, 'height': 900})
        page = await ctx.new_page()
        await page.goto('https://zhuanlan.zhihu.com/write', timeout=90000, wait_until='domcontentloaded')
        await page.wait_for_timeout(6000)

        title_in = page.locator('textarea').first
        await title_in.click()
        await title_in.fill(TITLE)
        print(f'标题: {TITLE} ({len(TITLE)}字)', flush=True)

        editor = page.locator('[contenteditable="true"]').first
        await editor.click()
        await page.wait_for_timeout(500)

        for btype, bval in blocks:
            if btype == 'text':
                await paste_text(editor, page, str(bval))
            else:
                await insert_inline_image(page, Path(bval))

        text_len = len((await editor.inner_text()).strip())
        imgs = await page.locator('[contenteditable="true"] img').count()
        print(f'正文注入完毕: {text_len} 字, 内文图 {imgs} 张', flush=True)

        await close_blocking_modal(page)
        await upload_topic_cover(page, COVER)
        await confirm_cover_dialog(page)
        await close_blocking_modal(page)

        # 创作声明
        try:
            combo = page.locator('button[role="combobox"], button.Select-button').first
            if await combo.count():
                await combo.scroll_into_view_if_needed()
                await combo.click(timeout=5000, force=True)
                await page.wait_for_timeout(600)
                opt = page.get_by_role("option", name="包含 AI 辅助创作 作者对内容负责", exact=True).first
                if await opt.count():
                    await opt.click(timeout=5000, force=True)
                    print('AI 创作声明已选择', flush=True)
        except Exception as e:
            print(f'创作声明跳过: {e}', flush=True)

        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(ARTIFACT_DIR / '_pub_zh_2_ready.png'), full_page=True)

        if os.getenv('DRY') == '1':
            print('🧪 DRY=1：已填好，不发布', flush=True)
            await b.close()
            return

        # 点击「发布」
        clicked = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const t = btns.find(x => (x.innerText || '').trim() === '发布' && x.offsetParent !== null);
            if (t) { t.scrollIntoView(); t.click(); return true; }
            return false;
        }""")
        print(f'点击发布: {clicked}', flush=True)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=str(ARTIFACT_DIR / '_pub_zh_3_after_click.png'), full_page=True)

        # 可能弹出「选择专栏」或确认框
        for _ in range(12):
            acted = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const t = btns.find(x => ['发布', '确认发布', '确定', '发布文章'].includes((x.innerText || '').trim())
                                       && x.offsetParent !== null);
                if (t) { t.click(); return (t.innerText || '').trim(); }
                return '';
            }""")
            if acted:
                print(f'  弹窗内点击: {acted}', flush=True)
                await page.wait_for_timeout(4000)
            if '/write' not in page.url:
                break
            await page.wait_for_timeout(1500)

        await page.wait_for_timeout(5000)
        print('发布后 URL:', page.url, flush=True)
        await page.screenshot(path=str(ARTIFACT_DIR / 'zhihu_ep13_published.png'), full_page=True)
        try:
            await ctx.storage_state(path=str(COOKIE))
        except Exception:
            pass
        await b.close()


asyncio.run(main())
