#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化保存「河狸云·蓝枢 Planora」3:4 图文笔记至小红书官方草稿箱。
严格规则：
1. 仅保存为草稿（点击「暂存离开」），绝不点击发布！
2. 每篇上传 3 张 3:4 (1080x1440) 官方高清卡片（含正版河狸云 Logo）。
3. 标题 <= 20 字，正文简洁干货、无任何导流/免费试点/外链违规词。
4. 勾选「添加内容类型声明 -> 笔记含AI合成内容」。
5. 脚本自行启动浏览器（持久化 profile），填好内容后保持浏览器打开，
   由你在后台手动点击「发布」。
"""
import asyncio
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'omnipost'))

from patchright.async_api import async_playwright
from uploader.xiaohongshu_uploader.main import XiaoHongShuNote
from utils.base_social_media import set_init_script
from utils.log import xiaohongshu_logger
from account_lookup import get_platform_account

IMG_BASE = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材' / '12_工地图纸多找详图难'
XHS_DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '12_工地图纸多找详图难_一稿五投'
COOKIE_PATH, ACCOUNT_USER = get_platform_account(1)
ARTIFACT_DIR = BASE_DIR / 'logs' / 'screenshots'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

import re as _re


def _load_xhs_body():
    raw = (XHS_DOCS_DIR / '母本A_小红书短图文_工地图纸多找详图难.md').read_text(encoding='utf-8')
    out = []
    for line in raw.splitlines():
        s = line.rstrip()
        st = s.strip()
        if st.startswith('# '):
            continue
        if st == '---':
            continue
        if st.startswith('> '):
            continue
        if st.startswith('!['):
            continue
        if st.startswith('#'):
            continue
        if st:
            out.append(s)
    return _re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip()


XHS_NOTES = [
    {
        'episode': 12,
        'title': '工地图纸找详图翻半天',
        'md_file': '母本A_小红书短图文_工地图纸多找详图难.md',
        'images': [
            IMG_BASE / '04_3x4_小红书封面_工地图纸多找详图难.png',
            IMG_BASE / '05_3x4_小红书内页1_工地图纸多找详图难.png',
            IMG_BASE / '06_3x4_小红书内页2_工地图纸多找详图难.png',
        ],
        'desc': _load_xhs_body(),
        'tags': ['施工图纸', '看图找详图', '工程人', '工地日常', '图纸管理'],
    },
]


def save_markdown_copies():
    XHS_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for note in XHS_NOTES:
        md_path = XHS_DOCS_DIR / note['md_file']
        tags_str = ' '.join(f"#{t}" for t in note['tags'])
        imgs_md = '\n'.join(f"![配图{i+1}]({img})" for i, img in enumerate(note['images']))
        content = (
            f"# {note['title']}\n\n"
            f"> **平台**：小红书图文笔记（3:4 竖版高清组图 × 3）\n"
            f"> **声明**：笔记含AI合成内容\n"
            f"> **标签**：{tags_str}\n\n"
            f"---\n\n"
            f"{imgs_md}\n\n"
            f"---\n\n"
            f"{note['desc']}\n\n"
            f"{tags_str}\n"
        )
        md_path.write_text(content, encoding='utf-8')
        print(f"📄 已保存小红书本地文稿: {md_path.name}")


PERSISTENT_PROFILE_DIR = BASE_DIR / 'omnipost' / 'browser_profiles' / 'xiaohongshu'


async def main():
    save_markdown_copies()
    PERSISTENT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(PERSISTENT_PROFILE_DIR),
            headless=False,
            channel="chrome",
            permissions=["geolocation", "clipboard-read", "clipboard-write"],
            viewport={"width": 1440, "height": 900},
        )
        if COOKIE_PATH and COOKIE_PATH.exists():
            state = json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
            if state.get("cookies"):
                await context.add_cookies(state["cookies"])
        else:
            print("⚠️ 未找到小红书有效 Cookie 文件，请先在 OmniPost 控制台 (http://localhost:5173) 扫码登录！")
        context = await set_init_script(context)
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto("https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch&target=image", timeout=60000)
        await page.wait_for_timeout(3500)

        for item in XHS_NOTES:
            ep = item['episode']
            title = item['title']
            print(f"\n==================================================")
            print(f"📕 [{ep}/5] 正在保存小红书图文草稿: {title} ({len(title)}字)")
            print(f"==================================================")

            uploader = XiaoHongShuNote(
                image_paths=[str(p) for p in item['images']],
                note=item['desc'],
                tags=item['tags'],
                publish_date=0,
                account_file=str(COOKIE_PATH),
                title=title,
                desc=item['desc'],
                headless=False,
                dry_run=False,
                ai_generated=True,
                is_draft=True,
            )

            await page.goto("https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch&target=image", timeout=60000)
            await page.wait_for_timeout(3000)

            upload_input = page.locator('input[type="file"][accept*="image"]').first
            if not await upload_input.count():
                upload_input = page.locator("div[class^='upload-content'] input[class='upload-input']").first
            await upload_input.wait_for(state="attached", timeout=30000)
            await upload_input.set_input_files([str(p) for p in item['images']])

            title_container = page.locator('input[placeholder*="填写标题"]').first
            await title_container.wait_for(state="visible", timeout=30000)
            await page.wait_for_timeout(2000)

            await title_container.click()
            await title_container.fill(title[:20])
            await page.wait_for_timeout(500)

            desc_editor = page.locator('div.ql-editor, [contenteditable="true"], p[data-placeholder*="输入正文描述"]').first
            await desc_editor.click()
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            await page.evaluate("async (t) => { await navigator.clipboard.writeText(t); }", item['desc'] + "\n")
            await page.keyboard.press("Meta+V")
            await page.wait_for_timeout(800)

            await uploader.fill_tags(page)
            await page.wait_for_timeout(800)

            ai_ok = await uploader.set_ai_content_declaration(page)
            print(f"  🤖 AI 合成内容声明勾选结果: {ai_ok}")
            await page.wait_for_timeout(800)

            draft_ok = await uploader._click_draft_button(page)
            print(f"  💾 暂存离开点击结果: {draft_ok}")
            await page.wait_for_timeout(2000)

            preview_shot = ARTIFACT_DIR / f"xhs_draft_ep0{ep}_saved.png"
            await page.screenshot(path=str(preview_shot), full_page=True)
            print(f"  📸 草稿页截图已保存: {preview_shot.name}")

        print("\n✅ 小红书草稿已保存，浏览器保持打开，请手动点击发布。")
        print("   ⚠️ 注意：不自动发布，由你在后台点击「发布」。")
        print("   🌐 浏览器将保持打开，请在页面上手动发布完成后再关闭本窗口。")

        await context.storage_state(path=str(COOKIE_PATH))

        print("   ⏳ 等待中，浏览器保持打开，请手动发布后关闭窗口...", flush=True)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    asyncio.run(main())
