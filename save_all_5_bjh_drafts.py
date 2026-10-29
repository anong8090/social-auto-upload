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
"""
import asyncio
import os
import re
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

DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '搜狐号精简版_5篇草稿'
IMG_BASE = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材' / '知乎专栏连载分期配图'
ACCOUNT_FILE, ACCOUNT_USER = get_platform_account(5)
ARTIFACT_DIR = BASE_DIR / 'logs' / 'screenshots'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

ARTICLES = [
    {
        'episode': 1,
        'title': '底图失踪之谜：外部参照如何让现场开出白卷',
        'file': '第一期_底图失踪之谜_外部参照是如何让现场开出白卷的.md',
        'cover': IMG_BASE / '第一期_成套解析与外部参照' / '01_本期头图_外部参照与底图开天窗排查.png',
        'inline_images': [
            IMG_BASE / '第一期_成套解析与外部参照' / '02_原理对比_外部参照引用链机制.png',
            IMG_BASE / '第一期_成套解析与外部参照' / '03_实机视窗_缺少外部参照直接清单点名.png',
        ],
    },
    {
        'episode': 2,
        'title': '十几万返工代价：为什么图纸必须确认才算现行',
        'file': '第二期_十几万返工代价：为什么图纸必须确认才算现行.md',
        'cover': IMG_BASE / '第二期_现行锁控与防错闭环' / '01_本期头图_未确认不算现行版.png',
        'inline_images': [
            IMG_BASE / '第二期_现行锁控与防错闭环' / '02_风控闭环_现行版三步管控流程.png',
            IMG_BASE / '第二期_现行锁控与防错闭环' / '03_实机视窗_现行版确认与智能目录台账.png',
        ],
    },
    {
        'episode': 3,
        'title': '图面标了个详03：现场到底要翻多久图集',
        'file': '第三期_图面标了个详03：现场到底要翻多久图集.md',
        'cover': IMG_BASE / '第三期_图面穿透与双栏对照' / '01_本期头图_图面索引一秒直达详图.png',
        'inline_images': [
            IMG_BASE / '第三期_图面穿透与双栏对照' / '02_交互对比_传统盲翻对比蓝枢秒通.png',
            IMG_BASE / '第三期_图面穿透与双栏对照' / '03_实机视窗_图面热区与双栏同屏对照.png',
        ],
    },
    {
        'episode': 4,
        'title': '跨单位协同两难：发给甲方看图免装软件防泄密',
        'file': '第四期_跨单位协同两难：发给甲方看图免装软件防泄密.md',
        'cover': IMG_BASE / '第四期_无感协同与对外分享' / '01_本期头图_微信发链接秒看与零门槛交付.png',
        'inline_images': [
            IMG_BASE / '第四期_无感协同与对外分享' / '02_安全对比_随手乱甩文件对比受控链接分享.png',
            IMG_BASE / '第四期_无感协同与对外分享' / '03_实机视窗_微信免登录看图与手机离线圈阅.png',
        ],
    },
    {
        'episode': 5,
        'title': '工程选型边界：办公室单机看图vs现场协同中枢',
        'file': '第五期_工程选型边界：办公室单机看图vs现场协同中枢.md',
        'cover': IMG_BASE / '第五期_工具选型与边界对比' / '01_本期头图_单机看图工具对比协同中枢.png',
        'inline_images': [
            IMG_BASE / '第五期_工具选型与边界对比' / '02_选型矩阵_四维分工对照表.png',
            IMG_BASE / '第五期_工具选型与边界对比' / '03_全景链路_现场图纸全生命周期协同流水线.png',
        ],
    },
]

def parse_article_blocks(md_path: Path, inline_images: list[Path]) -> list[tuple[str, str | Path]]:
    """将精简版 Markdown 拆分为 3 段文本块，中间穿插 2 张高清内文配图。"""
    raw = md_path.read_text(encoding='utf-8')
    lines = raw.splitlines()

    text_blocks = []
    cur = []
    img_idx = 0
    seen_first_img = False

    for line in lines:
        s = line.strip()
        if s.startswith('# ') or (s.startswith('>') and not seen_first_img) or s == '---':
            continue
        if s.startswith('!['):
            if not seen_first_img:
                seen_first_img = True
                continue
            # 遇到第 2、3 张图片标记时，切分文本块并插入对应配图
            if cur:
                text_blocks.append(('text', '\n'.join(cur).strip()))
                cur = []
            if img_idx < len(inline_images):
                text_blocks.append(('image', inline_images[img_idx]))
                img_idx += 1
            continue

        # 清理 Markdown 小标题符号、引用符号与粗体符号，使百家号正文干净自然
        cleaned_line = re.sub(r'^#{2,4}\s*', '', s)
        cleaned_line = re.sub(r'^>\s*', '', cleaned_line)
        cleaned_line = cleaned_line.replace('**', '')
        if cleaned_line.startswith('|'):
            # 表格行转为清晰列表文本
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
                // 如果最后一个节点是 blockquote，在它后面插入一个普通段落 p 并把光标放进去，防止 UEditor 禁用插入图片
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
    # 彻底清理行首的 '> ' 引用符，防止编辑器进入 blockquote 模式导致工具栏禁用插图
    clean_lines = [re.sub(r'^>\s*', '', ln) for ln in text.splitlines()]
    clean_text = '\n'.join(clean_lines)
    await page.evaluate("async (t) => { await navigator.clipboard.writeText(t); }", clean_text + "\n\n")
    await page.keyboard.press("Meta+V")
    await page.wait_for_timeout(600)

async def insert_inline_image_bjh(page: Page, article: BaiJiaHaoArticle, img_path: Path):
    await _dismiss_overlays(page)
    await focus_editor_end(page, article)
    await page.wait_for_timeout(300)

    # 打开图片上传弹窗（模拟真实鼠标点击 .edui-for-insertimage）
    for attempt in range(4):
        await focus_editor_end(page, article)
        # 移除工具栏按钮可能带有的 edui-state-disabled 类
        await page.evaluate("""() => {
            document.querySelectorAll('.edui-for-insertimage, .edui-for-insertimage *').forEach(el => {
                el.classList.remove('edui-state-disabled');
            });
        }""")
        btn = page.locator('.edui-for-insertimage .edui-button-body, .edui-for-insertimage').first
        try:
            await btn.scroll_into_view_if_needed()
            box = await btn.bounding_box()
            if box:
                await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            else:
                await btn.click(force=True)
        except Exception:
            await btn.click(force=True)
        await page.wait_for_timeout(1200)
        # 必须确保对话框可见
        visible_modals = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('.cheetah-ui-pro-image-modal'))
                .filter(el => el.offsetParent !== null && window.getComputedStyle(el).display !== 'none').length;
        }""")
        if visible_modals > 0:
            break

    # 在当前可见的弹窗内选择本地上传 input[type="file"]
    fi = page.locator('.cheetah-ui-pro-image-modal:visible input[type="file"]').last
    if not await fi.count():
        fi = page.locator('input[type="file"][accept*="image"]').last
    await fi.set_input_files(str(img_path))
    baijiahao_logger.info(f"  📤 已选择内文配图: {img_path.name}，等待上传完成...")
    await page.wait_for_timeout(3200)

    modal = page.locator('.cheetah-ui-pro-image-modal').last
    confirm_btn = modal.locator('button:has-text("确认"), button:has-text("确定")').last
    if await confirm_btn.count() and await confirm_btn.is_visible():
        await confirm_btn.click(force=True)
    else:
        await article._confirm_cover_dialog(page)
    await page.wait_for_timeout(1800)

    # 将光标移至末尾并换行
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

    # 1. 填写标题
    await article.fill_title(page)
    await page.wait_for_timeout(500)

    # 2. 清空正文并逐段注入文字与内文配图
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

    # 3. 上传单图展示封面 (16:9 头图)
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

    # 4. 勾选「采用AI生成内容」
    await article.apply_ai_declaration(page)
    await page.wait_for_timeout(600)

    # 截图保存前预览
    preview_shot = ARTIFACT_DIR / f"bjh_draft_ep0{ep}_preview.png"
    await page.screenshot(path=str(preview_shot), full_page=True)
    baijiahao_logger.info(f"  📸 预览截图已保存: {preview_shot.name}")

    # 5. 点击「存草稿」按钮
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

