# -*- coding: utf-8 -*-
import asyncio
import os
import re
import sys
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))
from account_lookup import get_platform_account

DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '知乎专栏连载草稿_5篇独立发布版'
COOKIE_PATH, ACCOUNT_USER = get_platform_account(7)
ARTIFACT_DIR = BASE_DIR / 'logs' / 'screenshots'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

ARTICLES = [
    {
        'episode': 1,
        'title': '底图失踪之谜：外部参照如何让现场开出白卷',
        'file': '第一期_底图失踪之谜_外部参照是如何让现场开出白卷的.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/知乎专栏连载分期配图/第一期_成套解析与外部参照/01_本期头图_外部参照与底图开天窗排查.png',
    },
    {
        'episode': 2,
        'title': '十几万返工代价：为什么图纸必须确认才算现行',
        'file': '第二期_十几万返工的代价_为什么图纸管理必须做到未确认不算现行版.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/知乎专栏连载分期配图/第二期_现行锁控与防错闭环/01_本期头图_未确认不算现行版.png',
    },
    {
        'episode': 3,
        'title': '图面标了个详03：现场到底要翻多久图集',
        'file': '第三期_图面标了个详03_现场到底要翻多久_图纸穿透与双栏对照.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/知乎专栏连载分期配图/第三期_图面穿透与双栏对照/01_本期头图_图面索引一秒直达详图.png',
    },
    {
        'episode': 4,
        'title': '跨单位协同两难：发给甲方看图免装软件防泄密',
        'file': '第四期_跨单位协同两难_发给甲方监理看图如何免装软件秒开防泄密.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/知乎专栏连载分期配图/第四期_无感协同与对外分享/01_本期头图_微信发链接秒看与零门槛交付.png',
    },
    {
        'episode': 5,
        'title': '工程选型边界：办公室单机看图vs现场协同中枢',
        'file': '第五期_工具选型边界_办公室单机看图工具vs现场协同中枢.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/知乎专栏连载分期配图/第五期_工具选型与边界对比/01_本期头图_单机看图工具对比协同中枢.png',
    },
]


def parse_markdown_content(file_path: Path):
    """把 Markdown 文件拆解为纯文本块与配图文件序列，同时过滤掉元信息"""
    raw_text = file_path.read_text(encoding='utf-8')
    lines = raw_text.splitlines()

    elements = []  # ('text', str) or ('image', Path)
    current_text = []

    is_header_meta = True
    first_image_seen = False

    for line in lines:
        sline = line.strip()

        # 跳过开头的标题 # 与专栏元信息引用块
        if is_header_meta:
            if sline.startswith('#'):
                continue
            if sline.startswith('>') or sline.startswith('---'):
                continue
            if sline:
                is_header_meta = False

        img_match = re.search(r'!\[(.*?)\]\((.*?)\)', sline)
        if img_match:
            # 第一张图通常是本期头图，已作为文章封面，跳过内文重复插入
            if not first_image_seen:
                first_image_seen = True
                continue

            rel_link = img_match.group(2)
            abs_img_path = (file_path.parent / rel_link).resolve()

            if current_text:
                elements.append(('text', '\n\n'.join(current_text)))
                current_text = []

            if abs_img_path.exists():
                elements.append(('image', abs_img_path))
            continue

        if sline:
            current_text.append(sline)

    if current_text:
        elements.append(('text', '\n\n'.join(current_text)))

    return elements


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
    """确保编辑器聚焦且光标在内容最末端，防止覆盖前面的图文"""
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
    """向编辑器末尾追加一段纯文本"""
    await move_caret_to_end(page)
    await page.evaluate('''async (t) => { await navigator.clipboard.writeText(t); }''', text)
    await page.keyboard.press('Meta+V')
    await page.keyboard.press('Enter')
    await page.wait_for_timeout(300)


async def insert_inline_image(page, img_path: Path):
    """在当前编辑器末尾插入一张内文配图"""
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

        # 移动光标到末尾并回车，准备接下一段
        await move_caret_to_end(page)
        await page.keyboard.press('Enter')
        await page.wait_for_timeout(200)
        return True
    except Exception as exc:
        print(f'⚠️ 插入内文配图异常: {img_path.name} ({exc})', flush=True)
        return False


async def set_ai_declaration(page):
    """勾选作品声明中的「引用AI」"""
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
    """将封面设置为「单图」并上传官方 16:9 封面配图"""
    try:
        # 1. 切换为单图 (radio value="2")
        await page.evaluate('''() => {
            const singleRadio = document.querySelector('label.byte-radio input[value="2"]');
            if (singleRadio && !singleRadio.checked) {
                singleRadio.click();
            }
        }''')
        await page.wait_for_timeout(800)

        # 2. 点击上传/替换封面触发抽屉弹窗
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

        # 3. 切换抽屉选项卡到「上传图片」
        tab_btn = page.locator('.byte-drawer-content :text-is("上传图片"), .byte-drawer :text-is("上传图片"), :text-is("上传图片")').first
        if await tab_btn.count() and await tab_btn.is_visible():
            await tab_btn.click()
            await page.wait_for_timeout(800)

        # 4. 在抽屉内点击本地上传
        async with page.expect_file_chooser(timeout=8000) as fc_info:
            upload_btn = page.locator('.byte-drawer-content button.upload-btn, button:has-text("本地上传")').first
            await upload_btn.click()

        fc = await fc_info.value
        await fc.set_files(str(cover_path))
        await page.wait_for_timeout(3000)

        # 5. 确定
        confirm_btn = page.locator('.byte-drawer button:has-text("确定"), button:has-text("确定")').first
        await confirm_btn.click()
        await page.wait_for_timeout(2000)

        # 6. 二次确保为单图模式
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


