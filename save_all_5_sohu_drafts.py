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
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))

from playwright.async_api import Page, async_playwright
from account_lookup import get_platform_account

DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '12_工地图纸多找详图难_一稿五投'
IMG_BASE = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材' / '12_工地图纸多找详图难'
COOKIE_PATH, ACCOUNT_USER = get_platform_account(8)
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

def parse_sohu_blocks(md_path: Path, inline_images: list | None = None) -> list:
    raw = md_path.read_text(encoding='utf-8')
    blocks, cur = [], []
    inline_images = inline_images or []

    def flush_text():
        nonlocal cur
        if cur:
            blocks.append(('text', '\n'.join(cur).strip()))
            cur = []

    for line in raw.splitlines():
        s = line.strip()
        if s.startswith('# ') or s == '---':
            continue
        if s.startswith('>') and not inline_images:
            continue
        cl = s.replace('**', '')
        if not cl:
            flush_text()
            continue
        if cl.startswith('|'):
            continue
        cur.append(cl)
    flush_text()

    # 将内文配图穿插到文本块之间：第2个文本块后插第1张，第4个文本块后插第2张
    text_block_indices = [i for i, e in enumerate(blocks) if e[0] == 'text']
    insertions = []
    for n, img_path in enumerate(inline_images[:2]):
        if n < len(text_block_indices):
            pos = text_block_indices[n] + 1 + n
            insertions.append((pos, img_path))
    for pos, img_path in reversed(insertions):
        blocks.insert(pos, ('image', img_path))

    return blocks


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
        await editor.click()
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
    raw_text = file_path.read_text(encoding='utf-8')
    lines = raw_text.splitlines()
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('# ') or stripped.startswith('> '):
            continue
        if stripped == '---':
            continue
        if stripped.startswith('!['):
            continue
        clean_lines.append(line)
    return '\n'.join(clean_lines).strip()


async def dismiss_overlays(page: Page):
    try:
        await page.evaluate('''() => {
            const masks = document.querySelectorAll('.win-mask, .annual-background-mask');
            for (const m of masks) {
                const btn = m.querySelector('[class*="close"], button, .icon-close');
                if (btn) btn.click();
                else m.remove();
            }
        }''')
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
    try:
        clicked = await page.evaluate("""() => {
            const texts = ['存草稿', '保存草稿', '暂存'];
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
    editor = page.locator('.ql-editor').first
    await editor.click()
    await page.keyboard.press("Meta+A")
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(200)
    for btype, bval in blocks:
        if btype == 'text':
            await page.evaluate("async (t) => { await navigator.clipboard.writeText(t); }", str(bval) + "\n\n")
            await page.keyboard.press("Meta+V")
            await page.wait_for_timeout(700)
        else:
            await insert_sohu_inline_image(page, Path(bval))
    imgs_now = await page.locator('.ql-editor img').count()
    print(f"  📝 正文注入完毕，编辑器内图片数: {imgs_now}")
    await page.wait_for_timeout(1000)

    try:
        cover_btn = page.locator('input[type="file"][accept*="image"]').first
        if await cover_btn.count():
            await cover_btn.set_input_files(str(cover_path), timeout=6000)
            await page.wait_for_timeout(3000)
            print(f"  🖼️ 封面已上传: {cover_path.name}")
    except Exception as e:
        print(f"  ⚠️ 封面上传失败(继续): {e}")

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

    preview_shot = ARTIFACT_DIR / f"sohu_draft_ep0{ep}_saved.png"
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
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    asyncio.run(main())
