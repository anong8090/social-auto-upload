# -*- coding: utf-8 -*-
"""自动化批量保存精炼图文至搜狐号官方草稿箱。

严格规则：
1. 仅保存为草稿（点击「存草稿」），绝不点击发布！
2. 标题字数精简在 30 字以内。
3. 创作声明勾选「含有AI生成内容」。
4. 单张 16:9 官方封面上传与设置。
5. 正文段落清晰排版，杜绝试点或平台专属字样。
6. 脚本自行启动浏览器，填好内容后保持浏览器打开，
   由你在后台手动点击「发布」。
"""
import asyncio
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))

from playwright.async_api import Page, async_playwright
from account_lookup import get_platform_account
from draft_content_parser import parse_body_blocks, clean_markdown_body

DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '13_设计变更后图纸怎么跟_一稿五投'
IMG_BASE = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材' / '13_设计变更后图纸怎么跟'
COOKIE_PATH, ACCOUNT_USER = get_platform_account(8)
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

def parse_sohu_blocks(md_path: Path, inline_images: list | None = None) -> list:
    """统一解析：剥离元数据说明行、清洗裸露 Markdown 标记、内文图穿插。"""
    return parse_body_blocks(md_path, inline_images, skip_cover=True)


async def _wait_image_input(page: Page, timeout_s: int = 10):
    sel = 'input[type="file"][accept*="image"]'
    for _ in range(timeout_s * 2):
        loc = page.locator(sel)
        if await loc.count():
            return loc.first
        await page.wait_for_timeout(500)
    return None


async def insert_sohu_inline_image(page: Page, img_path: Path) -> bool:
    try:
        before = await page.locator('.ql-editor img').count()
        editor = page.locator('.ql-editor').first
        await editor.click(force=True)
        await page.wait_for_timeout(300)

        # 点击正文配图按钮（尝试多个选择器）
        btn = None
        for sel in ['.ql-toolbar .ql-image', 'button.ql-image', '[class*="ql-image"]', 'button:has-text("图片")']:
            b = page.locator(sel).first
            if await b.count() and await b.is_visible():
                btn = b
                break
        if not btn:
            print('    ⚠️ 未找到正文配图按钮，跳过'); return False
        await btn.click(force=True)
        await page.wait_for_timeout(1000)

        # 等待文件选择器出现
        fi = None
        for _ in range(20):
            await page.wait_for_timeout(500)
            candidates = [
                page.locator('input[type="file"][accept*="image"]').last,
                page.locator('input[type="file"]').last,
            ]
            for c in candidates:
                if await c.count():
                    fi = c
                    break
            if fi:
                break

        if not fi or not await fi.count():
            print('    ⚠️ 配图弹窗无文件输入，跳过'); return False

        await fi.set_input_files(str(img_path))

        # 等待上传完成提示
        for _ in range(24):
            await page.wait_for_timeout(500)
            if await page.locator('text=/已成功上传|上传完成|上传成功/').count():
                break

        # 点击确定按钮
        clicked = await page.evaluate("""() => {
            const e = [...document.querySelectorAll('p.positive-button, .positive-button, button, [role="button"]')]
                .find(x => (x.innerText || x.textContent || '').replace(/\\s/g, '') === '确定' && x.offsetHeight > 0);
            if (e) { e.click(); return true; }
            return false;
        }""")

        after = before
        for _ in range(16):
            await page.wait_for_timeout(500)
            after = await page.locator('.ql-editor img').count()
            if after > before:
                break

        ok = after > before
        print(f'    {"✅" if ok else "⚠️"} 内文配图 {img_path.name} (确定={clicked}, 编辑器图片 {before}→{after})')
        return ok
    except Exception as e:
        print(f'    ⚠️ 内文图失败(跳过): {e}'); return False


def parse_markdown_clean_body(file_path: Path) -> str:
    """统一正文清洗（剥离元数据说明行 + 清洗裸露 Markdown 标记）。"""
    return clean_markdown_body(file_path)


