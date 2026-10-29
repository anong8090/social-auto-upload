#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化保存 5 篇「河狸云·蓝枢 Planora」3:4 图文笔记至小红书官方草稿箱。
严格规则：
1. 仅保存为草稿（点击「暂存离开」），绝不点击发布！
2. 每篇上传 3 张 3:4 (1080x1440) 官方高清卡片（含正版河狸云 Logo）。
3. 标题 <= 20 字，正文简洁干货、无任何导流/免费试点/外链违规词。
4. 勾选「添加内容类型声明 -> 笔记含AI合成内容」。
"""
import asyncio
import os
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

IMG_BASE = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '图片素材' / '知乎专栏连载分期配图'
XHS_DOCS_DIR = BASE_DIR / '河狸云蓝枢_自媒体物料包' / '文案合集' / '小红书图文笔记_5篇草稿'
COOKIE_PATH, ACCOUNT_USER = get_platform_account(1)
ARTIFACT_DIR = BASE_DIR / 'logs' / 'screenshots'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

XHS_NOTES = [
    {
        'episode': 1,
        'title': '设计院发来CAD图，现场打开变白卷？',
        'md_file': '第一期_设计院发来CAD图_现场打开变白卷.md',
        'images': [
            IMG_BASE / '第一期_成套解析与外部参照' / '00_小红书3比4封面_外部参照防白卷.png',
            IMG_BASE / '第一期_成套解析与外部参照' / '02_原理对比_外部参照引用链机制.png',
            IMG_BASE / '第一期_成套解析与外部参照' / '03_实机视窗_缺少外部参照直接清单点名.png',
        ],
        'desc': (
            "在工地一线看图的工程师，大概都遇到过这种抓狂瞬间：\n"
            "钢筋工木工在基坑边等着放线，手机点开设计院发来的平面图 DWG，加载完却空空荡荡——没轴网、没柱位，直接开出白卷！\n\n"
            "🔍 为什么现场会“开天窗”？\n"
            "根本原因不是手机坏了，而是【外部参照（Xref）脱节】：\n"
            "1️⃣ 散图漏发：设计院只发了主图，漏发引用的轴网底图；\n"
            "2️⃣ 路径断裂：微信群多次转发解压后，文件夹相对路径失效；\n"
            "3️⃣ 渲染跳过：轻量看图软件找不到底图，只能跳过渲染留下一片空白。\n\n"
            "💡 河狸云·蓝枢怎么破局？\n"
            "✅ 按套混选上传：按“项目+专业+楼栋”把整套 DWG/PDF 拖入云端统一解析；\n"
            "✅ 缺图清单点名：后台自动扫描引用树，缺哪张外部参照底图直接弹窗列出文件名，补齐后一键重试缝合；\n"
            "✅ 图框智能切分：把包含几十张子图的大 DWG 自动拆成独立单图，提取图名图号，手机秒开不卡死！\n\n"
            "📌 现场避坑建议：图纸下发前先做一次成套引用体检，底图打扎实，现场交底才不掉链子！"
        ),
        'tags': ['工程图纸', 'CAD看图', '施工现场管理', '土木工程', '工程人日常'],
    },
    {
        'episode': 2,
        'title': '群里发了第8版图，现场还在按旧图施工？',
        'md_file': '第二期_群里发了第8版图_现场还在按旧图施工.md',
        'images': [
            IMG_BASE / '第二期_现行锁控与防错闭环' / '00_小红书3比4封面_未确认不算现行版.png',
            IMG_BASE / '第二期_现行锁控与防错闭环' / '02_风控闭环_现行版三步管控流程.png',
            IMG_BASE / '第二期_现行锁控与防错闭环' / '03_实机视窗_现行版确认与智能目录台账.png',
        ],
        'desc': (
            "问项目总工最怕什么质量事故，“拿作废旧图放线施工”绝对排前三！\n"
            "设计变更单发进微信群，几百条工作汇报一刷屏就沉了底。带班工长顺手从手机缓存里调出半个月前的旧图扎筋浇筑，等监理复核发现洞口偏了 800mm，打凿返工直接损失十几万！\n\n"
            "⚠️ 为什么靠“群发通知”防不住旧图？\n"
            "1️⃣ 群消息极易刷屏沉底，没人敢保证全员看到；\n"
            "2️⃣ 手机微信本地缓存陷阱，随手点开往往是旧文件；\n"
            "3️⃣ 普通网盘上传即生效，缺少总工审核把关。\n\n"
            "🔒 河狸云·蓝枢：未确认不算现行版！\n"
            "✅ 双态隔离机制：图纸上传后先进“待审工作区”，未经核准绝不下发作业面；\n"
            "✅ 主管在线锁控：技术负责人核对无误点击「确认」，才正式晋升为唯一现行版；\n"
            "✅ 多端毫秒同步：新版生效瞬间旧图自动归档，手机电脑打开永远锁定最新现行版，查看历史版本会弹出醒目防错警示！\n\n"
            "📌 把“靠人自觉找新图”升级为“系统锁死现行版”，从源头守住不返工底线！"
        ),
        'tags': ['工程项目管理', '项目总工', '施工现场管理', '工程图纸', '建筑施工'],
    },
    {
        'episode': 3,
        'title': '图面标了个详03，现场找图要翻半小时？',
        'md_file': '第三期_图面标了个详03_现场找图要翻半小时.md',
        'images': [
            IMG_BASE / '第三期_图面穿透与双栏对照' / '00_小红书3比4封面_图面索引一秒穿透.png',
            IMG_BASE / '第三期_图面穿透与双栏对照' / '02_交互对比_传统盲翻对比蓝枢秒通.png',
            IMG_BASE / '第三期_图面穿透与双栏对照' / '03_实机视窗_图面热区与双栏同屏对照.png',
        ],
        'desc': (
            "在工地现场看图，最耗精力的往往不是看图本身，而是来回切屏找详图、翻图集！\n"
            "平面图上标着「详 03」、「剖面 1-1」或「做法见 11ZJ901」，传统看图软件里只能：\n"
            "退出当前图纸 ➔ 在几百张目录里盲翻 ➔ 打开大样图重新缩放找节点 ➔ 回工棚翻纸质国标图集……一天下来全在切屏折腾。\n\n"
            "🚀 河狸云·蓝枢：让死图纸变成“活超链接”！\n"
            "1️⃣ 图面热区一秒穿透：后台自动识别剖切与详图索引符号（如 T-301），图面生成蓝色高亮热区，手指一点毫秒跳转对应大样图！\n"
            "2️⃣ 双栏同屏对照：电脑端左屏看平面图、右屏联动看节点大样或结构配筋，同屏比对不再来回切窗口；\n"
            "3️⃣ 大白话搜国标图集：输入施工部位或做法关键词，直接关联翻到国家标准图集对应页，图纸与规范同屏核对！\n\n"
            "📌 减少 80% 翻目录找图时间，现场交底验收真正做到心中有数！"
        ),
        'tags': ['施工员日常', 'CAD看图', '建筑规范图集', '工程图纸', '土木工程'],
    },
    {
        'episode': 4,
        'title': '给甲方监理发CAD图，如何免装软件防泄密',
        'md_file': '第四期_给甲方监理发CAD图_如何免装软件防泄密.md',
        'images': [
            IMG_BASE / '第四期_无感协同与对外分享' / '00_小红书3比4封面_微信发链接秒看.png',
            IMG_BASE / '第四期_无感协同与对外分享' / '02_安全对比_随手乱甩文件对比受控链接分享.png',
            IMG_BASE / '第四期_无感协同与对外分享' / '03_实机视窗_微信免登录看图与手机离线圈阅.png',
        ],
        'desc': (
            "工程推进中，总包经常要把图纸发给甲方代表、监理或外部顾问，但每次发图都陷入两难：\n"
            "❌ 直接发 DWG 源文件：对方手机打不开，要逼领导下载几百兆看图 APP 还要注册账号，沟通阻力极大；而且源文件一旦扔进大群随意转发，核心资产彻底失控。\n"
            "❌ 转成 PDF 或截图发：几十兆大图一压缩，放大后线条文字全糊成马赛克。\n\n"
            "💡 河狸云·蓝枢：对内严锁控，对外零门槛交付！\n"
            "1️⃣ 微信发链接秒看：选中图纸一键生成分享链接发微信，甲方监理点开直接在浏览器秒开高清矢量图，免装 APP、免注册账号！\n"
            "2️⃣ 权限与有效期受控：支持设置有效查看时间（到期自动熔断）、访问密码与仅浏览防下载，随时可撤回；\n"
            "3️⃣ 手机离线圈阅同步：提前缓存整套图纸，地下室基坑无信号也能秒开；手机画笔圈阅拍现场照片，办公室电脑端立刻同步定位！\n\n"
            "📌 对外沟通丝滑零门槛，企业核心图纸资产牢牢管得住！"
        ),
        'tags': ['工程管理', '施工现场管理', '甲方监理', 'CAD看图', '工程图纸'],
    },
    {
        'episode': 5,
        'title': '单机看图软件vs协同云，工程项目怎么选？',
        'md_file': '第五期_单机看图软件vs协同云_工程项目怎么选.md',
        'images': [
            IMG_BASE / '第五期_工具选型与边界对比' / '00_小红书3比4封面_看图工具与协同中枢.png',
            IMG_BASE / '第五期_工具选型与边界对比' / '02_选型矩阵_四维分工对照表.png',
            IMG_BASE / '第五期_工具选型与边界对比' / '03_小红书3比4_现场图纸全生命周期协同流水线.png',
        ],
        'desc': (
            "很多工程同行会问：“平时单机看图软件用得很顺手，为什么项目部还需要河狸云·蓝枢？”\n"
            "其实这两者根本不是替代关系，而是解决不同维度痛点的【黄金互补组合】！\n\n"
            "📊 一眼看懂四维分工边界：\n"
            "🔹 个人单机看图工具（管个人效率）：\n"
            "适合办公室深化设计、单张图纸快速打开、画线测量算量，是优秀的个人桌面工具。\n\n"
            "🔸 河狸云·蓝枢协同中枢（守团队防错底线）：\n"
            "1️⃣ 成套上传解析：外部参照自动排查点名 + 图框智能切分子图；\n"
            "2️⃣ 现行核验锁控：技术主管确认才算现行版，全端同步锁死防旧图返工；\n"
            "3️⃣ 极速看图跳转：图面索引变超链接秒跳详图，双栏同屏直通国标图集；\n"
            "4️⃣ 现场无感协同：手机离线缓存 + 现场画笔圈阅同步 + 微信受控链接免装软件秒看。\n\n"
            "📌 选型总结：单兵绘图测量用单机工具，团队版本锁控与多方协同上蓝枢中枢，各司其职才不返工！"
        ),
        'tags': ['工程数字化', '工程项目管理', '土木工程师', 'CAD看图', '工程图纸'],
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

import json

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

        # 先检查当前持久化浏览器本地草稿箱中已有的图文草稿数量
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

            # 等待进入标题填写界面
            title_container = page.locator('input[placeholder*="填写标题"]').first
            await title_container.wait_for(state="visible", timeout=30000)
            await page.wait_for_timeout(2000)

            # 1. 填写标题
            await title_container.click()
            await title_container.fill(title[:20])
            await page.wait_for_timeout(500)

            # 2. 填写正文描述（使用剪贴板快速粘贴保证段落与表情完整）
            desc_editor = page.locator('div.ql-editor, [contenteditable="true"], p[data-placeholder*="输入正文描述"]').first
            await desc_editor.click()
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            await page.evaluate("async (t) => { await navigator.clipboard.writeText(t); }", item['desc'] + "\n")
            await page.keyboard.press("Meta+V")
            await page.wait_for_timeout(800)

            # 3. 填写话题标签
            await uploader.fill_tags(page)
            await page.wait_for_timeout(800)

            # 4. 勾选「笔记含AI合成内容」
            ai_ok = await uploader.set_ai_content_declaration(page)
            print(f"  🤖 AI 合成内容声明勾选结果: {ai_ok}")
            await page.wait_for_timeout(800)

            # 保存前预览截图
            preview_shot = ARTIFACT_DIR / f"xhs_draft_ep0{ep}_preview.png"
            await page.screenshot(path=str(preview_shot), full_page=True)
            print(f"  📸 已保存预览截图: {preview_shot.name}")

            # 5. 点击「暂存离开」保存至草稿箱
            clicked = await uploader._click_draft_button(page)
            print(f"  💾 点击「暂存离开」结果: {clicked}")
            await page.wait_for_timeout(4000)

            saved_shot = ARTIFACT_DIR / f"xhs_draft_ep0{ep}_saved.png"
            await page.screenshot(path=str(saved_shot), full_page=True)
            print(f"  🎉 第 {ep} 期小红书草稿保存完毕！当前 URL: {page.url}")

        # 打开草稿箱列表并切换到「图文笔记」标签页截图核验
        print("\n🔍 正在打开小红书图文草稿箱核验全部 5 篇草稿...")
        await page.goto("https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch&target=image", timeout=60000)
        await page.wait_for_timeout(3000)
        try:
            draft_box_btn = page.locator('text=/草稿箱/').first
            if await draft_box_btn.count() and await draft_box_btn.is_visible():
                await draft_box_btn.click()
                await page.wait_for_timeout(2000)
                img_note_tab = page.locator('text=/图文笔记/').first
                if await img_note_tab.count() and await img_note_tab.is_visible():
                    await img_note_tab.click()
                    await page.wait_for_timeout(2000)
        except Exception as e:
            print(f"点击草稿箱入口提示: {e}")

        final_shot = ARTIFACT_DIR / "xhs_5_drafts_complete_verified.png"
        await page.screenshot(path=str(final_shot), full_page=True)
        print(f"🏆 小红书 5 篇草稿核验截图已保存: {final_shot}")

        await context.storage_state(path=str(COOKIE_PATH))
        await context.close()

    # 自动将草稿桥接同步至用户日常主 Google Chrome 浏览器的 IndexedDB
    try:
        from sync_xhs_idb_to_main_chrome import sync_to_main_chrome
        await sync_to_main_chrome()
    except Exception as e:
        print(f"⚠️ 同步至主 Chrome IndexedDB 提示: {e}")

if __name__ == '__main__':
    asyncio.run(main())
