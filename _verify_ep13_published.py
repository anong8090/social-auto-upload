# -*- coding: utf-8 -*-
"""核验第 13 期在各平台是否真的上线（无头，不弹窗）。"""
import asyncio, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE)); sys.path.insert(0, str(BASE / 'omnipost'))
from playwright.async_api import async_playwright
from utils.base_social_media import set_init_script
from account_lookup import get_platform_account

SHOT = BASE / 'logs' / 'screenshots'
ZHIHU_URL = 'https://zhuanlan.zhihu.com/p/2100908174710784689'
KEY = '设计变更'


async def check_zhihu(page):
    """知乎文章页对无头/匿名访问一律 40362，改从创作中心「内容管理」核验。"""
    await page.goto('https://www.zhihu.com/creator/manage/creation/all', timeout=60000,
                    wait_until='domcontentloaded')
    await page.wait_for_timeout(8000)
    txt = await page.evaluate("() => document.body.innerText || ''")
    hit = KEY in txt
    print('[知乎] 内容管理页命中本期标题:', hit)
    for l in [x.strip() for x in txt.splitlines() if x.strip()][:14]:
        print('  ', l[:70])
    await page.screenshot(path=str(SHOT / 'verify_zhihu_ep13.png'), full_page=True)

    if hit:
        # 尝试点进文章详情，取正文长度
        link = page.locator(f'a:has-text("{KEY}")').first
        if await link.count():
            try:
                await link.click(timeout=8000)
                await page.wait_for_timeout(6000)
                data = await page.evaluate("""() => {
                    const t = document.querySelector('h1')?.innerText || '';
                    const body = document.querySelector('.Post-RichText, article, .RichText')?.innerText || '';
                    const imgs = document.querySelectorAll('.Post-RichText img, article img, .RichText img').length;
                    return {title: t, len: body.length, head: body.slice(0, 40), imgs};
                }""")
                print(f'  详情页 H1: {data["title"]}')
                print(f'  正文长度: {data["len"]} | 首句: {data["head"]!r} | 图片: {data["imgs"]}')
            except Exception as e:
                print(f'  详情页打开失败: {e}')
    return hit


async def check_baijiahao(page, ctx):
    await page.goto('https://baijiahao.baidu.com/builder/rc/content?currentPage=1&pageSize=10&search=&type=news',
                    timeout=60000)
    await page.wait_for_timeout(6000)
    rows = await page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('div,li,tr').forEach(el => {
            const t = (el.innerText || '');
            if (t.includes('设计变更一来') && t.length < 200) out.push(t.replace(/\\n+/g,' | ').slice(0,120));
        });
        return out.slice(0, 4);
    }""")
    print('[百家号] 命中条目:')
    for r in rows:
        print('  ', r)
    await page.screenshot(path=str(SHOT / 'verify_bjh_ep13.png'), full_page=True)
    return any('审核' in r or '已发布' in r for r in rows)


async def check_sohu(page):
    await page.goto('https://mp.sohu.com/mpfe/v3/main/content/list', timeout=60000)
    await page.wait_for_timeout(6000)
    rows = await page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('div,li,tr').forEach(el => {
            const t = (el.innerText || '');
            if (t.includes('设计变更一来') && t.length < 200) out.push(t.replace(/\\n+/g,' | ').slice(0,120));
        });
        return out.slice(0, 4);
    }""")
    print('[搜狐号] 命中条目:')
    for r in rows:
        print('  ', r)
    await page.screenshot(path=str(SHOT / 'verify_sohu_ep13.png'), full_page=True)
    return any('审核' in r or '已发布' in r for r in rows)


async def check_toutiao(page, ctx):
    await page.goto('https://mp.toutiao.com/profile_v4/graphic/articles', timeout=60000)
    await page.wait_for_timeout(7000)
    txt = await page.evaluate("(document.body.innerText||'').slice(0,400)")
    print('[头条] 列表首段:')
    for l in [x.strip() for x in txt.splitlines() if x.strip()][:6]:
        print('  ', l[:70])
    return '设计变更一来' in txt


async def check_xhs(ctx):
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto('https://creator.xiaohongshu.com/new/note-manager', timeout=60000)
    await page.wait_for_timeout(6000)
    txt = await page.evaluate("(document.body.innerText||'').slice(0,600)")
    print('[小红书] 笔记管理:')
    for l in [x.strip() for x in txt.splitlines() if x.strip()][:12]:
        print('  ', l[:60])
    ok = '设计变更来了' in txt
    await page.screenshot(path=str(SHOT / 'verify_xhs_ep13.png'), full_page=True)
    return ok


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    results = {}

    async with async_playwright() as p:
        if which in ('all', 'zhihu'):
            cookie, _ = get_platform_account(9)
            b = await p.chromium.launch(headless=True, channel='chrome',
                                        args=['--disable-blink-features=AutomationControlled', '--lang=zh-CN'])
            c = await b.new_context(storage_state=str(cookie), viewport={'width': 1280, 'height': 900},
                                    locale='zh-CN')
            pg = await c.new_page()
            results['知乎'] = await check_zhihu(pg)
            await b.close()

        if which in ('all', 'baijiahao'):
            cookie, _ = get_platform_account(5)
            b = await p.chromium.launch(headless=True, channel='chrome')
            c = await b.new_context(storage_state=str(cookie), viewport={'width': 1440, 'height': 900})
            pg = await c.new_page()
            results['百家号'] = await check_baijiahao(pg, c)
            await b.close()

        if which in ('all', 'sohu'):
            cookie, _ = get_platform_account(8)
            b = await p.chromium.launch(headless=True, channel='chrome')
            c = await b.new_context(storage_state=str(cookie), viewport={'width': 1440, 'height': 900})
            pg = await c.new_page()
            results['搜狐号'] = await check_sohu(pg)
            await b.close()

        if which in ('all', 'toutiao'):
            cookie, _ = get_platform_account(7)
            b = await p.chromium.launch(headless=True, channel='chrome')
            c = await b.new_context(storage_state=str(cookie), viewport={'width': 1440, 'height': 900})
            c = await set_init_script(c)
            pg = await c.new_page()
            results['今日头条'] = await check_toutiao(pg, c)
            await b.close()

        if which in ('all', 'xhs'):
            from patchright.async_api import async_playwright as pw2
            async with pw2() as p2:
                c = await p2.chromium.launch_persistent_context(
                    user_data_dir=str(BASE / 'omnipost' / 'browser_profiles' / 'xiaohongshu'),
                    headless=True, channel='chrome', viewport={'width': 1440, 'height': 900})
                c = await set_init_script(c)
                results['小红书'] = await check_xhs(c)
                await c.close()

    print('\n=== 汇总 ===')
    for k, v in results.items():
        print(f'  {k}: {"✅ 已上线" if v else "⚠️ 未确认"}')


asyncio.run(main())
