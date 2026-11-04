# -*- coding: utf-8 -*-
"""企鹅号（腾讯内容开放平台）文章发布与草稿管理（Playwright）。
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
from utils.log import qiehao_logger

QIEHAO_LOGIN_URL = "https://om.qq.com/userAuth/index"
QIEHAO_HOME_URL = "https://om.qq.com/main"
QIEHAO_PUBLISH_URL = "https://om.qq.com/article/articlePublish"

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
    return any(k in u for k in ("/userauth", "/login", "passport", "ptlogin2"))


async def _has_auth_cookie(context) -> bool:
    """企鹅号登录成功后通常会有 om_token 或 uin / skey。"""
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    names = {c.get("name") for c in cookies}
    return bool(names & {"om_token", "uin", "skey", "p_skey", "pt4_token"})


async def _wait_until_logged_in(page: Page, context, timeout_ms: int = 200_000) -> bool:
    """等待人工微信或QQ扫码登录企鹅号。"""
    elapsed = 0
    step = 1000
    while elapsed < timeout_ms:
        cur_url = (page.url or "").lower()
        if "main" in cur_url or "article" in cur_url:
            return True
        if await _has_auth_cookie(context) and not _is_login_url(cur_url):
            return True
        await page.wait_for_timeout(step)
        elapsed += step
    return await _has_auth_cookie(context)


async def cookie_auth(account_file) -> bool:
    """校验企鹅号 Cookie 是否有效。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS))
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(QIEHAO_HOME_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)

            cur_url = (page.url or "").lower()
            if _is_login_url(cur_url):
                qiehao_logger.error("[+] 企鹅号 cookie 失效（已跳转至登录页）")
                return False

            if "main" in cur_url or (await _has_auth_cookie(context) and not _is_login_url(cur_url)):
                qiehao_logger.success("[+] 企鹅号 cookie 有效")
                return True

            markers = ['text=发文章', 'text=图文', 'text=内容管理', '.user-info', 'a[href*="article"]']
            for sel in markers:
                try:
                    if await page.locator(sel).count():
                        qiehao_logger.success("[+] 企鹅号 cookie 有效（后台管理元素就绪）")
                        return True
                except Exception:
                    continue

            qiehao_logger.error("[+] 企鹅号 cookie 失效")
            return False
        except Exception as exc:
            qiehao_logger.warning(f"[+] 企鹅号 cookie 校验异常: {exc}")
            return False
        finally:
            await browser.close()


async def qiehao_cookie_gen(account_file):
    """CLI/本地打开浏览器扫码登录企鹅号。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto(QIEHAO_LOGIN_URL, timeout=60000)
        qiehao_logger.info("请在打开的浏览器中完成企鹅号扫码登录（微信/QQ）…")
        ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError("企鹅号登录超时：未检测到有效登录凭证，请重试")
        await page.wait_for_timeout(3000)
        Path(account_file).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=account_file)
        qiehao_logger.success(f"企鹅号 cookie 保存成功: {account_file}")
        await context.close()
        await browser.close()


async def qiehao_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        qiehao_logger.error("cookie文件不存在或已失效，即将打开浏览器，请登录企鹅号")
        await qiehao_cookie_gen(account_file)
    return True


class QiehaoPost(object):
    """企鹅号文章发布与草稿管理。"""

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
            qiehao_logger.info("正在打开企鹅号发文页...")
            await page.goto(QIEHAO_PUBLISH_URL, timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)

            if _is_login_url(page.url):
                await page.goto(QIEHAO_HOME_URL, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                pub_btn = page.locator('text=发文章, text=图文, a[href*="articlePublish"]').first
                if await pub_btn.count():
                    await pub_btn.click()
                    await page.wait_for_timeout(4000)

            # 填充标题
            title_input = page.locator('input[placeholder*="标题"], textarea[placeholder*="标题"]').first
            if await title_input.count():
                await title_input.click()
                await title_input.fill(self.title)
                qiehao_logger.info(f"已填写企鹅号标题: {self.title} ({len(self.title)}字)")

            # 填充正文
            editor = page.locator('div[contenteditable="true"], .w-e-text, .ql-editor').first
            if await editor.count():
                await editor.click()
                await page.wait_for_timeout(500)
                escaped = self.body.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
                await page.evaluate(f"""() => {{
                    const el = document.querySelector('div[contenteditable="true"]') || document.querySelector('.ql-editor');
                    if (el) {{
                        const ps = `{escaped}`.split('\\n');
                        el.innerHTML = ps.map(p => p.trim() ? `<p>${{p}}</p>` : '<p><br></p>').join('');
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}""")
                qiehao_logger.info(f"已注入企鹅号正文，长度={len(self.body)}")

            await page.wait_for_timeout(2000)

            # 截图保存预览
            preview_shot = Path(self.account_file).parent / "qiehao_article_preview.png"
            await page.screenshot(path=str(preview_shot))
            qiehao_logger.info(f"📸 企鹅号发布预览截图已保存: {preview_shot}")

            if self.dry_run:
                qiehao_logger.info("🛑 【预览模式】跳过保存与发布")
                return

            # 操作按钮
            action_btn = None
            if self.is_draft:
                action_btn = page.locator('button:has-text("存草稿"), button:has-text("保存草稿")').first
            else:
                action_btn = page.locator('button:has-text("发布"), button:has-text("发表")').first

            if action_btn and await action_btn.count() and await action_btn.is_visible():
                await action_btn.click()
                qiehao_logger.info("已点击企鹅号操作按钮")
                await page.wait_for_timeout(4000)
                done_shot = Path(self.account_file).parent / "qiehao_article_done.png"
                await page.screenshot(path=str(done_shot))
                qiehao_logger.success(f"🎉 企鹅号操作完成！截图: {done_shot}")
            else:
                qiehao_logger.warning("未定位到操作按钮")

        finally:
            await browser.close()
