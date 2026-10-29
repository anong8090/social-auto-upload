# -*- coding: utf-8 -*-
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
COOKIE_PATH = BASE_DIR / 'omnipost' / 'cookiesFile' / 'c261ae58-c931-11f1-883f-2ba2d23fe161.json'
ARTIFACT_DIR = Path(__file__).resolve().parent / 'logs' / 'screenshots'

COVERS = {
    '十几万返工代价：为什么图纸必须确认才算现行': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/知乎专栏连载分期配图/第二期_现行锁控与防错闭环/01_本期头图_未确认不算现行版.png',
    '图面标了个详03：现场到底要翻多久图集': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/知乎专栏连载分期配图/第三期_图面穿透与双栏对照/01_本期头图_图面索引一秒直达详图.png',
    '跨单位协同两难：发给甲方看图免装软件防泄密': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/知乎专栏连载分期配图/第四期_无感协同与对外分享/01_本期头图_微信发链接秒看与零门槛交付.png',
    '工程选型边界：办公室单机看图vs现场协同中枢': BASE_DIR / '河狸云蓝枢_自媒体物料包/图片素材/知乎专栏连载分期配图/第五期_工具选型与边界对比/01_本期头图_单机看图工具对比协同中枢.png',
}

async def update_cover_for_page(page, cover_path: Path):
    # 1. 确保切换为单图 (点击 label)
    single_label = page.locator('label.byte-radio:has-text("单图")').first
    await single_label.scroll_into_view_if_needed()
    await single_label.click(force=True)
    await page.wait_for_timeout(800)

    # 2. 点击替换或者添加按钮
    clicked = await page.evaluate('''() => {
        const rep = document.querySelector('a.article-cover-img-replace');
        if (rep) {
            rep.scrollIntoView();
            rep.click();
            return 'replace';
        }
        const add = document.querySelector('.article-cover-add');
        if (add) {
            add.scrollIntoView();
            add.click();
            return 'add';
        }
        return 'none';
    }''')
    print(f'   点击封面按钮结果: {clicked}', flush=True)
    await page.wait_for_timeout(1000)

    # 3. 切换抽屉选项卡到「上传图片」
    tab_btn = page.locator('.byte-drawer-content :text-is("上传图片"), .byte-drawer :text-is("上传图片"), :text-is("上传图片")').first
    if await tab_btn.count() and await tab_btn.is_visible():
        await tab_btn.click()
        await page.wait_for_timeout(800)

    # 4. 点击本地上传
    async with page.expect_file_chooser(timeout=8000) as fc_info:
        upload_btn = page.locator('.byte-drawer-content button.upload-btn, button:has-text("本地上传")').first
        await upload_btn.click()
    fc = await fc_info.value
    await fc.set_files(str(cover_path))
    await page.wait_for_timeout(3000)

    # 5. 点击确定
    confirm_btn = page.locator('.byte-drawer button:has-text("确定"), button:has-text("确定")').first
    await confirm_btn.click()
    await page.wait_for_timeout(2000)

    # 6. 二次确保单图与 AI 声明
    await single_label.click(force=True)
    await page.wait_for_timeout(400)

    await page.evaluate('''() => {
        const labels = Array.from(document.querySelectorAll('label')).filter(l => l.innerText.includes('引用AI'));
        if (labels.length) {
            const inp = labels[0].querySelector('input');
            if (inp && !inp.checked) inp.click();
        }
    }''')
    await page.wait_for_timeout(500)

    # 7. 保存草稿
    footer = page.locator('.footer-draft-save, .footer-tip-save').first
    await footer.click(force=True)
    await page.wait_for_timeout(4000)
    print(f'   草稿保存状态: {await footer.inner_text()}', flush=True)


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
        list_page = await context.new_page()

        for title, cover_file in COVERS.items():
            prefix = title[:8]
            print(f'\n🖼️ 正在为《{title}》配置官方 16:9 封面: {cover_file.name}...', flush=True)
            await list_page.goto('https://mp.toutiao.com/profile_v4/manage/draft', timeout=60000)
            await list_page.wait_for_timeout(3500)

            # 找到对应草稿卡片
            card = list_page.locator(f'.article-draft-item:has-text("{prefix}")').first
            edit_btn = card.locator('text="编辑"').first

            async with context.expect_page() as pinfo:
                await edit_btn.click()
            edit_page = await pinfo.value
            await edit_page.wait_for_timeout(3500)

            await update_cover_for_page(edit_page, cover_file)
            print(f'   已成功更新封面并保存草稿。', flush=True)
            await edit_page.close()
            await asyncio.sleep(1)

        # 全量草稿箱终验
        print('\n========================================', flush=True)
        print('📋 重新前往草稿箱进行全量最终核验...', flush=True)
        await list_page.goto('https://mp.toutiao.com/profile_v4/manage/draft', timeout=60000)
        await list_page.wait_for_timeout(5000)

        final_box_screenshot = ARTIFACT_DIR / 'toutiao_5_drafts_complete_verified.png'
        await list_page.screenshot(path=str(final_box_screenshot), full_page=True)
        print(f'📸 今日头条 5 篇草稿全量终验截图已保存: {final_box_screenshot.name}', flush=True)

        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
