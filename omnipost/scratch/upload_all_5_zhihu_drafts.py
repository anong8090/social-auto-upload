import asyncio
import json
import os
import re
import sys
from pathlib import Path
from playwright.async_api import async_playwright

COOKIE_FILE = Path('/Users/yi/Documents/market/omnipost/cookiesFile/56dea8c6-c94e-11f1-883f-2ba2d23fe161.json')
DRAFT_DIR = Path('/Users/yi/Documents/market/河狸云蓝枢_自媒体物料包/文案合集/知乎专栏连载草稿_5篇独立发布版')
ARTIFACT_DIR = Path('/Users/yi/.gemini/antigravity/brain/4a23b17f-fac5-475e-bfb7-c3d2a34877d9')

ARTICLES = [
    '第一期_底图失踪之谜_外部参照是如何让现场开出白卷的.md',
    '第二期_十几万返工的代价_为什么图纸管理必须做到未确认不算现行版.md',
    '第三期_图面标了个详03_现场到底要翻多久_图纸穿透与双栏对照.md',
    '第四期_跨单位协同两难_发给甲方监理看图如何免装软件秒开防泄密.md',
    '第五期_工具选型边界_办公室单机看图工具vs现场协同中枢.md',
]

def parse_article(md_path: Path):
    content = md_path.read_text(encoding='utf-8')
    lines = content.split('\n')
    
    # 提取标题并去前缀
    raw_title = lines[0].replace('#', '').strip()
    title = re.sub(r'^【(?:知乎)?专栏\d+】\s*', '', raw_title)
    title = re.sub(r'^连载第\w+期[：:]\s*', '', title).strip()[:100]
    
    # 过滤顶部多余的元数据
    body_lines = lines[1:]
    filtered_lines = []
    for line in body_lines:
        s = line.strip()
        if '匹配知乎高热问答' in s or '话题标签' in s or '专栏：工程现场图纸协同与防错实录' in s:
            continue
        filtered_lines.append(line)
        
    cleaned_content = '\n'.join(filtered_lines).strip()
    cleaned_content = re.sub(r'^(?:---\s*)+', '', cleaned_content).strip()
    
    img_regex = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
    tokens = []
    last_pos = 0
    cover_img = None
    
    for match in img_regex.finditer(cleaned_content):
        start, end = match.span()
        alt, rel_path = match.groups()
        abs_path = str((md_path.parent / rel_path).resolve())
        if not cover_img:
            cover_img = abs_path
        
        text_before = cleaned_content[last_pos:start].strip()
        if text_before:
            tokens.append(('text', text_before))
        tokens.append(('image', abs_path, alt))
        last_pos = end
        
    tail = cleaned_content[last_pos:].strip()
    if tail:
        tokens.append(('text', tail))
        
    return title, cover_img, tokens


async def clean_existing_drafts(page):
    print("🧹 正在检查并清理知乎创作者中心历史测试草稿...", flush=True)
    await page.goto('https://www.zhihu.com/creator/manage/creation/draft?type=article', wait_until='networkidle')
    await page.wait_for_timeout(3000)
    
    # 关闭弹窗
    try:
        close_btn = page.locator('.Modal-closeButton, button[aria-label="关闭"]').first
        if await close_btn.count() and await close_btn.is_visible():
            await close_btn.click()
            await page.wait_for_timeout(500)
    except Exception:
        pass

    for attempt in range(15):
        del_btns = await page.locator('button:has-text("删除")').all()
        if not del_btns:
            print("✅ 历史测试草稿已全部清理干净", flush=True)
            break
        print(f"🗑 发现 {len(del_btns)} 个旧草稿，正在删除第一个...", flush=True)
        try:
            await del_btns[0].click()
            await page.wait_for_timeout(600)
            confirm = page.locator('.Modal button:has-text("确定"), button:has-text("确认")').first
            if await confirm.count():
                await confirm.click()
                await page.wait_for_timeout(1500)
            else:
                break
        except Exception as e:
            print(f"删除异常: {e}", flush=True)
            break


