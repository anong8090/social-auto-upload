# -*- coding: utf-8 -*-
"""阿里大鱼号文章发布与草稿管理（Playwright）。
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page, async_playwright

from conf import LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from utils.base_social_media import set_init_script
from utils.log import dayu_logger

DAYU_LOGIN_URL = "https://mp.dayu.com/"
DAYU_HOME_URL = "https://mp.dayu.com/dashboard"

TITLE_MAX_LEN = 30
COVER_MAX_BYTES = 10 * 1024 * 1024
COVER_EXTS = {".jpg", ".jpeg", ".png"}


def _build_launch_kwargs(headless: bool) -> dict:
    options = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--disable-infobars",
            "--start-maximized",
        ],
    }
    if LOCAL_CHROME_PATH:
        options["executable_path"] = LOCAL_CHROME_PATH
    return options


def _is_login_url(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in ("/login", "passport", "auth"))


async def _has_auth_cookie(context) -> bool:
    """大鱼号登录成功后通常会有 _dayu_s_ 或 uc_token / cna。"""
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    names = {c.get("name") for c in cookies}
    return bool(names & {"_dayu_s_", "uc_token", "login_token", "cna"})


async def _wait_until_logged_in(page: Page, context, timeout_ms: int = 200_000) -> bool:
    """等待人工扫码登录大鱼号。"""
    elapsed = 0
    step = 1000
    while elapsed < timeout_ms:
        cur_url = (page.url or "").lower()
        if "dashboard" in cur_url or "article" in cur_url:
            return True
        if await _has_auth_cookie(context) and not _is_login_url(cur_url):
            return True
        await page.wait_for_timeout(step)
        elapsed += step
    return await _has_auth_cookie(context)


async def cookie_auth(account_file) -> bool:
    """校验大鱼号 Cookie 是否有效。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS))
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(DAYU_HOME_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)

            cur_url = (page.url or "").lower()
            if _is_login_url(cur_url):
                dayu_logger.error("[+] 大鱼号 cookie 失效（已跳转至登录页）")
                return False

            if "dashboard" in cur_url or (await _has_auth_cookie(context) and not _is_login_url(cur_url)):
                dayu_logger.success("[+] 大鱼号 cookie 有效")
                return True

            markers = ['text=发布图文', 'text=创作中心', 'text=草稿箱', '.user-info']
            for sel in markers:
                try:
                    if await page.locator(sel).count():
                        dayu_logger.success("[+] 大鱼号 cookie 有效（后台管理元素就绪）")
                        return True
                except Exception:
                    continue

            dayu_logger.error("[+] 大鱼号 cookie 失效")
            return False
        except Exception as exc:
            dayu_logger.warning(f"[+] 大鱼号 cookie 校验异常: {exc}")
            return False
        finally:
            await browser.close()


async def dayu_cookie_gen(account_file):
    """CLI/本地打开浏览器扫码登录大鱼号。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto(DAYU_LOGIN_URL, timeout=60000)
        dayu_logger.info("请在打开的浏览器中完成大鱼号登录（扫码或密码）…")
        ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError("大鱼号登录超时：未检测到有效登录凭证，请重试")
        await page.wait_for_timeout(3000)
        Path(account_file).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=account_file)
        dayu_logger.success(f"大鱼号 cookie 保存成功: {account_file}")
        await context.close()
        await browser.close()


async def dayu_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        dayu_logger.error("cookie文件不存在或已失效，即将打开浏览器，请登录大鱼号")
        await dayu_cookie_gen(account_file)
    return True


class DayuPost(object):
    """阿里大鱼号文章发布与草稿管理。"""

    def __init__(
        self,
        title,
        body,
        tags,
        publish_date: datetime | int,
        account_file,
        dry_run=False,
        cover_path=None,
        is_draft=False,
        file_list=None,
    ):
        raw_title = (title or "").strip()
        self.title = re.sub(r"^【.*?】\s*", "", raw_title).strip()[:TITLE_MAX_LEN]
        self.body = (body or "").strip()
        self.tags = tags or []
        self.publish_date = publish_date
        self.account_file = str(account_file)
        self.dry_run = bool(dry_run)
        self.is_draft = bool(is_draft)
        self.cover_path = cover_path
        self.file_list = file_list or []
        self.headless = LOCAL_CHROME_HEADLESS if not self.dry_run else False

    async def main(self) -> None:
        async with async_playwright() as playwright:
            await self.upload(playwright)

    async def upload(self, playwright) -> None:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=self.headless))
        context = await browser.new_context(
            storage_state=self.account_file,
            viewport={"width": 1440, "height": 900},
        )
        context = await set_init_script(context)
        page = await context.new_page()
        try:
            dayu_logger.info("正在打开大鱼号控制台...")
            await page.goto(DAYU_HOME_URL, timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)

            # 点击「写文章」或「发布图文」
            create_btn = page.locator('text=发布图文, text=写图文, text=写文章, a[href*="create"]').first
            if await create_btn.count():
                await create_btn.click()
                await page.wait_for_timeout(4000)

            # 填充标题
            title_input = page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"]').first
            if await title_input.count():
                await title_input.click()
                await title_input.fill(self.title)
                dayu_logger.info(f"已填写大鱼号标题: {self.title} ({len(self.title)}字)")

            # 填充正文
            editor = page.locator('div[contenteditable="true"], .w-e-text, .public-DraftEditor-content').first
            if await editor.count():
                await editor.click()
                await page.wait_for_timeout(500)
                escaped = self.body.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
                await page.evaluate(f"""() => {{
                    const el = document.querySelector('div[contenteditable="true"]') || document.querySelector('.w-e-text');
                    if (el) {{
                        const ps = `{escaped}`.split('\\n');
                        el.innerHTML = ps.map(p => p.trim() ? `<p>${{p}}</p>` : '<p><br></p>').join('');
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}""")
                dayu_logger.info(f"已注入大鱼号正文，长度={len(self.body)}")

            await page.wait_for_timeout(2000)

            # 截图保存预览
            preview_shot = Path(self.account_file).parent / "dayu_article_preview.png"
            await page.screenshot(path=str(preview_shot))
            dayu_logger.info(f"📸 大鱼号发布预览截图已保存: {preview_shot}")

            if self.dry_run:
                dayu_logger.info("🛑 【预览模式】跳过保存与发布")
                return

            # 操作按钮
            action_btn = None
            if self.is_draft:
                action_btn = page.locator('button:has-text("存草稿"), button:has-text("保存草稿")').first
            else:
                action_btn = page.locator('button:has-text("发布"), button:has-text("确认发布")').first

            if action_btn and await action_btn.count() and await action_btn.is_visible():
                await action_btn.click()
                dayu_logger.info("已点击大鱼号操作按钮")
                await page.wait_for_timeout(4000)
                done_shot = Path(self.account_file).parent / "dayu_article_done.png"
                await page.screenshot(path=str(done_shot))
                dayu_logger.success(f"🎉 大鱼号操作完成！截图: {done_shot}")
            else:
                dayu_logger.warning("未定位到操作按钮")

        finally:
            await browser.close()