async def upload_sohu_cover(page: Page, cover_path: Path) -> bool:
    """搜狐封面完整流程：上传图片 → 切「本地上传」→ 选图 → 弹窗内「确定」→ 校验。

    注意：确认按钮必须在当前激活的 .el-dialog 内定位，
    否则会误点到弹窗外部隐藏的公式按钮，导致封面卡死。
    """
    if not cover_path.exists():
        print(f'  ⚠️ 封面文件不存在: {cover_path}')
        return False
    try:
        # 1. 找到并点击封面上传触发区
        try:
            await page.locator('.cover-title').first.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            pass
        trigger = page.locator('.cover-button .upload-file, .cover-button .upload-tip').first
        if not await trigger.count():
            trigger = page.get_by_text('上传图片', exact=True).first
        if not await trigger.count():
            print('  ⚠️ 未找到封面「上传图片」入口')
            return False
        await trigger.click(force=True)
        await page.wait_for_timeout(800)

        # 2. 找当前可见、含封面上传文案的弹窗。
        #    搜狐封面弹窗的容器可能是 .win-mask（不是 .el-dialog），两种都要找。
        marked = await page.evaluate("""() => {
          const sels = ['.win-mask', '.el-dialog', '[role="dialog"]'];
          const seen = new Set();
          for (const sel of sels) {
            for (const d of document.querySelectorAll(sel)) {
              if (seen.has(d)) continue;
              seen.add(d);
              const st = getComputedStyle(d);
              if (st.display === 'none' || st.visibility === 'hidden') continue;
              const r = d.getBoundingClientRect();
              if (r.width < 200 || r.height < 150) continue;
              const t = d.innerText || '';
              if (t.includes('正文图片') || t.includes('本地上传') || t.includes('最多可选择') || t.includes('已选择')) {
                d.setAttribute('data-sohu-cover-dialog', '1');
                return true;
              }
            }
          }
          return false;
        }""")
        if not marked:
            print('  ⚠️ 未找到搜狐封面对话框')
            return False
        dialog = page.locator('[data-sohu-cover-dialog="1"]').first

        # 3. 切到「本地上传」标签（在弹窗内部查找）
        await dialog.evaluate("""(d) => {
          const tab = [...d.querySelectorAll('*')].find(el =>
            [...el.childNodes].some(n => n.nodeType === 3 && (n.textContent || '').trim() === '本地上传')
          );
          if (tab) tab.click();
        }""")
        await page.wait_for_timeout(500)

        # 4. 选择本地文件
        file_input = dialog.locator('input[type="file"]').first
        if not await file_input.count():
            file_input = page.locator('.el-dialog input[type="file"]').last
        if not await file_input.count():
            print('  ⚠️ 封面对话框内无文件输入')
            return False
        await file_input.set_input_files(str(cover_path))
        print('  📤 已写入本地封面，等待选中…')

        # 5. 等待「已选择 N」
        for _ in range(30):
            await page.wait_for_timeout(400)
            try:
                text = await dialog.inner_text(timeout=2000)
            except Exception:
                text = ''
            m = __import__('re').search(r'已选择\s*(\d+)', text or '')
            if m and int(m.group(1)) >= 1:
                print(f'  ✅ 封面已选中: {m.group(0)}')
                break

        # 6. 在激活弹窗内点「确定」（p.positive-button）
        clicked = False
        try:
            confirm = dialog.locator('p.positive-button, .positive-button, button').filter(has_text='确定').first
            if await confirm.count():
                await confirm.click(force=True, timeout=5000)
                clicked = True
        except Exception:
            pass
        if not clicked:
            clicked = await dialog.evaluate("""(d) => {
              const oks = [...d.querySelectorAll('p.positive-button, .positive-button, button, .el-button')]
                .filter(b => (b.innerText || b.textContent || '').replace(/\\s/g, '') === '确定')
                .filter(b => { const r = b.getBoundingClientRect(); return r.width > 10 && r.height > 10; });
              if (!oks.length) return false;
              oks.sort((a, b) => b.getBoundingClientRect().y - a.getBoundingClientRect().y);
              oks[0].click();
              return true;
            }""")
        await page.wait_for_timeout(1800)

        # 7. 校验封面已应用
        ready = await page.evaluate("""() => {
          const pic = document.querySelector('.pic-cover');
          if (!pic) return false;
          const st = getComputedStyle(pic);
          const bg = st.backgroundImage || '';
          return st.display !== 'none' && (bg.includes('http') || bg.includes('blob:'));
        }""")
        if ready:
            print('  ✅ 封面已应用到发文页')
        elif clicked:
            print('  ⚠️ 已点确定，但未检测到封面预览')
        return bool(ready or clicked)
    except Exception as exc:
        print(f'  ⚠️ 封面流程异常: {exc}')
        return False