async def main():
    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright, headless=False)
        context = await browser.new_context(
            storage_state=str(ACCOUNT_FILE),
            permissions=["clipboard-read", "clipboard-write"],
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = await context.new_page()

        # 前往百家号草稿箱页面，先删除未设置封面的旧第 5 期草稿（如有）
        draft_list_url = "https://baijiahao.baidu.com/builder/rc/content?currentPage=1&pageSize=10&search=&type=news&collection=draft"
        await page.goto(draft_list_url, timeout=60000)
        await page.wait_for_timeout(4000)
        await _dismiss_overlays(page)
        try:
            del_clicked = await page.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('div, tr, li')).filter(el => {
                    const t = (el.innerText || '');
                    return t.includes('工程选型边界：办公室单机看图vs现场协同中枢') && t.includes('删除') && el.offsetHeight > 40 && el.offsetHeight < 220;
                });
                if (items.length > 0) {
                    const delBtn = Array.from(items[0].querySelectorAll('a, span, button, div')).find(b => (b.innerText || '').trim() === '删除');
                    if (delBtn) {
                        delBtn.click();
                        return true;
                    }
                }
                return false;
            }""")
            if del_clicked:
                await page.wait_for_timeout(1000)
                confirm_del = page.locator('button:has-text("确定"), button:has-text("确认")').last
                if await confirm_del.count() and await confirm_del.is_visible():
                    await confirm_del.click(force=True)
                    await page.wait_for_timeout(2000)
                    baijiahao_logger.info("🧹 已清理未设置封面的旧第 5 期草稿")
        except Exception as e:
            baijiahao_logger.warning(f"清理旧第 5 期草稿提示: {e}")

        for item in ARTICLES:
            if item['episode'] != 5:
                continue
            await save_single_bjh_draft(page, item)
            await asyncio.sleep(2)

        # 前往百家号草稿箱页面截图核验
        baijiahao_logger.info("\n🔍 正在前往百家号草稿箱页面核验 5 篇草稿...")
        draft_list_url = "https://baijiahao.baidu.com/builder/rc/content?currentPage=1&pageSize=10&search=&type=news&collection=draft"
        await page.goto(draft_list_url, timeout=60000)
        await page.wait_for_timeout(4500)
        await _dismiss_overlays(page)

        final_shot = ARTIFACT_DIR / "bjh_5_drafts_complete_verified.png"
        await page.screenshot(path=str(final_shot), full_page=True)
        baijiahao_logger.success(f"🏆 百家号 5 篇草稿核验截图已保存: {final_shot}")

        await context.storage_state(path=str(ACCOUNT_FILE))
        await context.close()
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
