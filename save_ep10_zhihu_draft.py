#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本期《工地图纸多找详图难》知乎专栏草稿保存：
脚本自行启动浏览器，仅自动填写内容 + 上传封面 + 勾选 AI 创作声明，存入知乎官方草稿箱，绝不点击发布。
填好后浏览器保持打开，由你手动点击「发布」。
"""
import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))

from playwright.async_api import async_playwright
from uploader.zhihu_uploader.main import ZhiHuArticle
from account_lookup import get_platform_account

DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '12_工地图纸多找详图难_一稿五投'
IMG_BASE = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材' / '12_工地图纸多找详图难'
COOKIE_PATH, ACCOUNT_USER = get_platform_account(9)
ARTIFACT_DIR = BASE_DIR / 'logs' / 'screenshots'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

TITLE = '工地图纸几十张，找详图翻半天怎么办？'
MD_FILE = DOCS_DIR / '母本B_四平台精炼长文_工地图纸多找详图难.md'
COVER = IMG_BASE / '01_16x9_头图封面_工地图纸多找详图难.png'
INLINE = [
    IMG_BASE / '02_16x9_内文图1_工地图纸多找详图难.png',
    IMG_BASE / '03_16x9_内文图2_工地图纸多找详图难.png',
]


def parse_blocks():
    blocks, cur = [], []
    img_idx = -1
    re = __import__('re')
    for line in MD_FILE.read_text(encoding='utf-8').splitlines():
        s = line.strip()
        if s.startswith('# ') or s == '---' or (s.startswith('>') and img_idx < 0):
            continue
        if s.startswith('!['):
            img_idx += 1
            if img_idx == 0:
                continue
            if cur:
                blocks.append(('text', '\n'.join(cur).strip()))
                cur = []
            blocks.append(('image', INLINE[img_idx - 1]))
            continue
        cl = re.sub(r'^#{2,4}\s*', '', s).replace('**', '')
        if cl.startswith('|'):
            continue
        if cl:
            cur.append(cl)
    if cur:
        blocks.append(('text', '\n'.join(cur).strip()))
    return blocks


async def paste_text(editor, page, text):
    await page.evaluate(
        """(t) => {
            const el = document.querySelector('.public-DraftEditor-content') || document.querySelector('[contenteditable="true"]');
            const dt = new DataTransfer();
            dt.setData('text/plain', t);
            el.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
        }""", text + '\n\n')
    await page.wait_for_timeout(800)


async def insert_inline_image(page, img_path: Path):
    try:
        # 点击图片按钮（尝试多个选择器）
        btn = None
        for sel in ['button:has-text("图片")', 'button[title*="图片"]', '.ql-image', '[class*="image"] button']:
            b = page.locator(sel).first
            if await b.count() and await b.is_visible():
                btn = b
                break
        if not btn:
            print('    ⚠️ 未找到图片按钮，跳过', flush=True)
            return False
        await btn.click(force=True, timeout=4000)
        await page.wait_for_timeout(1500)

        # 等待文件选择器出现
        fi = None
        for _ in range(20):
            await page.wait_for_timeout(500)
            candidates = [
                page.locator('input[accept*="image/webp"]').last,
                page.locator('input[accept^="image/"]').last,
                page.locator('input[type="file"]').last,
            ]
            for c in candidates:
                if await c.count():
                    fi = c
                    break
            if fi:
                break

        if not fi or not await fi.count():
            print('    ⚠️ 未找到图片 input，跳过', flush=True)
            return False

        await fi.set_input_files(str(img_path), timeout=6000)
        await page.wait_for_timeout(5000)
        n = await page.locator('[contenteditable="true"] img').count()
        print(f'    📤 内文图已上传: {img_path.name} (编辑器图片数={n})', flush=True)
        return n > 0
    except Exception as e:
        print(f'    ⚠️ 内文图失败(跳过): {e}', flush=True)
        return False


async def main():
    blocks = parse_blocks()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel='chrome')
        ctx = await browser.new_context(storage_state=str(COOKIE_PATH),
                                        viewport={'width': 1440, 'height': 900})
        page = await ctx.new_page()
        await page.goto('https://zhuanlan.zhihu.com/write', timeout=90000, wait_until='domcontentloaded')
        await page.wait_for_timeout(4000)
        if 'login' in page.url or 'signin' in page.url:
            raise RuntimeError('知乎 cookie 已失效')

        title_in = page.locator('textarea').first
        await title_in.click()
        await title_in.fill(TITLE)
        print(f'  ✍️ 标题: {TITLE} ({len(TITLE)}字)', flush=True)

        editor = page.locator('[contenteditable="true"]').first
        await editor.click()

        for btype, bval in blocks:
            if btype == 'text':
                await paste_text(editor, page, str(bval))
            else:
                await insert_inline_image(page, Path(bval))

        text_len = len((await editor.inner_text()).strip())
        print(f'  📝 正文注入完毕，编辑器字数: {text_len}', flush=True)

        try:
            await page.locator('button:has-text("封面")').first.click(force=True, timeout=4000)
            await page.wait_for_timeout(1000)
            cover_input = page.locator('input[type="file"]').last
            if await cover_input.count():
                await cover_input.set_input_files(str(COVER), timeout=6000)
                await page.wait_for_timeout(3000)
                print(f'  🖼️ 封面已上传: {COVER.name}', flush=True)
        except Exception as e:
            print(f'  ⚠️ 封面上传失败(继续): {e}', flush=True)

        try:
            for _ in range(10):
                if await page.locator('label:has-text("创作声明")').count():
                    break
                await page.mouse.wheel(0, 500)
                await page.wait_for_timeout(200)
            combo = page.locator('button[role="combobox"], button.Select-button').first
            if await combo.count():
                await combo.scroll_into_view_if_needed()
                await combo.click(timeout=5000, force=True)
                await page.wait_for_timeout(500)
                option = page.get_by_role("option", name="包含 AI 辅助创作 作者对内容负责", exact=True).first
                if await option.count():
                    await option.click(timeout=5000, force=True)
                    await page.wait_for_timeout(400)
                    print('  ✅ 已选择创作声明: 包含 AI 辅助创作 作者对内容负责', flush=True)
        except Exception as e:
            print(f'  ⚠️ 创作声明设置失败(继续): {e}', flush=True)

        await page.wait_for_timeout(3000)
        shot = ARTIFACT_DIR / 'zhihu_ep12_draft_ready.png'
        await page.screenshot(path=str(shot), full_page=True)
        print(f'  📸 发布页截图已保存: {shot.name}（不自动发布，浏览器保持打开，请手动点击发布）', flush=True)

        print('\n✅ 知乎内容已填写完毕，发布页保持打开，请手动点击发布。', flush=True)
        print('   ⚠️ 注意：不自动保存草稿，不自动发布，由你在后台点击「发布」。', flush=True)
        print('   🌐 浏览器将保持打开，请在页面上手动发布完成后再关闭本窗口。', flush=True)

        await ctx.storage_state(path=str(COOKIE_PATH))

        print('   ⏳ 等待中，浏览器保持打开，请手动发布后关闭窗口...', flush=True)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    asyncio.run(main())