async def dismiss_overlays(page: Page):
    try:
        await page.evaluate('''() => {
            const masks = document.querySelectorAll('.win-mask, .annual-background-mask');
            for (const m of masks) {
                // 页面加载时可能自动弹出「设置封面」弹窗（也是 .win-mask），
                // 它会挡住正文工具栏，这里先关掉；真正上传封面时会重新打开。
                const html = m.innerHTML || '';
                const isCoverDialog = html.includes('new-file') || html.includes('upload-area');
                const btn = m.querySelector(
                    '[class*="close"], .icon-close, button[aria-label*="关闭"], .win-mask-close'
                );
                if (btn) { btn.click(); continue; }
                if (isCoverDialog) { m.remove(); continue; }
                m.remove();
            }
        }''')
    except Exception:
        pass
    # 关闭「识别到Markdown格式文字，是否解析…」提示条，避免遮挡工具栏
    try:
        await page.evaluate("""() => {
            document.querySelectorAll('[class*="tooltip"], [class*="tip"], [class*="notice"]').forEach(el => {
                const t = (el.innerText || el.textContent || '');
                if (t.includes('识别到Markdown格式文字') || t.includes('确认解析')) el.remove();
            });
        }""")
    except Exception:
        pass
    close_candidates = [
        'button:has-text("知道了")',
        'button:has-text("我知道了")',
        'button:has-text("暂不")',
        'button:has-text("关闭")',
        'button:has-text("取消")',
    ]
    for sel in close_candidates:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=800, force=True)
                await page.wait_for_timeout(200)
        except Exception:
            continue


