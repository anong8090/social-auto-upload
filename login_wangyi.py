# -*- coding: utf-8 -*-
"""网易号账号登录工具（Playwright）。
打开本地 Chrome 浏览器，用户扫码登录网易号后，
自动将 Cookie 保存至 omnipost/cookiesFile/wangyi_account.json，并同步记录至数据库。
"""
import asyncio
import os
import sqlite3
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
COOKIES_DIR = BASE_DIR / 'omnipost' / 'cookiesFile'
COOKIE_FILE = COOKIES_DIR / 'wangyi_account.json'
DB_PATH = BASE_DIR / 'omnipost' / 'db' / 'database.db'


async def login_wangyi():
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

        print("🟢 正在启动 Chrome 浏览器打开网易号登录页...")
        browser = await p.chromium.launch(**options)
        context = await browser.new_context(
            viewport={'width': 1366, 'height': 850},
            locale='zh-CN',
        )
        page = await context.new_page()

        print("🟢 打开网易号登录页: https://mp.163.com/login.html")
        await page.goto("https://mp.163.com/login.html", timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        qr_dir = BASE_DIR / 'logs' / 'screenshots'
        qr_dir.mkdir(parents=True, exist_ok=True)
        qr_shot = qr_dir / 'wangyi_login_qr_screen.png'
        await page.screenshot(path=str(qr_shot))
        print(f"📸 登录二维码界面已就绪并截图: {qr_shot}")
        print("⏳ 请在打开的浏览器窗口中完成网易号扫码登录（网易新闻APP）或密码登录...")

        logged_in = False
        for sec in range(600):
            await asyncio.sleep(1)
            cur_url = page.url.lower()
            if "mp.163.com" in cur_url and "login" not in cur_url:
                logged_in = True
                print(f"✅ 检测到登录成功后台跳转！URL: {page.url}")
                break

            try:
                cookies = await context.cookies()
                if any(c.get("name") in ("NTES_SESS", "P_INFO", "URS_TOKEN") for c in cookies):
                    logged_in = True
                    print(f"✅ 检测到网易号登录凭证 Cookie，登录成功！URL: {page.url}")
                    break
            except Exception:
                pass

            if sec > 0 and sec % 30 == 0:
                print(f"⏳ 等待登录中... 已过 {sec} 秒，当前 URL: {page.url}")

        if not logged_in:
            print("❌ 登录超时（600秒）")
            await browser.close()
            return False

        await page.wait_for_timeout(3000)
        await context.storage_state(path=str(COOKIE_FILE))
        print(f"💾 Cookie 已保存至: {COOKIE_FILE}")

        creator_name = "网易号创作者"
        for sel in ['.user-name', '.nick-name', 'span[class*="name"]', '.account-info']:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    t = (await loc.inner_text()).strip()
                    if t and len(t) < 30:
                        creator_name = t
                        break
            except Exception:
                pass

        print(f"👤 创作者名称: {creator_name}")

        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT id FROM user_info WHERE type = 13")
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE user_info SET filePath = ?, userName = ?, status = 1 WHERE id = ?",
                    (COOKIE_FILE.name, creator_name, row[0])
                )
            else:
                cur.execute(
                    "INSERT INTO user_info (type, filePath, userName, status) VALUES (13, ?, ?, 1)",
                    (COOKIE_FILE.name, creator_name)
                )
            conn.commit()
            conn.close()
            print("✅ 网易号账号信息已同步记录至 omnipost 数据库 (type=13)")
        except Exception as e:
            print(f"⚠️ 数据库写入提示: {e}")

        shot_path = qr_dir / 'wangyi_logged_in_dashboard.png'
        await page.screenshot(path=str(shot_path))
        print(f"📸 登录成功截图: {shot_path}")

        await browser.close()
        print("\n🎉 网易号登录认证完成！")
        return True


if __name__ == '__main__':
    asyncio.run(login_wangyi())
