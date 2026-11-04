# -*- coding: utf-8 -*-
"""无头发布搜狐号第 13 期（不弹窗）。

搜狐现有草稿是旧解析器产物（含笔误与列表短横线），
故这里在编辑器里重新注入修正后的正文，再点「发布」。
注意：禁止注入 set_init_script，搜狐 qiankun 微前端会白屏。
"""
import asyncio, os, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / 'omnipost'))
from playwright.async_api import async_playwright
from account_lookup import get_platform_account
from save_all_5_sohu_drafts import (
    parse_sohu_blocks, insert_sohu_inline_image, upload_sohu_cover,
    dismiss_overlays, ARTIFACT_DIR, DOCS_DIR, IMG_BASE,
)

COOKIE, _ = get_platform_account(8)
TITLE = '设计变更一来，手上那叠图该换哪几张？'
MD = DOCS_DIR / '母本B_四平台精炼长文_设计变更后图纸怎么跟.md'
COVER = IMG_BASE / '01_16x9_头图封面_设计变更后图纸怎么跟.png'
INLINE = [IMG_BASE / '02_16x9_内文图1_设计变更后图纸怎么跟.png',
          IMG_BASE / '03_16x9_内文图2_设计变更后图纸怎么跟.png']
EDITOR_URL = 'https://mp.sohu.com/mpfe/v4/contentManagement/news/addarticle'


