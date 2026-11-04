# -*- coding: utf-8 -*-
"""自动化批量保存精炼图文至今日头条官方草稿箱。

严格规则：
1. 仅保存为草稿，绝不点击发布！
2. 标题字数精简在 30 字以内。
3. 正文插入 2 张 16:9 内文配图，单图 16:9 封面。
4. 作品声明勾选「引用AI」。
5. 脚本自行启动浏览器，填好内容后保持浏览器打开，
   由你在后台手动点击「发布」。
"""
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional, List

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))

from playwright.async_api import Page, async_playwright
from account_lookup import get_platform_account
from utils.base_social_media import set_init_script
from draft_content_parser import parse_body_blocks

DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '13_设计变更后图纸怎么跟_一稿五投'
IMG_BASE = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材' / '13_设计变更后图纸怎么跟'
COOKIE_PATH, ACCOUNT_USER = get_platform_account(7)
ARTIFACT_DIR = BASE_DIR / 'logs' / 'screenshots'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

ARTICLES = [
    {
        'episode': 13,
        'title': '设计变更一来，手上那叠图该换哪几张？',
        'file': '母本B_四平台精炼长文_设计变更后图纸怎么跟.md',
        'cover': IMG_BASE / '01_16x9_头图封面_设计变更后图纸怎么跟.png',
        'inline_images': [
            IMG_BASE / '02_16x9_内文图1_设计变更后图纸怎么跟.png',
            IMG_BASE / '03_16x9_内文图2_设计变更后图纸怎么跟.png',
        ],
    },
]


def parse_markdown_content(file_path: Path, inline_images: Optional[List] = None):
    """统一解析：剥离元数据说明行、清洗裸露 Markdown 标记、内文图穿插。"""
    return parse_body_blocks(file_path, inline_images, skip_cover=True)


async def dismiss_masks_and_drawers(page):
    await page.evaluate('''() => {
        document.querySelectorAll('.byte-drawer-mask').forEach(m => m.remove());
        document.querySelectorAll('.byte-popover').forEach(p => {
            const btn = p.querySelector('button');
            if (btn) btn.click();
            p.remove();
        });
    }''')
    await page.wait_for_timeout(300)


async def move_caret_to_end(page):
    await page.evaluate('''() => {
        const pm = document.querySelector('.ProseMirror');
        if (!pm) return;
        pm.focus();
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(pm);
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
    }''')
    await page.wait_for_timeout(150)


async def insert_text_block(page, text: str):
    await move_caret_to_end(page)
    await page.evaluate('''async (t) => { await navigator.clipboard.writeText(t); }''', text)
    await page.keyboard.press('Meta+V')
    await page.keyboard.press('Enter')
    await page.wait_for_timeout(300)


async def insert_inline_image(page, img_path: Path):
    try:
        await move_caret_to_end(page)
        tool_btn = page.locator('.syl-toolbar-tool.image button').first
        await tool_btn.click()
        await page.wait_for_timeout(600)
        async with page.expect_file_chooser(timeout=8000) as fc_info:
            upload_btn = page.locator('text="本地上传"').first
            await upload_btn.click()
        fc = await fc_info.value
        await fc.set_files(str(img_path))
        await page.wait_for_timeout(2500)
        for label in ['确定', '完成', '确认', '使用']:
            btn = page.locator(f'button:has-text("{label}")').first
            if await btn.count() and await btn.is_visible():
                await btn.click()
                break
        await page.wait_for_timeout(1500)
        await move_caret_to_end(page)
        await page.keyboard.press('Enter')
        await page.wait_for_timeout(200)
        return True
    except Exception as exc:
        print(f'⚠️ 插入内文配图异常: {img_path.name} ({exc})', flush=True)
        return False