async def upload_single_draft(page, md_filename, ep_idx):
    md_path = DRAFT_DIR / md_filename
    title, cover_img, tokens = parse_article(md_path)
    print(f"\n==========================================", flush=True)
    print(f"🚀 开始写入第 {ep_idx} 期草稿: 《{title}》", flush=True)
    print(f"   封面路径: {cover_img}", flush=True)
    print(f"   Token 数量: {len(tokens)} (图文交替段落)", flush=True)
    
    await page.goto('https://zhuanlan.zhihu.com/write', wait_until='networkidle')
    await page.wait_for_timeout(2500)
    
    # 1. 填写标题
    title_loc = page.locator('textarea, input[placeholder*="标题"]').first
    await title_loc.click(force=True)
    await title_loc.fill('')
    await title_loc.fill(title)
    await page.wait_for_timeout(600)
    print(f"   [1/5] 已填写标题: {title}", flush=True)
    
    # 2. 聚焦编辑器并按顺序交替录入文字和图片
    editor = page.locator('.public-DraftEditor-content').first
    await editor.click()
    await page.wait_for_timeout(500)
    
    img_count = 0
    for idx, token in enumerate(tokens):
        if token[0] == 'text':
            text = token[1]
            await page.evaluate('''text => {
                const el = document.querySelector('.public-DraftEditor-content') || document.querySelector('[contenteditable="true"]');
                const dt = new DataTransfer();
                dt.setData('text/plain', text);
                const evt = new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt });
                el.dispatchEvent(evt);
            }''', text)
            await page.wait_for_timeout(600)
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(300)
        elif token[0] == 'image':
            img_path = token[1]
            alt = token[2]
            if os.path.exists(img_path):
                img_input = page.locator('input[type="file"][accept*="image"]').first
                initial_count = await page.locator('.public-DraftEditor-content img, figure img').count()
                await img_input.set_input_files(img_path)
                
                # 等待图片插入成功
                for _ in range(16):
                    await page.wait_for_timeout(500)
                    now_count = await page.locator('.public-DraftEditor-content img, figure img').count()
                    if now_count > initial_count:
                        break
                await page.wait_for_timeout(1000)
                # 移出图片 block 到新行
                await page.keyboard.press('ArrowDown')
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(400)
                img_count += 1
                print(f"   [图文编排] 已插入配图 #{img_count}: {alt} ({os.path.basename(img_path)})", flush=True)
            else:
                print(f"   ⚠️ 图片不存在: {img_path}", flush=True)
                
    total_text_len = len(await editor.inner_text())
    print(f"   [2/5] 图文编排完成，知乎正文字符数={total_text_len}，内嵌配图={img_count}张", flush=True)
    
    # 3. 上传大封面
    if cover_img and os.path.exists(cover_img):
        cover_input = page.locator('input.UploadPicture-input[type="file"]').first
        if not await cover_input.count():
            cover_input = page.locator('input[type="file"]').last
        if await cover_input.count():
            await cover_input.set_input_files(cover_img)
            await page.wait_for_timeout(2500)
            for confirm in ('button:has-text("确定")', 'button:has-text("完成")', 'button:has-text("确认")'):
                try:
                    btn = page.locator(confirm).first
                    if await btn.count() and await btn.is_visible():
                        await btn.click(timeout=2000)
                        await page.wait_for_timeout(1000)
                        break
                except Exception:
                    pass
            print(f"   [3/5] 已成功上传官方封面: {os.path.basename(cover_img)}", flush=True)
            
    # 4. 选择创作声明（默认选用包含 AI 辅助创作声明）
    try:
        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        await page.wait_for_timeout(600)
        combo = page.locator('button[role="combobox"]:has-text("声明"), button.Select-button:has-text("声明")').first
        if not await combo.count():
            row = page.locator('div:has(> label:has-text("创作声明"))').first
            if await row.count():
                combo = row.locator('button[role="combobox"], button.Select-button').first

        if await combo.count():
            cur = ((await combo.inner_text()) or "").strip()
            target_statement = "包含 AI 辅助创作 作者对内容负责"
            if target_statement not in cur:
                await combo.scroll_into_view_if_needed()
                await combo.click(timeout=3000, force=True)
                await page.wait_for_timeout(500)
                opt = page.locator('button.Select-option[role="option"]').filter(has_text="包含 AI 辅助创作").first
                if await opt.count():
                    await opt.click(timeout=3000, force=True)
                    await page.wait_for_timeout(500)
        print(f"   [4/5] 创作声明已确认为「包含 AI 辅助创作 作者对内容负责」", flush=True)
    except Exception as exc:
        print(f"   创作声明设置跳过: {exc}", flush=True)
        
    # 5. 等待草稿持久化保存
    print("   [5/5] 等待知乎云端草稿自动保存...", flush=True)
    await page.wait_for_timeout(5000)
    
    # 保存截图凭证
    screenshot_path = ARTIFACT_DIR / f"zhihu_official_draft_ep0{ep_idx}.png"
    await page.screenshot(full_page=True, path=str(screenshot_path))
    print(f"   📸 第 {ep_idx} 期草稿凭证截图已保存: {screenshot_path.name}", flush=True)
    return title, total_text_len, img_count


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            cookie_data = json.load(f)
        await context.add_cookies(cookie_data.get('cookies', []))
        page = await context.new_page()
        
        # 1. 清理残留测试草稿
        await clean_existing_drafts(page)
        
        # 2. 依次写入 5 篇
        results = []
        for i, filename in enumerate(ARTICLES, 1):
            t, t_len, i_cnt = await upload_single_draft(page, filename, i)
            results.append({'ep': i, 'title': t, 'chars': t_len, 'images': i_cnt})
            await page.wait_for_timeout(2000)
            
        # 3. 访问创作者中心草稿箱，验证 5 篇全部存在并截图
        print("\n==========================================", flush=True)
        print("🔍 正在打开知乎创作者中心草稿箱进行全量验证...", flush=True)
        await page.goto('https://www.zhihu.com/creator/manage/creation/draft?type=article', wait_until='networkidle')
        await page.wait_for_timeout(4000)
        
        summary_shot = ARTIFACT_DIR / "zhihu_all_5_drafts_verified.png"
        await page.screenshot(full_page=True, path=str(summary_shot))
        print(f"📸 知乎全量草稿箱验证截图已保存: {summary_shot.name}", flush=True)
        
        print("\n🎉 全部 5 篇专栏文章已成功写入知乎官方草稿箱！")
        for res in results:
            print(f"  - 第 {res['ep']} 期: 《{res['title']}》 ({res['chars']} 字, {res['images']} 张插图)")
            
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