async def main():
    blocks = parse_sohu_blocks(MD, INLINE)
    print(f'解析块数: {len(blocks)}', flush=True)

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, channel='chrome',
                                    args=['--disable-blink-features=AutomationControlled', '--lang=zh-CN'])
        ctx = await b.new_context(storage_state=str(COOKIE), viewport={'width': 1440, 'height': 900},
                                  locale='zh-CN',
                                  permissions=['clipboard-read', 'clipboard-write'])
        page = await ctx.new_page()
        await page.goto(EDITOR_URL, timeout=90000, wait_until='domcontentloaded')
        await page.wait_for_timeout(6000)
        await dismiss_overlays(page)

        title_input = page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"]').first
        await title_input.wait_for(state='visible', timeout=20000)
        await title_input.click()
        await title_input.fill(TITLE)
        print(f'标题: {TITLE} ({len(TITLE)}字)', flush=True)

        await dismiss_overlays(page)
        editor = page.locator('.ql-editor').first
        await editor.click(force=True)
        await page.keyboard.press('Meta+A')
        await page.keyboard.press('Backspace')
        await page.wait_for_timeout(400)
        for _ in range(8):
            if await page.locator('.ql-editor img').count() == 0:
                break
            await page.locator('.ql-editor img').first.click(force=True)
            await page.wait_for_timeout(150)
            await page.keyboard.press('Backspace')
            await page.wait_for_timeout(200)

        await editor.click(force=True)
        await page.wait_for_timeout(200)
        for btype, bval in blocks:
            if btype == 'text':
                await page.evaluate("async (t) => { await navigator.clipboard.writeText(t); }", str(bval) + '\n\n')
                await page.keyboard.press('Meta+V')
                await page.wait_for_timeout(700)
            else:
                await insert_sohu_inline_image(page, Path(bval))

        body = await editor.inner_text()
        imgs = await page.locator('.ql-editor img').count()
        print(f'正文注入完毕: {len(body)} 字, 内文图 {imgs} 张', flush=True)
        print(f'  笔误残留「设计一纸」: {body.count("设计一纸")} 次', flush=True)

        cover_ok = await upload_sohu_cover(page, COVER)
        print(f'封面上传: {cover_ok}', flush=True)

        try:
            ai_radio = page.locator('label.el-radio:has-text("含有AI生成内容"), '
                                    'span.el-radio__label:has-text("含有AI生成内容")').first
            await ai_radio.scroll_into_view_if_needed(timeout=5000)
            await page.wait_for_timeout(300)
            await ai_radio.click(force=True)
            print('AI 创作声明已勾选', flush=True)
        except Exception as e:
            print(f'AI 声明跳过: {e}', flush=True)

        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(ARTIFACT_DIR / '_pub_sohu_ready.png'), full_page=True)

        # 列出按钮
        btns = page.locator('button, p.positive-button, a.el-button')
        n = await btns.count()
        for i in range(n):
            bb = btns.nth(i)
            try:
                t = (bb and (await bb.inner_text()).strip())
                if t and t in ('发布', '存草稿', '预览') and await bb.is_visible():
                    print(f'  按钮[{i}]: {t!r}', flush=True)
            except Exception:
                pass

        if os.getenv('DRY') == '1':
            info = await page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('*').forEach(el => {
                    const own = Array.from(el.childNodes)
                        .filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim()).join('');
                    if (own === '发布' || own === '存草稿') {
                        const r = el.getBoundingClientRect();
                        out.push({tag: el.tagName, cls: (el.className || '').toString().slice(0, 70),
                                  own, x: Math.round(r.x), y: Math.round(r.y),
                                  w: Math.round(r.width), h: Math.round(r.height)});
                    }
                });
                return out;
            }""")
            print('--- 含「发布/存草稿」的元素 ---', flush=True)
            for it in info:
                print(' ', it, flush=True)
            print('🧪 DRY=1：已填好，不发布', flush=True)
            await b.close()
            return

        clicked = False
        # 搜狐发文页的发布按钮实际是 li.publish-report-btn.active.positive-button
        li_btn = page.locator('li.publish-report-btn.active, li.publish-report-btn.positive-button').first
        if await li_btn.count() and await li_btn.is_visible():
            txt = (await li_btn.inner_text()).strip()
            if '发布' in txt:
                await li_btn.scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                await li_btn.click(timeout=10000)
                clicked = True
                print(f'✅ 已点击「{txt}」(li.publish-report-btn)', flush=True)

        cands = page.locator('button:has-text("发布"), p:has-text("发布"), div[class*="publish"]:has-text("发布")')
        total = await cands.count()
        for i in range(total):
            c = cands.nth(i)
            try:
                t = (await c.inner_text()).strip()
                if t == '发布' and await c.is_visible():
                    await c.scroll_into_view_if_needed()
                    await page.wait_for_timeout(300)
                    await c.click(timeout=10000)
                    clicked = True
                    print(f'✅ 已点击发布按钮 [{i}]', flush=True)
                    break
            except Exception:
                continue
        if not clicked:
            # 兜底：按样式类找黄色主按钮
            for sel in ('button.positive-button', '.positive-button', 'button.el-button--warning',
                        'button.el-button--primary'):
                b2 = page.locator(sel).first
                if await b2.count() and await b2.is_visible():
                    txt = (await b2.inner_text()).strip()
                    if '发布' in txt and '定时' not in txt:
                        await b2.click(timeout=10000)
                        clicked = True
                        print(f'✅ 兜底点击发布按钮: {sel} ({txt})', flush=True)
                        break
        print(f'点击发布: {clicked}', flush=True)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=str(ARTIFACT_DIR / '_pub_sohu_after_click.png'), full_page=True)

        for _ in range(10):
            acted = await page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('button, p.positive-button'));
                const t = all.find(x => ['确定', '确认', '发布', '提交'].includes((x.innerText || '').trim())
                                        && x.offsetParent !== null);
                if (t) { t.click(); return (t.innerText || '').trim(); }
                return '';
            }""")
            if acted:
                print(f'  弹窗点击: {acted}', flush=True)
                await page.wait_for_timeout(4000)
            if 'addarticle' not in page.url:
                break
            await page.wait_for_timeout(1500)

        await page.wait_for_timeout(5000)
        print('发布后 URL:', page.url, flush=True)
        await page.screenshot(path=str(ARTIFACT_DIR / 'sohu_ep13_published.png'), full_page=True)
        await b.close()


asyncio.run(main())