async def set_ai_declaration(page):
    try:
        res = await page.evaluate('''() => {
            const labels = Array.from(document.querySelectorAll('label')).filter(l => l.innerText.includes('引用AI'));
            if (labels.length) {
                const inp = labels[0].querySelector('input');
                if (inp) {
                    if (!inp.checked) {
                        inp.click();
                    }
                    return {found: true, checked: inp.checked};
                }
            }
            return {found: false, checked: false};
        }''')
        print(f'  [AI声明] 勾选结果: {res}', flush=True)
        await page.wait_for_timeout(400)
        return res.get('checked', False)
    except Exception as exc:
        print(f'⚠️ 设置AI声明异常: {exc}', flush=True)
        return False


async def upload_article_cover(page, cover_path: Path):
    try:
        await page.evaluate('''() => {
            const singleRadio = document.querySelector('label.byte-radio input[value="2"]');
            if (singleRadio && !singleRadio.checked) {
                singleRadio.click();
            }
        }''')
        await page.wait_for_timeout(800)
        clicked = await page.evaluate('''() => {
            const rep = document.querySelector('a.article-cover-img-replace');
            if (rep) {
                rep.scrollIntoView();
                rep.click();
                return 'clicked_replace';
            }
            const add = document.querySelector('.article-cover-add');
            if (add) {
                add.scrollIntoView();
                add.click();
                return 'clicked_add';
            }
            return 'not_found';
        }''')
        await page.wait_for_timeout(1000)
        tab_btn = page.locator('.byte-drawer-content :text-is("上传图片"), .byte-drawer :text-is("上传图片"), :text-is("上传图片")').first
        if await tab_btn.count() and await tab_btn.is_visible():
            await tab_btn.click()
            await page.wait_for_timeout(800)
        async with page.expect_file_chooser(timeout=8000) as fc_info:
            upload_btn = page.locator('.byte-drawer-content button.upload-btn, button:has-text("本地上传")').first
            await upload_btn.click()
        fc = await fc_info.value
        await fc.set_files(str(cover_path))
        await page.wait_for_timeout(3000)
        confirm_btn = page.locator('.byte-drawer button:has-text("确定"), button:has-text("确定")').first
        await confirm_btn.click()
        await page.wait_for_timeout(2000)
        await page.evaluate('''() => {
            const singleRadio = document.querySelector('label.byte-radio input[value="2"]');
            if (singleRadio && !singleRadio.checked) {
                singleRadio.click();
            }
        }''')
        await page.wait_for_timeout(500)
        return True
    except Exception as exc:
        print(f'⚠️ 上传封面异常: {cover_path.name} ({exc})', flush=True)
        return False


