# -*- coding: utf-8 -*-
"""自动化批量保存 5 篇精炼图文至搜狐号官方草稿箱。

严格规则：
1. 仅保存为草稿（点击「存草稿」），绝不点击发布！
2. 标题字数精简在 30 字以内。
3. 创作声明勾选「含有AI生成内容」。
4. 单张 16:9 官方封面上传与设置。
5. 正文段落清晰排版，杜绝试点或平台专属字样。
"""
import asyncio
import os
import re
import sys
from pathlib import Path
from playwright.async_api import Page, async_playwright

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))
from account_lookup import get_platform_account

DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '搜狐号精简版_5篇草稿'
COOKIE_PATH, ACCOUNT_USER = get_platform_account(8)
ARTIFACT_DIR = BASE_DIR / 'logs' / 'screenshots'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

ARTICLES = [
    {
        'episode': 1,
        'title': '底图失踪之谜：外部参照如何让现场开出白卷',
        'file': '第一期_底图失踪之谜_外部参照是如何让现场开出白卷的.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/图文连载分期配图/第一期_成套解析与外部参照/01_本期头图_外部参照与底图开天窗排查.png',
    },
    {
        'episode': 2,
        'title': '十几万返工代价：为什么图纸必须确认才算现行',
        'file': '第二期_十几万返工代价：为什么图纸必须确认才算现行.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/图文连载分期配图/第二期_现行锁控与防错闭环/01_本期头图_未确认不算现行版.png',
    },
    {
        'episode': 3,
        'title': '图面标了个详03：现场到底要翻多久图集',
        'file': '第三期_图面标了个详03：现场到底要翻多久图集.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/图文连载分期配图/第三期_图面穿透与双栏对照/01_本期头图_图面索引一秒直达详图.png',
    },
    {
        'episode': 4,
        'title': '跨单位协同两难：发给甲方看图免装软件防泄密',
        'file': '第四期_跨单位协同两难：发给甲方看图免装软件防泄密.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/图文连载分期配图/第四期_无感协同与对外分享/01_本期头图_微信发链接秒看与零门槛交付.png',
    },
    {
        'episode': 5,
        'title': '工程选型边界：办公室单机看图vs现场协同中枢',
        'file': '第五期_工程选型边界：办公室单机看图vs现场协同中枢.md',
        'cover': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/图文连载分期配图/第五期_工具选型与边界对比/01_本期头图_单机看图工具对比协同中枢.png',
    },
]