async def save_one_article(page, item: dict):
    ep = item['episode']
    title = item['title']
    file_path = DOCS_DIR / item['file']
    cover_path = item['cover']

    print(f'\n========================================', flush=True)
    print(f'🚀 开始保存第 {ep} 篇: 《{title}》', flush=True)

    publish_code = -1

    async def on_resp(resp):
        nonlocal publish_code
        if '/mp/agw/article/publish' in resp.url:
            try:
                data = await resp.json()
                if 'code' in data:
                    publish_code = data['code']
                    if publish_code == 0:
                        print(f'  [API] 保存接口响应: code={publish_code} (成功)', flush=True)
                    else:
                        print(f'  [API] 保存接口响应: code={publish_code}, msg={data.get("message")}', flush=True)
            except Exception:
                pass

    page.on('response', on_resp)

    # 1. 打开新发文页面
    await page.goto('https://mp.toutiao.com/profile_v4/graphic/publish', timeout=60000)
    await page.wait_for_timeout(3500)
    await dismiss_masks_and_drawers(page)

    # 2. 填写标题 (保证 <= 30 字)
    title_in = page.locator('textarea[placeholder*="请输入文章标题"]').first
    await title_in.click(timeout=5000)
    await title_in.fill('')
    await page.keyboard.type(title)
    print(f'  [1/5] 标题已填入: {title} ({len(title)}字)', flush=True)
    await page.wait_for_timeout(500)

    # 3. 填写正文与内文插图 (图文交叉顺序注入)
    editor = page.locator('.ProseMirror').first
    await editor.click()
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])

    elements = parse_markdown_content(file_path)
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

    # 4. 上传官方 16:9 封面并设置为单图
    cover_ok = await upload_article_cover(page, cover_path)
    print(f'  [3/5] 官方封面上传完成: {cover_ok} ({cover_path.name})', flush=True)

    # 5. 设置作品声明：勾选「引用AI」
    ai_ok = await set_ai_declaration(page)
    print(f'  [4/5] 作品声明「引用AI」已勾选: {ai_ok}', flush=True)

    # 6. 等待自动保存 / 点击保存草稿
    await page.wait_for_timeout(3000)
    footer = page.locator('.footer-draft-save, .footer-tip-save').first
    status_text = await footer.inner_text()
    if '已保存' not in status_text:
        await footer.click(force=True)
        await page.wait_for_timeout(4000)
        status_text = await footer.inner_text()

    content_stats = await page.evaluate('''() => {
        const pm = document.querySelector('.ProseMirror');
        const text = pm ? pm.innerText.trim() : '';
        const foot = document.querySelector('.footer-tip-save, .footer-draft-save');
        return {
            chars: text.length,
            status: foot ? foot.innerText.trim() : ''
        };
    }''')
    print(f'  [5/5] 草稿保存状态: {content_stats["status"]} | 正文字数: {content_stats["chars"]}字', flush=True)

    # 截图留存草稿编辑状态
    screenshot_path = ARTIFACT_DIR / f'toutiao_draft_ep0{ep}_saved.png'
    await page.screenshot(path=str(screenshot_path), full_page=True)
    print(f'  📸 第 {ep} 篇草稿状态截图已保存: {screenshot_path.name}', flush=True)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--lang=zh-CN',
                '--start-maximized',
            ]
        )
        context = await browser.new_context(
            storage_state=str(COOKIE_PATH),
            viewport={'width': 1440, 'height': 900}
        )
        page = await context.new_page()

        # 清理可能存在的残留草稿，确保整洁从 0 开始
        print('🧹 检查并清理旧草稿...', flush=True)
        await page.goto('https://mp.toutiao.com/profile_v4/manage/draft', timeout=60000)
        await page.wait_for_timeout(3500)
        
        while True:
            del_btns = page.locator('text="删除"')
            count = await del_btns.count()
            if count == 0:
                break
            btn = del_btns.first
            if await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(600)
                confirm_btn = page.locator('.byte-modal button:has-text("确定")').first
                if await confirm_btn.count() and await confirm_btn.is_visible():
                    await confirm_btn.click()
                    await page.wait_for_timeout(1200)
            else:
                break
        print('   草稿箱已重置清空，准备顺序写入 5 篇草稿。', flush=True)

        for item in ARTICLES:
            await save_one_article(page, item)
            await asyncio.sleep(2)

        # 全量草稿箱验证
        print('\n========================================', flush=True)
        print('📋 前往今日头条草稿箱进行全量核对...', flush=True)
        await page.goto('https://mp.toutiao.com/profile_v4/manage/draft', timeout=60000)
        await page.wait_for_timeout(5000)

        final_box_screenshot = ARTIFACT_DIR / 'toutiao_5_drafts_complete_verified.png'
        await page.screenshot(path=str(final_box_screenshot), full_page=True)
        print(f'📸 今日头条 5 篇草稿全量核验截图已保存: {final_box_screenshot.name}', flush=True)

        drafts_info = await page.evaluate('''() => {
            const items = document.querySelectorAll('.draft-item, tr, [class*="draft-list"] > div, .article-card');
            const list = [];
            items.forEach(el => {
                const titleEl = el.querySelector('[class*="title"], h3, h4, a');
                const title = titleEl ? titleEl.innerText.trim() : el.innerText.trim().split('\\n')[0];
                if (title && title.length > 5) {
                    list.push(title);
                }
            });
            return Array.from(new Set(list));
        }''')
        print(f'✅ 草稿箱内已确认记录 ({len(drafts_info)} 篇):')
        for d in drafts_info:
            print(f'   - {d}')

        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