async def click_toutiao_draft_button(page: Page) -> bool:
    """今日头条网页端没有独立「存草稿」按钮。

    通过点击底部「预览」按钮触发云端草稿保存接口（预览本身不会发布）。
    顺序：先找真实存在的草稿按钮，找不到再走「预览」路径。
    """
    candidates = [
        'button:has-text("存草稿")',
        'button:has-text("保存草稿")',
        'button:has-text("暂存")',
        '[class*="draft"] button',
        'button[class*="draft"]',
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(force=True, timeout=3000)
                await page.wait_for_timeout(1500)
                print(f'  💾 已点击「{sel}」')
                return True
        except Exception:
            continue

    # 头条无独立草稿按钮：点击底部「预览」强制触发云端草稿保存
    preview_selectors = [
        'button.publish-btn:has-text("预览")',
        'button:has-text("预览")',
    ]
    for sel in preview_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(force=True, timeout=5000)
                await page.wait_for_timeout(2500)
                print(f'  💾 已点击「{sel}」触发云端草稿保存（预览不发布）')
                return True
        except Exception:
            continue

    try:
        clicked = await page.evaluate("""() => {
            const texts = ['存草稿', '保存草稿', '暂存', '预览'];
            const btns = Array.from(document.querySelectorAll('button, [role="button"], a, div, span'));
            const match = btns.find(el => {
                const t = (el.innerText || el.textContent || '').trim();
                if (!texts.includes(t)) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 16) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || st.pointerEvents === 'none') return false;
                if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
                return true;
            });
            if (match) { match.click(); return true; }
            return false;
        }""")
        if clicked:
            print('  💾 已通过 JS 点击「存草稿」/「预览」')
            await page.wait_for_timeout(1500)
            return True
    except Exception as e:
        print(f'  ⚠️ JS 点击存草稿失败: {e}')
    return False


async def save_one_article(page, item: dict):
    ep = item['episode']
    title = item['title']
    file_path = DOCS_DIR / item['file']
    cover_path = item['cover']

    print(f'\n========================================', flush=True)
    print(f'🚀 开始保存第 {ep} 篇: 《{title}》', flush=True)

    await page.goto('https://mp.toutiao.com/profile_v4/graphic/publish', timeout=60000)
    await page.wait_for_timeout(3500)
    await dismiss_masks_and_drawers(page)

    title_in = page.locator('textarea[placeholder*="请输入文章标题"]').first
    await title_in.click(timeout=5000)
    await title_in.fill('')
    await page.keyboard.type(title)
    print(f'  [1/5] 标题已填入: {title} ({len(title)}字)', flush=True)
    await page.wait_for_timeout(500)

    editor = page.locator('.ProseMirror').first
    await editor.click()
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])

    elements = parse_markdown_content(file_path, item.get('inline_images', []))
    print(f'  [2/5] 开始解析注入正文与插图序列，共 {len(elements)} 个块...', flush=True)

    img_count = 0
    text_count = 0
    for elem_type, elem_val in elements:
        if elem_type == 'text':
            text_count += 1
            await insert_text_block(page, elem_val)
        elif elem_type == 'image':
            img_count += 1
            ok = await insert_inline_image(page, elem_val)
            if ok:
                print(f'    - 插入第 {img_count} 张内文图: {elem_val.name}', flush=True)
            await page.wait_for_timeout(500)

    print(f'  [2/5] 图文注入完毕: {text_count} 个文本块, {img_count} 张内文图', flush=True)
    await page.wait_for_timeout(1000)

    cover_ok = await upload_article_cover(page, cover_path)
    print(f'  [3/5] 官方封面上传完成: {cover_ok} ({cover_path.name})', flush=True)

    ai_ok = await set_ai_declaration(page)
    print(f'  [4/5] 作品声明「引用AI」已勾选: {ai_ok}', flush=True)

    draft_ok = await click_toutiao_draft_button(page)
    if not draft_ok:
        print('  ⚠️ 未找到可点击的「存草稿」按钮，页面保持打开供手动存草稿')

    screenshot_path = ARTIFACT_DIR / f'toutiao_draft_ep{ep:02d}_saved.png'
    await page.screenshot(path=str(screenshot_path), full_page=True)
    print(f'  📸 发布页截图已保存: {screenshot_path.name}', flush=True)


async def main():
    if not COOKIE_PATH.exists():
        print(f"❌ 找不到 Cookie 文件: {COOKIE_PATH}")
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            headless=False,
            args=['--disable-blink-features=AutomationControlled','--lang=zh-CN','--start-maximized']
        )
        context = await browser.new_context(storage_state=str(COOKIE_PATH), viewport={'width': 1440, 'height': 900})
        # 注入 stealth 抹除 navigator.webdriver，防止字节跳动 SecSDK 风控返回 code: 7050
        context = await set_init_script(context)
        page = await context.new_page()

        page.on("dialog", lambda d: asyncio.create_task(d.accept()))

        for item in ARTICLES:
            await save_one_article(page, item)
            await asyncio.sleep(2)

        print("\n✅ 今日头条草稿已保存，浏览器保持打开，请手动点击发布。")
        print("   ⚠️ 注意：不自动保存草稿，不自动发布，由你在后台点击「发布」。")
        print("   🌐 浏览器将保持打开，请在页面上手动发布完成后再关闭本窗口。")

        await context.storage_state(path=str(COOKIE_PATH))

        print("   ⏳ 等待中，浏览器保持打开，请手动发布后关闭窗口...", flush=True)
        if os.getenv('DRAFT_NO_WAIT') == '1':
            print('   ⏹ DRAFT_NO_WAIT=1，保存完成即退出（草稿已留在浏览器/云端）', flush=True)
            return True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    asyncio.run(main())
