#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本期《工地图纸多找详图难》知乎专栏草稿保存：
脚本自行启动浏览器，仅自动填写内容 + 上传封面 + 勾选 AI 创作声明，存入知乎官方草稿箱，绝不点击发布。
填好后浏览器保持打开，由你手动点击「发布」。
"""
import asyncio
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))

from playwright.async_api import async_playwright
from uploader.zhihu_uploader.main import ZhiHuArticle
from account_lookup import get_platform_account
from draft_content_parser import parse_body_blocks

DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '13_设计变更后图纸怎么跟_一稿五投'
IMG_BASE = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材' / '13_设计变更后图纸怎么跟'
COOKIE_PATH, ACCOUNT_USER = get_platform_account(9)
ARTIFACT_DIR = BASE_DIR / 'logs' / 'screenshots'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

TITLE = '设计变更一来，手上那叠图该换哪几张？'
MD_FILE = DOCS_DIR / '母本B_四平台精炼长文_设计变更后图纸怎么跟.md'
COVER = IMG_BASE / '01_16x9_头图封面_设计变更后图纸怎么跟.png'
INLINE = [
    IMG_BASE / '02_16x9_内文图1_设计变更后图纸怎么跟.png',
    IMG_BASE / '03_16x9_内文图2_设计变更后图纸怎么跟.png',
]


def parse_blocks():
    """统一解析：剥离元数据说明行、清洗裸露 Markdown 标记、内文图穿插。"""
    return parse_body_blocks(MD_FILE, INLINE, skip_cover=True)


async def paste_text(editor, page, text):
    await page.evaluate(
        """(t) => {
            const el = document.querySelector('.public-DraftEditor-content') || document.querySelector('[contenteditable="true"]');
            const dt = new DataTransfer();
            dt.setData('text/plain', t);
            el.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
        }""", text + '\n\n')
    await page.wait_for_timeout(800)


async def has_upload_fail(page) -> bool:
    """检查编辑区内是否出现知乎的「上传失败」占位块。"""
    try:
        return await page.evaluate("""() => {
            const ed = document.querySelector('[contenteditable="true"]');
            if (!ed) return false;
            return Array.from(ed.querySelectorAll('*')).some(el =>
                el.children.length === 0 && (el.innerText || '').trim() === '上传失败');
        }""")
    except Exception:
        return False


async def remove_failed_images(page):
    """点选失败的图片块并退格删除，避免重传后出现两块。"""
    for _ in range(3):
        el = page.locator('[contenteditable="true"] :text-is("上传失败")').first
        try:
            if not await el.count():
                return
            await el.click(force=True)
            await page.wait_for_timeout(300)
            await page.keyboard.press('Backspace')
            await page.wait_for_timeout(600)
        except Exception:
            return


async def insert_inline_image(page, img_path: Path):
    """内文图上传，带失败检测与重试（知乎偶发上传失败）。"""
    for attempt in range(1, 4):
        ok = await _try_insert_image(page, img_path)
        await page.wait_for_timeout(4000)
        if ok and not await has_upload_fail(page):
            if attempt > 1:
                print(f'    第 {attempt} 次重试成功', flush=True)
            return True
        if await has_upload_fail(page):
            print(f'    ⚠️ 第 {attempt} 次上传失败，清理后重试', flush=True)
            await remove_failed_images(page)
            await page.wait_for_timeout(800)
    print(f'    ❌ 内文图多次上传失败: {img_path.name}', flush=True)
    return False


async def _try_insert_image(page, img_path: Path) -> bool:
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


async def close_blocking_modal(page):
    """关闭「投稿至问题」等自动弹出的面板（它会浮在编辑区上方挡住截图与左侧设置）。
    知乎该面板的 DOM 层级不稳定，直接按文本定位最小容器并从 DOM 移除，不影响已写内容。"""
    await page.keyboard.press('Escape')
    await page.wait_for_timeout(300)
    try:
        removed = await page.evaluate("""() => {
            const nodes = Array.from(document.querySelectorAll('div,section,aside'));
            const cands = nodes.filter(n => (n.innerText || '').includes('投稿至问题'));
            if (!cands.length) return 0;
            cands.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
            let node = cands[0];
            for (let i = 0; i < 6 && node; i++) {
                const cs = getComputedStyle(node);
                const cn = (node.className || '').toString();
                if (cs.position === 'fixed' || /modal|Modal|dialog|Dialog|Drawer/i.test(cn)) {
                    node.remove();
                    return 1;
                }
                node = node.parentElement;
            }
            cands[0].remove();
            return 2;
        }""")
        if removed:
            print(f'  🧹 已关闭「投稿至问题」浮层 (level={removed})', flush=True)
            await page.wait_for_timeout(400)
        return bool(removed)
    except Exception as e:
        print(f'  ℹ️ 关闭浮层异常: {e}', flush=True)
        return False


async def cover_uploaded(page) -> bool:
    """封面缩略图出现在 UploadPicture-input 的祖先容器里即视为上传成功。
    注意：「添加封面」是栏目标题文字，始终存在，不能作为空框判据。"""
    try:
        return await page.evaluate("""() => {
            const inp = document.querySelector('input.UploadPicture-input');
            if (!inp) return false;
            let box = inp.parentElement;
            for (let i = 0; i < 6 && box; i++) {
                if (box.querySelectorAll('img').length > 0) return true;
                box = box.parentElement;
            }
            return false;
        }""")
    except Exception:
        return False


async def upload_topic_cover(page, cover_path: Path):
    """封面入口：左侧「发布设置」->「添加封面」，input 为 input.UploadPicture-input。
    知乎偶发静默失败（不抛错但界面仍是空框），故上传后校验状态并重试。"""
    """知乎文章封面入口是左侧「发布设置」里的「添加封面」(label)，文案不是「添加题图」。
    封面 input 为 `input.UploadPicture-input`（accept=.jpeg,.jpg,.png，隐藏态）。"""
    # 路径一：直接给封面 input 塞文件（Playwright 对隐藏 input 有效，最稳）
    cover_input = page.locator('input.UploadPicture-input').first
    for attempt in range(1, 4):
        if await cover_uploaded(page):
            await page.wait_for_timeout(500)
            if await cover_uploaded(page):
                print(f'  🖼️ 封面已就位: {cover_path.name}', flush=True)
                return True
        try:
            if await cover_input.count():
                await cover_input.set_input_files(str(cover_path), timeout=10000)
                await page.wait_for_timeout(6000)
                await confirm_cover_dialog(page)
                await page.wait_for_timeout(1200)
                if await cover_uploaded(page):
                    print(f'  🖼️ 封面已上传(第 {attempt} 次): {cover_path.name}', flush=True)
                    return True
                print(f'  ⚠️ 第 {attempt} 次封面上传后仍为空框，重试', flush=True)
        except Exception as e:
            print(f'  ℹ️ 封面上传异常({type(e).__name__}): {e}', flush=True)
        await page.wait_for_timeout(1500)

    # 路径二：点「添加封面」label 触发文件选择器
    entry = None
    for sel in ('label:has-text("添加封面")', 'text=添加文章封面', 'text=添加封面'):
        loc = page.locator(sel).last
        try:
            if await loc.count():
                entry = loc
                break
        except Exception:
            continue
    if entry is None:
        print('  ⚠️ 未找到「添加封面」入口', flush=True)
        return False

    try:
        await entry.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    await page.wait_for_timeout(300)
    try:
        async with page.expect_file_chooser(timeout=6000) as fc_info:
            await entry.click(force=True, timeout=4000)
        chooser = await fc_info.value
        await chooser.set_files(str(cover_path))
        await page.wait_for_timeout(5000)
        print(f'  🖼️ 封面已上传(文件选择器): {cover_path.name}', flush=True)
        return True
    except Exception as e:
        print(f'  ⚠️ 点「添加封面」未触发选择器: {e}', flush=True)
        return False


async def confirm_cover_dialog(page):
    """题图裁剪/确认弹窗：只在可见 modal 内点确定。"""
    for _ in range(6):
        modal = page.locator('div[role="dialog"]:visible, .Modal-content:visible, [class*="Modal"]:visible').last
        if await modal.count():
            for t in ('button:has-text("确定")', 'button:has-text("完成")', 'button:has-text("保存")'):
                b = modal.locator(t).first
                try:
                    if await b.count() and await b.is_visible():
                        await b.click(force=True, timeout=2000)
                        await page.wait_for_timeout(1500)
                        return True
                except Exception:
                    continue
        await page.wait_for_timeout(500)
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
        # 知乎会自动恢复上一次草稿，先清空避免正文重复
        await page.keyboard.press("Meta+A")
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(800)
        for _ in range(6):
            imgs = page.locator('[contenteditable="true"] img')
            if await imgs.count() == 0:
                break
            await imgs.first.click(force=True)
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(300)

        for btype, bval in blocks:
            if btype == 'text':
                await paste_text(editor, page, str(bval))
            else:
                await insert_inline_image(page, Path(bval))

        text_len = len((await editor.inner_text()).strip())
        print(f'  📝 正文注入完毕，编辑器字数: {text_len}', flush=True)

        try:
            await close_blocking_modal(page)
            ok = await upload_topic_cover(page, COVER)
            if ok:
                await confirm_cover_dialog(page)
                await close_blocking_modal(page)
            else:
                print('  ⚠️ 封面未上传成功，稍后可在左侧「发布设置」手动补图', flush=True)
        except Exception as e:
            print(f'  ⚠️ 题图上传失败(继续): {e}', flush=True)

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
        shot = ARTIFACT_DIR / 'zhihu_ep13_draft_ready.png'
        await page.screenshot(path=str(shot), full_page=True)
        print(f'  📸 发布页截图已保存: {shot.name}（不自动发布，浏览器保持打开，请手动点击发布）', flush=True)

        print('\n✅ 知乎内容已填写完毕，发布页保持打开，请手动点击发布。', flush=True)
        print('   ⚠️ 注意：不自动保存草稿，不自动发布，由你在后台点击「发布」。', flush=True)
        print('   🌐 浏览器将保持打开，请在页面上手动发布完成后再关闭本窗口。', flush=True)

        await ctx.storage_state(path=str(COOKIE_PATH))

        print('   ⏳ 等待中，浏览器保持打开，请手动发布后关闭窗口...', flush=True)
        if os.getenv('DRAFT_NO_WAIT') == '1':
            print('   ⏹ DRAFT_NO_WAIT=1，保存完成即退出（草稿已留在浏览器/云端）', flush=True)
            return True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    asyncio.run(main())
