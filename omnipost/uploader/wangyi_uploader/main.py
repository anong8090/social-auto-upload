# -*- coding: utf-8 -*-
"""网易号文章草稿与发布管理（Playwright）。
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
from utils.log import wangyi_logger

WANGYI_LOGIN_URL = "https://mp.163.com/login.html"
WANGYI_HOME_URL = "https://mp.163.com/"
WANGYI_PUBLISH_URL = "https://mp.163.com/article/publish.html"

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
    return any(k in u for k in ("/login", "passport", "login.html"))


async def _has_auth_cookie(context) -> bool:
    """网易号登录成功后通常会有 NTES_SESS 或 P_INFO。"""
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    names = {c.get("name") for c in cookies}
    return bool(names & {"NTES_SESS", "P_INFO", "S_INFO", "URS_TOKEN"})


async def _wait_until_logged_in(page: Page, context, timeout_ms: int = 200_000) -> bool:
    """等待人工扫码或密码登录网易号。"""
    elapsed = 0
    step = 1000
    while elapsed < timeout_ms:
        if await _has_auth_cookie(context):
            return True
        cur_url = (page.url or "").lower()
        if (not _is_login_url(cur_url)) and ("mp.163.com" in cur_url):
            await page.wait_for_timeout(500)
            if await _has_auth_cookie(context):
                return True
        await page.wait_for_timeout(step)
        elapsed += step
    return await _has_auth_cookie(context)


async def cookie_auth(account_file) -> bool:
    """校验网易号 Cookie 是否有效。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS))
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(WANGYI_HOME_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)

            cur_url = (page.url or "").lower()
            if _is_login_url(cur_url):
                wangyi_logger.error("[+] 网易号 cookie 失效（已跳转至登录页）")
                return False

            if await _has_auth_cookie(context) and not _is_login_url(cur_url):
                wangyi_logger.success("[+] 网易号 cookie 有效")
                return True

            markers = ['text=发文章', 'text=内容管理', 'text=草稿箱', '.user-name', 'a[href*="publish"]']
            for sel in markers:
                try:
                    if await page.locator(sel).count():
                        wangyi_logger.success("[+] 网易号 cookie 有效（后台管理元素就绪）")
                        return True
                except Exception:
                    continue

            wangyi_logger.error("[+] 网易号 cookie 失效")
            return False
        except Exception as exc:
            wangyi_logger.warning(f"[+] 网易号 cookie 校验异常: {exc}")
            return False
        finally:
            await browser.close()


async def wangyi_cookie_gen(account_file):
    """CLI/本地打开浏览器登录并保存网易号 cookie。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto(WANGYI_LOGIN_URL, timeout=60000)
        wangyi_logger.info("请在打开的浏览器中完成网易号登录（网易新闻APP扫码/手机验证码）…")
        ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError("网易号登录超时：未检测到有效登录凭证，请重试")
        await page.wait_for_timeout(3000)
        Path(account_file).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=account_file)
        wangyi_logger.success(f"网易号 cookie 保存成功: {account_file}")
        await context.close()
        await browser.close()


async def wangyi_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        wangyi_logger.error("cookie文件不存在或已失效，即将打开浏览器，请登录网易号")
        await wangyi_cookie_gen(account_file)
    return True


class WangyiPost(object):
    """网易号文章发布与草稿管理。"""

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
            wangyi_logger.info("正在打开网易号文章发文页...")
            await page.goto(WANGYI_PUBLISH_URL, timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)

            if _is_login_url(page.url):
                # 尝试从主页跳转
                await page.goto(WANGYI_HOME_URL, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                pub_btn = page.locator('text=发文章, a[href*="publish"]').first
                if await pub_btn.count():
                    await pub_btn.click()
                    await page.wait_for_timeout(4000)

            # 填充标题
            title_input = page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"]').first
            if await title_input.count():
                await title_input.click()
                await title_input.fill(self.title)
                wangyi_logger.info(f"已填写网易号标题: {self.title} ({len(self.title)}字)")

            # 填充正文
            editor = page.locator('div[contenteditable="true"], .w-e-text, .ne-editor').first
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
                wangyi_logger.info(f"已注入网易号正文，长度={len(self.body)}")

            await page.wait_for_timeout(2000)

            # 截图保存预览
            preview_shot = Path(self.account_file).parent / "wangyi_article_preview.png"
            await page.screenshot(path=str(preview_shot))
            wangyi_logger.info(f"📸 网易号发布预览截图已保存: {preview_shot}")

            if self.dry_run:
                wangyi_logger.info("🛑 【预览模式】跳过保存与发布")
                return

            # 草稿 vs 发布
            action_btn = None
            if self.is_draft:
                action_btn = page.locator('button:has-text("存草稿"), button:has-text("保存草稿"), a:has-text("存草稿")').first
            else:
                action_btn = page.locator('button:has-text("发布"), button:has-text("发表"), a:has-text("发布")').first

            if action_btn and await action_btn.count() and await action_btn.is_visible():
                await action_btn.click()
                wangyi_logger.info("已点击操作按钮")
                await page.wait_for_timeout(4000)
                done_shot = Path(self.account_file).parent / "wangyi_article_done.png"
                await page.screenshot(path=str(done_shot))
                wangyi_logger.success(f"🎉 网易号操作完成！截图: {done_shot}")
            else:
                wangyi_logger.warning("未定位到操作按钮")

        finally:
            await browser.close()
