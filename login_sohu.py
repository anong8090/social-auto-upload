# -*- coding: utf-8 -*-
import asyncio
import os
import sqlite3
import uuid
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
COOKIES_DIR = BASE_DIR / 'omnipost' / 'cookiesFile'
COOKIE_FILE = COOKIES_DIR / 'sohu_account.json'
DB_PATH = BASE_DIR / 'omnipost' / 'db' / 'database.db'

async def login_sohu():
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        options = {
            'headless': False,
            'args': [
                '--disable-blink-features=AutomationControlled',
                '--lang=zh-CN',
                '--start-maximized',
            ],
        }
        chrome_app = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        if os.path.exists(chrome_app):
            options['executable_path'] = chrome_app

        print("🟢 正在启动 Chrome 浏览器打开搜狐号登录页...")
        browser = await p.chromium.launch(**options)
        context = await browser.new_context(
            viewport={'width': 1366, 'height': 850},
            locale='zh-CN',
        )
        page = await context.new_page()

        print("🟢 打开搜狐创作者登录页: https://mp.sohu.com/mpfe/v4/login")
        await page.goto("https://mp.sohu.com/mpfe/v4/login", timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        # 截取登录二维码页面供核实
        qr_shot = BASE_DIR / 'logs' / 'screenshots' / 'sohu_login_qr_screen.png'
        await page.screenshot(path=str(qr_shot))
        print(f"📸 登录二维码界面已就绪并截图: {qr_shot}")
        print("⏳ 请在打开的浏览器窗口中完成搜狐号登录（微信/手机号/搜狐扫码）...")

        # 循环检测登录状态（最长等待 10 分钟）
        logged_in = False
        for sec in range(600):
            await asyncio.sleep(1)
            current_url = page.url.lower()
            if "mp.sohu.com" in current_url and "/login" not in current_url and "passport.sohu.com" not in current_url:
                logged_in = True
                print(f"✅ 检测到 URL 跳转，登录成功！URL: {page.url}")
                break
            
            # 检测后台元素标记
            markers = ['.user-name', '.nick-name', 'text=内容管理', 'text=写文章', 'a[href*="addarticle"]']
            for m in markers:
                try:
                    if await page.locator(m).count():
                        logged_in = True
                        print(f"✅ 检测到创作者后台元素 [{m}]，登录成功！URL: {page.url}")
                        break
                except Exception:
                    pass
            if logged_in:
                break

            if sec > 0 and sec % 30 == 0:
                print(f"⏳ 等待登录中... 已过 {sec} 秒，当前 URL: {page.url}")

        if not logged_in:
            print("❌ 登录超时（600秒）")
            await browser.close()
            return False

        # 登录成功后等待3秒加载用户信息
        await page.wait_for_timeout(3000)

        # 保存 cookie 文件
        await context.storage_state(path=str(COOKIE_FILE))
        print(f"💾 Cookie 已保存至: {COOKIE_FILE}")

        # 获取创作者名称
        creator_name = "搜狐号创作者"
        for sel in ['.user-name', '.nick-name', '.user-info-name', 'span[class*="name"]', '.creator-name']:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    t = (await loc.inner_text()).strip()
                    if t:
                        creator_name = t
                        break
            except Exception:
                pass

        print(f"👤 创作者账号: {creator_name}")

        # 写入 omnipost 数据库
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT id FROM user_info WHERE type = 8")
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE user_info SET filePath = ?, userName = ?, status = 1 WHERE id = ?",
                    (COOKIE_FILE.name, creator_name, row[0])
                )
            else:
                cur.execute(
                    "INSERT INTO user_info (type, filePath, userName, status) VALUES (8, ?, ?, 1)",
                    (COOKIE_FILE.name, creator_name)
                )
            conn.commit()
            conn.close()
            print("✅ 账号信息已同步记录至 omnipost 数据库")
        except Exception as e:
            print(f"⚠️ 数据库写入提示: {e}")

        # 截取登录成功后台截图
        shot_path = BASE_DIR / 'logs' / 'screenshots' / 'sohu_logged_in_dashboard.png'
        await page.screenshot(path=str(shot_path))
        print(f"📸 登录成功截图: {shot_path}")

        await browser.close()

        # 登录成功后自动无缝触发 5 篇草稿批量存盘
        print("\n🎉 搜狐号登录认证成功！正在全自动批量保存 5 篇精炼图文草稿...")
        import subprocess
        subprocess.run(["./omnipost/.venv/bin/python", "save_all_5_sohu_drafts.py"])
        return True

if __name__ == '__main__':
    asyncio.run(login_sohu())