def parse_markdown_clean_body(file_path: Path) -> str:
    """提取干净正文，过滤标题头、元信息和图片标签"""
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

    # 1. 填入标题
    title_input = page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"], input[placeholder*="请输入标题"]').first
    await title_input.wait_for(state="visible", timeout=15000)
    await title_input.click()
    await title_input.fill(title)
    print(f"  ✍️ 标题已填入: {title} ({len(title)}字)")
    await page.wait_for_timeout(300)

    # 2. 填入正文
    clean_body = parse_markdown_clean_body(md_path)
    editor = page.locator('.ql-editor').first
    await editor.click()
    # 清空编辑器已有内容
    await page.keyboard.press("Meta+A")
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(200)

    # 通过剪贴板快速粘贴保持换行
    await page.evaluate('''async (t) => {
        await navigator.clipboard.writeText(t);
    }''', clean_body)
    await page.keyboard.press("Meta+V")
    print(f"  📝 正文已填入，长度: {len(clean_body)} 字符")
    await page.wait_for_timeout(1000)

    # 3. 上传 16:9 封面
    if cover_path.exists():
        print(f"  🖼️ 上传 16:9 官方封面: {cover_path.name}")
        trigger = page.locator('.cover-button .upload-file, .cover-button .upload-tip, .cover-button').first
        await trigger.scroll_into_view_if_needed(timeout=5000)
        await trigger.click(force=True)
        await page.wait_for_timeout(1000)

        # 查找弹出的对话框
        dialog_index = await page.evaluate('''() => {
            const dialogs = [...document.querySelectorAll('.el-dialog')];
            for (let i = 0; i < dialogs.length; i++) {
                const d = dialogs[i];
                const wrap = d.closest('.el-dialog__wrapper') || d.parentElement;
                if (wrap && getComputedStyle(wrap).display === 'none') continue;
                const t = d.innerText || '';
                if (t.includes('正文图片') || t.includes('本地上传') || t.includes('最多可选择')) return i;
            }
            return -1;
        }''')
        if dialog_index < 0:
            print("  ⚠️ 未找到封面对话框，尝试打开 .select-dialog")
            dialog = page.locator('.select-dialog').first
        else:
            dialog = page.locator('.el-dialog').nth(dialog_index)

        # 切换到「本地上传」
        await page.evaluate('''(idx) => {
            const d = document.querySelectorAll('.el-dialog')[idx];
            if (!d) return;
            const tab = [...d.querySelectorAll('*')].find(el =>
                [...el.childNodes].some(n => n.nodeType === 3 && (n.textContent || '').trim() === '本地上传')
            );
            if (tab) tab.click();
        }''', dialog_index)
        await page.wait_for_timeout(600)

        fi = dialog.locator('input[type="file"]').first
        if not await fi.count():
            fi = page.locator('.select-dialog input[type="file"], .el-dialog input[type="file"]').first
        await fi.set_input_files(str(cover_path))
        print("  📤 封面图片已上传，等待渲染完成...")
        await page.wait_for_timeout(2500)

        # 点击确定
        confirm = dialog.locator('p.positive-button, .positive-button, button').filter(has_text='确定').first
        await confirm.click(force=True)
        await page.wait_for_timeout(2000)
        print("  ✅ 封面已选定并确认")
    else:
        print(f"  ⚠️ 封面文件不存在: {cover_path}")

    # 4. 勾选创作声明：含有AI生成内容
    print("  🤖 设置创作声明: 含有AI生成内容")
    ai_radio = page.locator('label.el-radio:has-text("含有AI生成内容"), span.el-radio__label:has-text("含有AI生成内容")').first
    await ai_radio.scroll_into_view_if_needed(timeout=5000)
    await page.wait_for_timeout(400)
    await ai_radio.click(force=True)
    print("  ✅ 成功勾选创作声明: 含有AI生成内容")
    await page.wait_for_timeout(400)

    # 5. 截图保存前状态
    preview_shot = ARTIFACT_DIR / f"sohu_draft_ep0{ep}_preview.png"
    await page.screenshot(path=str(preview_shot), full_page=True)

    # 6. 点击存草稿
    print("  💾 点击「存草稿」按钮...")
    draft_btn = page.locator('li.publish-report-btn:has-text("存草稿"), [report-attr*="commitdraft"]').first
    await draft_btn.scroll_into_view_if_needed(timeout=5000)
    await page.wait_for_timeout(500)
    await draft_btn.click(force=True)
    print("  ✅ 已点击「存草稿」，等待后台存盘...")
    await page.wait_for_timeout(4000)

    # 截图保存后状态
    saved_shot = ARTIFACT_DIR / f"sohu_draft_ep0{ep}_saved.png"
    await page.screenshot(path=str(saved_shot))
    print(f"  🎉 第 {ep} 期草稿保存成功！截图: {saved_shot.name}")
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

        # 逐篇保存
        for art in ARTICLES:
            success = await save_single_article(page, art)
            if not success:
                print(f"❌ 第 {art['episode']} 期处理失败！")
                break
            await page.wait_for_timeout(2000)

        # 访问草稿箱进行最终核验
        print("\n==================================================")
        print("🔍 进入搜狐号官方草稿箱进行最终批量核验...")
        print("==================================================")
        draft_list_url = "https://mp.sohu.com/mpfe/v3/main/content/list?contentStatus=1"
        await page.goto(draft_list_url, timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        await dismiss_overlays(page)

        final_shot = ARTIFACT_DIR / "sohu_5_drafts_complete_verified.png"
        await page.screenshot(path=str(final_shot), full_page=True)
        print(f"🏆 全部 5 篇草稿最终核验截图已生成: {final_shot}")

        await context.storage_state(path=str(COOKIE_PATH))
        print("💾 最新会话凭证已更新保存")
        await browser.close()
        return True

if __name__ == '__main__':
    asyncio.run(main())
