# -*- coding: utf-8 -*-
"""无头发布今日头条第 13 期（不弹窗）。

背景：头条草稿是点「预览」触发的，云端只存了标题+首段（62 字），正文与内文图丢失。
因此这里打开草稿后重新注入完整正文，再点「预览并发布」。
"""
import asyncio, os, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / 'omnipost'))
from playwright.async_api import async_playwright
from utils.base_social_media import set_init_script
from account_lookup import get_platform_account
from draft_content_parser import parse_body_blocks
from save_all_5_toutiao_drafts import (
    insert_text_block, insert_inline_image, upload_article_cover,
    set_ai_declaration, dismiss_masks_and_drawers,
)

COOKIE, _ = get_platform_account(7)
SHOT = BASE / 'logs' / 'screenshots'
DOCS = BASE / '河狸云蓝枢_自媒体物料包' / '文案合集' / '13_设计变更后图纸怎么跟_一稿五投'
IMGS = BASE / '河狸云蓝枢_自媒体物料包' / '图片素材' / '13_设计变更后图纸怎么跟'
MD = DOCS / '母本B_四平台精炼长文_设计变更后图纸怎么跟.md'
COVER = IMGS / '01_16x9_头图封面_设计变更后图纸怎么跟.png'
INLINE = [IMGS / '02_16x9_内文图1_设计变更后图纸怎么跟.png',
          IMGS / '03_16x9_内文图2_设计变更后图纸怎么跟.png']


async def main():
    blocks = parse_body_blocks(MD, INLINE, skip_cover=True)
    print(f'解析块数: {len(blocks)}', flush=True)

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, channel='chrome',
                                    args=['--disable-blink-features=AutomationControlled', '--lang=zh-CN'])
        ctx = await b.new_context(storage_state=str(COOKIE), viewport={'width': 1440, 'height': 900},
                                  locale='zh-CN',
                                  permissions=['clipboard-read', 'clipboard-write'])
        ctx = await set_init_script(ctx)
        page = await ctx.new_page()

        await page.goto('https://mp.toutiao.com/profile_v4/index', timeout=60000)
        await page.wait_for_timeout(6000)
        await page.get_by_text('草稿箱', exact=True).first.click(timeout=10000)
        await page.wait_for_timeout(6000)
        before = len(ctx.pages)
        await page.get_by_text('编辑', exact=True).first.click(timeout=10000)
        await page.wait_for_timeout(8000)
        if len(ctx.pages) > before:
            page = ctx.pages[-1]
            await page.wait_for_load_state('domcontentloaded')
            await page.wait_for_timeout(5000)
            print('  已切换到新标签页', flush=True)
        print('编辑器 URL:', page.url, flush=True)

        # 清空正文（头条会恢复不完整的草稿）
        pm = page.locator('.ProseMirror').first
        try:
            await pm.wait_for(state='visible', timeout=25000)
        except Exception:
            print('⚠️ .ProseMirror 未出现，尝试 contenteditable', flush=True)
            pm = page.locator('[contenteditable="true"]').first
            await pm.wait_for(state='visible', timeout=15000)
        await pm.click(force=True)
        await page.wait_for_timeout(500)
        await page.keyboard.press('Meta+A')
        await page.keyboard.press('Backspace')
        await page.wait_for_timeout(800)
        # 头条 ProseMirror 的全选删不掉图片节点，逐张点选删除
        for _ in range(8):
            if await page.locator('.ProseMirror img').count() == 0:
                break
            await page.locator('.ProseMirror img').first.click(force=True)
            await page.wait_for_timeout(300)
            await page.keyboard.press('Backspace')
            await page.wait_for_timeout(500)
        left = await page.locator('.ProseMirror img').count()
        print(f'清空后残留图片: {left} 张', flush=True)

        for btype, bval in blocks:
            if btype == 'text':
                await insert_text_block(page, str(bval))
            else:
                await insert_inline_image(page, Path(bval))

        body_len = len((await pm.inner_text()).strip())
        imgs = await page.locator('.ProseMirror img').count()
        print(f'正文重新注入完毕: {body_len} 字, 内文图 {imgs} 张', flush=True)

        await dismiss_masks_and_drawers(page)
        cover_ok = False
        for attempt in range(1, 4):
            try:
                await upload_article_cover(page, COVER)
            except Exception as e:
                print(f'  封面上传异常: {e}', flush=True)
            await page.wait_for_timeout(7000)
            st = await page.evaluate("""() => {
                const img = document.querySelector('.article-cover-img, .article-cover img, .article-cover-add img');
                const txt = document.body.innerText || '';
                return {src: img ? (img.src || '').slice(0, 70) : '', failed: txt.includes('保存失败')};
            }""")
            print(f'  封面状态[第{attempt}次]: {st}', flush=True)
            if st.get('src') and not st.get('failed'):
                cover_ok = True
                break
        print(f'封面最终结果: {cover_ok}', flush=True)
        await set_ai_declaration(page)
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(SHOT / '_pub_tt_4_refilled.png'), full_page=True)

        if os.getenv('DRY') == '1':
            print('🧪 DRY=1：已重填，不发布', flush=True)
            await b.close()
            return

        # 点「预览并发布」
        await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const t = btns.find(b => (b.innerText || '').trim() === '预览并发布');
            if (t) { t.scrollIntoView(); t.click(); }
        }""")
        print('已点击「预览并发布」', flush=True)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(SHOT / '_pub_tt_5_after_click.png'), full_page=True)

        # 预览抽屉里的确认发布
        for _ in range(10):
            done = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const t = btns.find(b => ['确认发布', '确定', '发布'].includes((b.innerText || '').trim())
                                       && b.offsetParent !== null);
                if (t) { t.click(); return (t.innerText || '').trim(); }
                return '';
            }""")
            if done:
                print(f'  已点击预览层按钮: {done}', flush=True)
                await page.wait_for_timeout(4000)
            if 'graphic/publish' not in page.url:
                break
            await page.wait_for_timeout(1500)

        await page.wait_for_timeout(6000)
        print('发布后 URL:', page.url, flush=True)
        await page.screenshot(path=str(SHOT / 'toutiao_ep13_published.png'), full_page=True)
        try:
            await ctx.storage_state(path=str(COOKIE))
        except Exception:
            pass
        await b.close()
        print('截图: toutiao_ep13_published.png', flush=True)


asyncio.run(main())
