# -*- coding: utf-8 -*-
"""无头发布小红书第 13 期图文草稿（不弹窗）。

草稿以「暂存离开」存在持久化 profile 的 IndexedDB 里，
因此必须复用 omnipost/browser_profiles/xiaohongshu 这个目录。
"""
import asyncio, os, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / 'omnipost'))
from patchright.async_api import async_playwright
from utils.base_social_media import set_init_script

PROFILE = BASE / 'omnipost' / 'browser_profiles' / 'xiaohongshu'
SHOT = BASE / 'logs' / 'screenshots'
PUBLISH_URL = ("https://creator.xiaohongshu.com/publish/publish"
               "?source=official&from=tab_switch&target=image")


async def main():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=True,
            channel='chrome',
            viewport={'width': 1440, 'height': 900},
        )
        ctx = await set_init_script(ctx)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(PUBLISH_URL, timeout=60000)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(SHOT / '_pub_xhs_1_publish.png'), full_page=True)
        print('发布页 URL:', page.url, flush=True)

        # 打开右上角「草稿箱」
        for sel in ('text=草稿箱', 'div:has-text("草稿箱")'):
            loc = page.locator(sel).last
            if await loc.count():
                try:
                    await loc.click(timeout=5000)
                    print(f'已点击草稿箱入口: {sel}', flush=True)
                    break
                except Exception as e:
                    print(f'  点击草稿箱失败 {sel}: {e}', flush=True)
        await page.wait_for_timeout(3000)

        # 默认停在「视频笔记」标签，切到「图文笔记」
        for sel in ('text=图文笔记', 'div:has-text("图文笔记")'):
            loc = page.locator(sel).last
            if await loc.count():
                try:
                    await loc.click(timeout=5000)
                    print(f'已切换到图文笔记: {sel}', flush=True)
                    break
                except Exception as e:
                    print(f'  切换图文笔记失败: {e}', flush=True)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(SHOT / '_pub_xhs_2_draftbox.png'), full_page=True)

        # 列出版面文案，便于定位
        txt = await page.evaluate("""() => (document.body.innerText || '').slice(0, 1200)""")
        print('--- 页面文本 ---', flush=True)
        print(txt, flush=True)

        # 打开最新一条草稿（列表按时间倒序，第一条即本期）
        edit = page.get_by_text('编辑', exact=True).first
        await edit.scroll_into_view_if_needed()
        await edit.click(timeout=10000)
        await page.wait_for_timeout(7000)
        await page.screenshot(path=str(SHOT / '_pub_xhs_3_editor.png'), full_page=True)
        print('编辑器 URL:', page.url, flush=True)

        # 修正正文里的笔误「设计一纸变更下来」->「设计变更一来」
        fixed = await page.evaluate("""() => {
            const ed = document.querySelector('.ql-editor') ||
                       document.querySelector('[contenteditable="true"]');
            if (!ed) return 'no-editor';
            const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
            let node, done = 0;
            while ((node = walker.nextNode())) {
                if (node.nodeValue && node.nodeValue.includes('设计一纸变更下来')) {
                    node.nodeValue = node.nodeValue.replace('设计一纸变更下来', '设计变更一来');
                    done++;
                }
            }
            if (done) {
                ed.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
            }
            return done ? `fixed:${done}` : 'not-found';
        }""")
        print(f'  正文笔误修正: {fixed}', flush=True)
        await page.wait_for_timeout(1200)

        if os.getenv('DRY') == '1':
            btns = page.locator('button')
            n = await btns.count()
            print(f'按钮数: {n}', flush=True)
            for i in range(n):
                b = btns.nth(i)
                try:
                    t = (await b.inner_text()).strip()
                    if t and await b.is_visible():
                        print(f'  [{i}] {t!r}', flush=True)
                except Exception:
                    pass
            print('🧪 DRY=1：只探查', flush=True)
            await ctx.close()
            return

        # 点击「发布」（精确匹配，避免「发布笔记」等按钮）
        clicked = False
        btns = page.locator('button')
        n = await btns.count()
        for i in range(n):
            b = btns.nth(i)
            try:
                t = (await b.inner_text()).strip()
            except Exception:
                continue
            if t in ('发布', '发布笔记') and await b.is_visible():
                await b.scroll_into_view_if_needed()
                await b.click(timeout=10000)
                print(f'✅ 已点击「{t}」', flush=True)
                clicked = True
                break

        if clicked:
            await page.wait_for_timeout(4000)
            for _ in range(8):
                conf = page.locator('button:has-text("确认发布"), button:has-text("确定")').last
                try:
                    if await conf.count() and await conf.is_visible():
                        await conf.click(force=True, timeout=5000)
                        print('  已确认二次弹窗', flush=True)
                        await page.wait_for_timeout(4000)
                        break
                except Exception:
                    pass
                await page.wait_for_timeout(1000)
            await page.wait_for_timeout(6000)
            print('发布后 URL:', page.url, flush=True)

        await page.screenshot(path=str(SHOT / 'xhs_ep13_published.png'), full_page=True)
        print('截图: xhs_ep13_published.png', flush=True)
        await ctx.close()


asyncio.run(main())