async def click_sohu_draft_button(page: Page) -> bool:
    # 搜狐「存草稿」不一定是 <button>，可能是 <p>/<span>/<li> 等样式化元素，
    # 因此先按精确文本定位（Playwright text= 不限标签），再退回结构选择器。
    candidates = [
        'text="存草稿"',
        'p:has-text("存草稿")',
        'span:has-text("存草稿")',
        'li:has-text("存草稿")',
        'button:has-text("存草稿")',
        'div:has-text("存草稿")',
        'button:has-text("保存草稿")',
        'button:has-text("暂存")',
        '[class*="draft"] button',
        'button[class*="draft"]',
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(force=True, timeout=3000)
                await page.wait_for_timeout(1500)
                print(f'  💾 已点击「{sel}」')
                return True
        except Exception:
            continue
    try:
        clicked = await page.evaluate("""() => {
            const texts = ['存草稿', '保存草稿', '暂存'];
            // 扫描全部元素，取「最深」的精确文本节点，再点它或其可点击祖先
            const all = Array.from(document.querySelectorAll('body *'));
            const hits = all.filter(el => {
                const t = (el.innerText || el.textContent || '').trim();
                if (!texts.includes(t)) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 16) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || st.pointerEvents === 'none') return false;
                if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
                return true;
            });
            if (!hits.length) return false;
            // 取子元素最少的那个，避免点到包住整页的容器
            hits.sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
            const el = hits[0];
            const clickable = el.closest('button, [role="button"], a, li, p, span, div') || el;
            clickable.click();
            return true;
        }""")
        if clicked:
            print('  💾 已通过 JS 点击「存草稿」')
            await page.wait_for_timeout(1500)
            return True
    except Exception as e:
        print(f'  ⚠️ JS 点击存草稿失败: {e}')
    return False


async def save_single_article(page: Page, art: dict) -> bool:
    ep = art['episode']
    title = art['title']
    md_path = DOCS_DIR / art['file']
    cover_path = art['cover']

    print(f"\n==================================================")
    print(f"[{ep}/5] 正在处理第 {ep} 期: {title}")
    print(f"==================================================")

    editor_url = "https://mp.sohu.com/mpfe/v4/contentManagement/news/addarticle"
    await page.goto(editor_url, timeout=60000, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await dismiss_overlays(page)

    title_input = page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"], input[placeholder*="请输入标题"]').first
    await title_input.wait_for(state="visible", timeout=15000)
    await title_input.click()
    await title_input.fill(title)
    print(f"  ✍️ 标题已填入: {title} ({len(title)}字)")
    await page.wait_for_timeout(300)

    blocks = parse_sohu_blocks(md_path, art.get('inline_images', []))
    # 注入正文前再清一次遮罩：页面加载后可能延迟弹出「设置封面」弹窗挡住编辑器
    await dismiss_overlays(page)
    await page.wait_for_timeout(300)
    editor = page.locator('.ql-editor').first
    await editor.click(force=True)
    # 搜狐会自动恢复上一轮草稿：先全选删文本，再逐张点选图片组件删除。
    # 注意只做一轮全选，重复全选会把编辑器选区搞乱，导致后续粘贴/插图失败。
    await page.keyboard.press("Meta+A")
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(300)
    for _ in range(6):
        if await page.locator('.ql-editor img').count() == 0:
            break
        try:
            await page.locator('.ql-editor img').first.click(force=True)
            await page.wait_for_timeout(120)
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(180)
        except Exception:
            break
    left_imgs = await page.locator('.ql-editor img').count()
    try:
        left_text_len = len((await editor.inner_text()).strip())
    except Exception:
        left_text_len = -1
    print(f"  🧹 编辑器已清空，残留图片 {left_imgs} 张、文本 {left_text_len} 字")
    # 重新把光标放回正文起始位置
    await editor.click(force=True)
    await page.wait_for_timeout(200)
    for btype, bval in blocks:
        if btype == 'text':
            await page.evaluate("async (t) => { await navigator.clipboard.writeText(t); }", str(bval) + "\n\n")
            await page.keyboard.press("Meta+V")
            await page.wait_for_timeout(700)
        else:
            await insert_sohu_inline_image(page, Path(bval))
    imgs_now = await page.locator('.ql-editor img').count()
    body_now = await editor.inner_text()
    dup = body_now.count('设计一纸变更下来')
    print(f"  📝 正文注入完毕，编辑器内图片数: {imgs_now}，正文长度 {len(body_now)}，导语重复 {dup} 次")
    if imgs_now > 2 or dup > 1:
        print("  ⚠️ 自检异常：图片或正文疑似重复，请检查该草稿")
    await page.wait_for_timeout(1000)

    cover_ok = await upload_sohu_cover(page, cover_path)
    print(f"  🖼️ 封面上传: {'成功' if cover_ok else '失败/跳过'} ({cover_path.name})")

    print("  🤖 设置创作声明: 含有AI生成内容")
    ai_radio = page.locator('label.el-radio:has-text("含有AI生成内容"), span.el-radio__label:has-text("含有AI生成内容")').first
    await ai_radio.scroll_into_view_if_needed(timeout=5000)
    await page.wait_for_timeout(400)
    await ai_radio.click(force=True)
    print("  ✅ 成功勾选创作声明: 含有AI生成内容")
    await page.wait_for_timeout(400)

    draft_ok = await click_sohu_draft_button(page)
    if not draft_ok:
        print("  ⚠️ 未找到可点击的「存草稿」按钮，页面保持打开供手动存草稿")

    preview_shot = ARTIFACT_DIR / f"sohu_draft_ep{ep:02d}_saved.png"
    await page.screenshot(path=str(preview_shot), full_page=True)
    print(f"  📸 发布页截图已保存: {preview_shot.name}")
    return True


async def main():
    if not COOKIE_PATH.exists():
        print(f"❌ 找不到 Cookie 文件: {COOKIE_PATH}")
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                '--disable-blink-features=AutomationControlled',
                '--lang=zh-CN',
                '--start-maximized',
            ],
        )
        context = await browser.new_context(
            storage_state=str(COOKIE_PATH),
            viewport={'width': 1440, 'height': 900},
            permissions=['clipboard-read', 'clipboard-write'],
            locale='zh-CN',
        )
        page = await context.new_page()
        page.on("dialog", lambda d: asyncio.create_task(d.accept()))

        for art in ARTICLES:
            success = await save_single_article(page, art)
            if not success:
                print(f"❌ 第 {art['episode']} 期处理失败！")
                break
            await page.wait_for_timeout(2000)

        print("\n✅ 搜狐号草稿已保存，浏览器保持打开，请手动点击发布。")
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
