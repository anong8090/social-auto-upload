#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化批量保存 5 篇精炼图文至百家号官方草稿箱（图文并茂版）。

严格规则：
1. 仅保存为草稿（点击「存草稿」），绝不点击发布！
2. 标题字数精简在 8-30 字以内。
3. 正文交叉插入 2 张高清技术配图（02_* 与 03_*，均带正版河狸云 Logo），图文并茂。
4. 展示封面设置为「单图」，上传 16:9 官方封面（01_本期头图_*.png）。
5. 创作声明勾选「采用AI生成内容」。
6. 杜绝任何免费试点、网址链接、联系方式等违规导流字样。
7. 脚本自行启动浏览器，填好内容后保持浏览器打开，
   由你在后台手动点击「发布」。
"""
import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))

from playwright.async_api import Page, async_playwright
from uploader.baijiahao_uploader.main import (
    BaiJiaHaoArticle,
    _launch_browser,
    _install_preview_guard,
    _dismiss_overlays,
)
from utils.log import baijiahao_logger
from account_lookup import get_platform_account

DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '12_工地图纸多找详图难_一稿五投'
IMG_BASE = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材' / '12_工地图纸多找详图难'
ACCOUNT_FILE, ACCOUNT_USER = get_platform_account(5)
ARTIFACT_DIR = BASE_DIR / 'logs' / 'screenshots'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

ARTICLES = [
    {
        'episode': 12,
        'title': '工地图纸几十张，找详图翻半天怎么办？',
        'file': '母本B_四平台精炼长文_工地图纸多找详图难.md',
        'cover': IMG_BASE / '01_16x9_头图封面_工地图纸多找详图难.png',
        'inline_images': [
            IMG_BASE / '02_16x9_内文图1_工地图纸多找详图难.png',
            IMG_BASE / '03_16x9_内文图2_工地图纸多找详图难.png',
        ],
    },
]


def parse_article_blocks(md_path: Path, inline_images: list[Path]) -> list[tuple[str, str | Path]]:
    raw = md_path.read_text(encoding='utf-8')
    lines = raw.splitlines()
    text_blocks = []
    cur = []
    img_idx = 0
    seen_first_img = False
    re = __import__('re')

    for line in lines:
        s = line.strip()
        if s.startswith('# ') or (s.startswith('>') and not seen_first_img) or s == '---':
            continue
        if s.startswith('!['):
            if not seen_first_img:
                seen_first_img = True
                continue
            if cur:
                text_blocks.append(('text', '\n'.join(cur).strip()))
                cur = []
            if img_idx < len(inline_images):
                text_blocks.append(('image', inline_images[img_idx]))
                img_idx += 1
            continue

        cleaned_line = re.sub(r'^#{2,4}\s*', '', s)
        cleaned_line = re.sub(r'^>\s*', '', cleaned_line)
        cleaned_line = cleaned_line.replace('**', '')
        if cleaned_line.startswith('|'):
            cells = [c.strip() for c in cleaned_line.strip('|').split('|')]
            if all(set(c) <= {':', '-'} for c in cells):
                continue
            if cells[0] == '核心维度':
                continue
            cleaned_line = f"• {cells[0]}：单机工具为「{cells[1]}」；蓝枢协同中枢为「{cells[2]}」"
        if cleaned_line:
            cur.append(cleaned_line)

    if cur:
        text_blocks.append(('text', '\n'.join(cur).strip()))

    return text_blocks


async def focus_editor_end(page: Page, article: BaiJiaHaoArticle):
    editor = await article._wait_for_body_editor(page)
    if not editor:
        raise RuntimeError("未找到百家号正文编辑区")
    editable = await article._get_body_editable(page, editor)
    await article._blur_title_editor(page)
    await editable.scroll_into_view_if_needed()
    try:
        box = await editable.bounding_box()
        if box:
            await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + min(80, box["height"] / 2))
        else:
            await editable.click(force=True)
    except Exception:
        await editable.click(force=True)
    await page.wait_for_timeout(150)
    await editable.evaluate("""(el) => {
        el.focus();
        const sel = window.getSelection();
        const range = document.createRange();
        const children = Array.from(el.childNodes);
        if (children.length > 0) {
            const last = children[children.length - 1];
            if (last.nodeType === Node.TEXT_NODE) {
                range.setStart(last, (last.textContent || '').length);
                range.collapse(true);
            } else if (last.nodeName === 'BLOCKQUOTE') {
                const p = el.ownerDocument.createElement('p');
                p.innerHTML = '<br>';
                el.appendChild(p);
                range.setStart(p, 0);
                range.collapse(true);
            } else {
                range.selectNodeContents(last);
                range.collapse(false);
            }
        } else {
            range.selectNodeContents(el);
            range.collapse(false);
        }
        sel.removeAllRanges();
        sel.addRange(range);
    }""")
    await page.wait_for_timeout(150)
    return editable


async def append_text_block(page: Page, article: BaiJiaHaoArticle, text: str):
    await focus_editor_end(page, article)
    clean_lines = [__import__('re').sub(r'^>\s*', '', ln) for ln in text.splitlines()]
    clean_text = '\n'.join(clean_lines)
    await page.evaluate("async (t) => { await navigator.clipboard.writeText(t); }", clean_text + "\n\n")
    await page.keyboard.press("Meta+V")
    await page.wait_for_timeout(600)


async def insert_inline_image_bjh(page: Page, article: BaiJiaHaoArticle, img_path: Path):
    await _dismiss_overlays(page)
    await focus_editor_end(page, article)
    await page.wait_for_timeout(300)

    # 点击插图按钮（尝试多次，确保弹窗出现）
    for attempt in range(4):
        await focus_editor_end(page, article)
        await page.evaluate("""() => {
            document.querySelectorAll('.edui-for-insertimage, .edui-for-insertimage *').forEach(el => {
                el.classList.remove('edui-state-disabled');
            });
        }""")
        btn = page.locator('.edui-for-insertimage .edui-button-body, .edui-for-insertimage').first
        try:
            if await btn.count():
                await btn.scroll_into_view_if_needed()
                box = await btn.bounding_box()
                if box:
                    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                else:
                    await btn.click(force=True)
        except Exception:
            await btn.click(force=True)
        await page.wait_for_timeout(1500)
        # 检查 file input 是否已出现
        fi_check = page.locator('input[type="file"][accept*="image"]')
        if await fi_check.count():
            break

    # 找 file input：优先在可见 modal/对话框里找，退回到全页面
    fi = None
    for modal_sel in ['.cheetah-ui-pro-image-modal:visible', '.cheetah-modal:visible', 'div[role="dialog"]:visible', 'div[class*="modal"]:visible']:
        m = page.locator(modal_sel).last
        if await m.count():
            candidate = m.locator('input[type="file"][accept*="image"]').first
            if not await candidate.count():
                candidate = m.locator('input[type="file"]').first
            if await candidate.count():
                fi = candidate
                break

    if not fi or not await fi.count():
        fi = page.locator('input[type="file"][accept*="image"]').last
    if not fi or not await fi.count():
        fi = page.locator('input[type="file"]').last

    if not await fi.count():
        baijiahao_logger.warning(f"  ⚠️ 未找到图片上传控件: {img_path.name}，跳过")
        return False

    await fi.set_input_files(str(img_path))
    baijiahao_logger.info(f"  📤 已选择内文配图: {img_path.name}，等待上传完成...")
    await page.wait_for_timeout(4000)

    # 点击确认/插入按钮
    confirm_btn = page.locator('button:has-text("确认"), button:has-text("确定"), button:has-text("插入"), button:has-text("完成")').last
    if await confirm_btn.count() and await confirm_btn.is_visible():
        await confirm_btn.click(force=True)
        await page.wait_for_timeout(1500)
    else:
        await article._confirm_cover_dialog(page)
        await page.wait_for_timeout(1000)

    await focus_editor_end(page, article)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(300)


async def save_single_bjh_draft(page: Page, item: dict):
    ep = item['episode']
    title = item['title']
    md_path = DOCS_DIR / item['file']
    cover_path = item['cover']
    inline_images = item['inline_images']

    baijiahao_logger.info(f"\n==================================================")
    baijiahao_logger.info(f"📘 [{ep}/5] 开始保存百家号图文草稿: {title}")
    baijiahao_logger.info(f"==================================================")

    article = BaiJiaHaoArticle(
        title=title,
        body="占位",
        tags=[],
        publish_date=0,
        account_file=str(ACCOUNT_FILE),
        dry_run=True,
        cover_path=str(cover_path),
        ai_generated=True,
    )

    await article._open_article_page(page)
    await _install_preview_guard(page)
    await _dismiss_overlays(page)

    await article.fill_title(page)
    await page.wait_for_timeout(500)

    editable = await focus_editor_end(page, article)
    await page.keyboard.press("Meta+A")
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(200)

    blocks = parse_article_blocks(md_path, inline_images)
    for btype, bval in blocks:
        if btype == 'text':
            await append_text_block(page, article, str(bval))
        elif btype == 'image':
            await insert_inline_image_bjh(page, article, Path(bval))

    baijiahao_logger.info("  ✅ 正文与 2 张内文配图已穿插注入完毕")
    await page.wait_for_timeout(1000)

    for cover_try in range(3):
        try:
            await article.handle_cover(page)
            await page.wait_for_timeout(1500)
            if await article._cover_already_set(page):
                break
        except Exception as e:
            baijiahao_logger.warning(f"第 {cover_try+1} 次上传封面重试: {e}")
        await page.wait_for_timeout(1000)
    await page.wait_for_timeout(800)

    await article.apply_ai_declaration(page)
    await page.wait_for_timeout(600)

    await _install_preview_guard(page)
    draft_btn = page.locator("button.cheetah-public:has-text('存草稿'), button:has-text('存草稿')").first
    await draft_btn.scroll_into_view_if_needed()
    await page.wait_for_timeout(300)
    await draft_btn.click(force=True)
    baijiahao_logger.info("  💾 已点击「存草稿」按钮，等待保存响应...")

    for _ in range(20):
        await page.wait_for_timeout(500)
        toasts = await page.locator("div.cheetah-message, div.ant-message, div[role='alert']").all_text_contents()
        if any("草稿" in t or "保存" in t or "成功" in t for t in toasts):
            baijiahao_logger.success(f"  🎉 草稿保存成功提示: {toasts}")
            break
        if "article_id=" in page.url:
            baijiahao_logger.success(f"  🎉 草稿已分配 article_id: {page.url}")
            break

    await page.wait_for_timeout(2000)
    saved_shot = ARTIFACT_DIR / f"bjh_draft_ep0{ep}_saved.png"
    await page.screenshot(path=str(saved_shot))
    baijiahao_logger.info(f"  📸 草稿页截图已保存: {saved_shot.name}")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel='chrome',
                                          args=['--disable-blink-features=AutomationControlled',
                                                '--lang=zh-CN',
                                                '--start-maximized'])
        context = await browser.new_context(
            storage_state=str(ACCOUNT_FILE),
            permissions=["clipboard-read", "clipboard-write"],
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = await context.new_page()

        for item in ARTICLES:
            await save_single_bjh_draft(page, item)
            await asyncio.sleep(2)

        baijiahao_logger.info("\n🔍 正在前往百家号草稿箱页面核验草稿...")
        draft_list_url = "https://baijiahao.baidu.com/builder/rc/content?currentPage=1&pageSize=10&search=&type=news&collection=draft"
        await page.goto(draft_list_url, timeout=60000)
        await page.wait_for_timeout(4500)
        await _dismiss_overlays(page)

        final_shot = ARTIFACT_DIR / "bjh_ep12_draft_verified.png"
        await page.screenshot(path=str(final_shot), full_page=True)
        baijiahao_logger.success(f"🏆 百家号本期草稿核验截图已保存: {final_shot}")

        print("\n✅ 百家号草稿已保存，浏览器保持打开，请手动点击发布。")
        print("   ⚠️ 注意：不自动发布，由你在后台点击「发布」。")
        print("   🌐 浏览器将保持打开，请在页面上手动发布完成后再关闭本窗口。")

        await context.storage_state(path=str(ACCOUNT_FILE))

        print("   ⏳ 等待中，浏览器保持打开，请手动发布后关闭窗口...", flush=True)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await context.close()
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
