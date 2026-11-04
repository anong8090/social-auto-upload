# -*- coding: utf-8 -*-
"""微信公众平台图文草稿与发布管理（Playwright）。
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, async_playwright

from conf import LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from utils.base_social_media import set_init_script
from utils.log import wechat_mp_logger

WECHAT_MP_LOGIN_URL = "https://mp.weixin.qq.com/"
WECHAT_MP_HOME_URL = "https://mp.weixin.qq.com/"

TITLE_MAX_LEN = 64
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
    return "token=" not in u and any(k in u for k in ("/login", "action=login", "mp.weixin.qq.com/$"))


async def _has_auth_cookie(context) -> bool:
    """公众号登录成功后通常会有 slave 或 data_ticket / data_bizuin。"""
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    names = {c.get("name") for c in cookies}
    return bool(names & {"slave", "data_ticket", "bizuin", "data_bizuin"})


async def _wait_until_logged_in(page: Page, context, timeout_ms: int = 200_000) -> bool:
    """等待用户使用微信扫码授权完成。"""
    elapsed = 0
    step = 1000
    while elapsed < timeout_ms:
        cur_url = page.url or ""
        if "token=" in cur_url or "cgi-bin/home" in cur_url:
            return True
        if await _has_auth_cookie(context):
            return True
        await page.wait_for_timeout(step)
        elapsed += step
    return "token=" in (page.url or "") or await _has_auth_cookie(context)


def _extract_token_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        tokens = qs.get("token")
        if tokens:
            return tokens[0]
    except Exception:
        pass
    return None


async def cookie_auth(account_file) -> bool:
    """校验微信公众号 Cookie 是否仍然有效。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS))
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(WECHAT_MP_HOME_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)

            cur_url = page.url or ""
            if "token=" in cur_url or "cgi-bin/home" in cur_url:
                wechat_mp_logger.success("[+] 微信公众号 cookie 有效（成功进入后台）")
                return True

            if await _has_auth_cookie(context) and not _is_login_url(cur_url):
                wechat_mp_logger.success("[+] 微信公众号 cookie 有效")
                return True

            # 检测后台元素标记
            markers = ['.weui-desktop-layout__main', 'text=草稿箱', 'text=新的创作', 'text=图文消息']
            for sel in markers:
                try:
                    if await page.locator(sel).count():
                        wechat_mp_logger.success("[+] 微信公众号 cookie 有效（检测到管理后台元素）")
                        return True
                except Exception:
                    continue

            wechat_mp_logger.error("[+] 微信公众号 cookie 失效")
            return False
        except Exception as exc:
            wechat_mp_logger.warning(f"[+] 微信公众号 cookie 校验异常: {exc}")
            return False
        finally:
            await browser.close()


async def wechat_mp_cookie_gen(account_file):
    """CLI/本地打开浏览器扫码登录并保存微信公众号 cookie。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto(WECHAT_MP_LOGIN_URL, timeout=60000)
        wechat_mp_logger.info("请使用微信扫描屏幕中的公众号登录二维码并手机端确认…")
        ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError("微信公众号登录超时：未检测到扫码确认，请重试")
        await page.wait_for_timeout(3000)
        Path(account_file).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=account_file)
        wechat_mp_logger.success(f"微信公众号 cookie 保存成功: {account_file}")
        await context.close()
        await browser.close()


async def wechat_mp_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        wechat_mp_logger.error("cookie文件不存在或已失效，即将打开浏览器，请扫码登录微信公众号")
        await wechat_mp_cookie_gen(account_file)
    return True


class WechatMpPost(object):
    """微信公众平台图文草稿保存与发布。"""

    def __init__(
        self,
        title,
        body,
        tags,
        publish_date: datetime | int,
        account_file,
        dry_run=False,
        cover_path=None,
        is_draft=True,  # 公众号默认存为官方草稿箱
        author="",
    ):
        raw_title = (title or "").strip()
        self.title = re.sub(r"^【.*?】\s*", "", raw_title).strip()[:TITLE_MAX_LEN]
        self.body = (body or "").strip()
        self.tags = tags or []
        self.author = author or "河狸云"
        self.publish_date = publish_date
        self.account_file = str(account_file)
        self.dry_run = bool(dry_run)
        self.is_draft = bool(is_draft)
        self.cover_path = cover_path
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
            wechat_mp_logger.info("正在打开微信公众平台主页...")
            await page.goto(WECHAT_MP_HOME_URL, timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)

            token = _extract_token_from_url(page.url)
            if not token:
                # 尝试从主页链接或当前页面获取 token
                token_el = page.locator('a[href*="token="]').first
                if await token_el.count():
                    href = await token_el.get_attribute("href") or ""
                    token = _extract_token_from_url(href)

            if token:
                edit_url = f"https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit&isNew=1&type=77&createType=0&token={token}&lang=zh_CN"
                wechat_mp_logger.info(f"直接前往图文编辑页: {edit_url}")
                await page.goto(edit_url, timeout=60000, wait_until="domcontentloaded")
            else:
                # 点击「新的创作」或「图文消息」
                wechat_mp_logger.info("通过管理后台导航进入图文编辑页...")
                create_btn = page.locator('text=新的创作, a:has-text("新的创作"), text=写新图文').first
                if await create_btn.count():
                    async with page.expect_popup() as popup_info:
                        await create_btn.click()
                    page = await popup_info.value
                else:
                    wechat_mp_logger.warning("未找到快速创作按钮，尝试点击草稿箱")
                    await page.click('text=草稿箱')
                    await page.wait_for_timeout(2000)
                    new_draft_btn = page.locator('button:has-text("写新图文"), a:has-text("写新图文")').first
                    if await new_draft_btn.count():
                        async with page.expect_popup() as popup_info:
                            await new_draft_btn.click()
                        page = await popup_info.value

            await page.wait_for_timeout(4000)

            # 填充标题
            title_input = page.locator('#title, input[placeholder*="标题"], textarea[placeholder*="标题"]').first
            if await title_input.count():
                await title_input.click()
                await title_input.fill(self.title)
                wechat_mp_logger.info(f"已填写文章标题: {self.title}")

            # 填充作者
            author_input = page.locator('#author, input[placeholder*="作者"]').first
            if await author_input.count():
                await author_input.fill(self.author)

            # 填充正文
            editor = page.locator('#js_editor_area, div[contenteditable="true"], .edui-editor-body').first
            if await editor.count():
                await editor.click()
                await page.wait_for_timeout(500)
                # 使用 evaluate 写入富文本或纯文本换行
                escaped_body = self.body.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
                await page.evaluate(f"""() => {{
                    const ed = document.querySelector('#js_editor_area') || document.querySelector('div[contenteditable="true"]');
                    if (ed) {{
                        const paragraphs = `{escaped_body}`.split('\\n');
                        ed.innerHTML = paragraphs.map(p => p.trim() ? `<p>${{p}}</p>` : '<p><br></p>').join('');
                        ed.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}""")
                wechat_mp_logger.info(f"正文注入完成，长度={len(self.body)}")

            await page.wait_for_timeout(2000)

            # 截图记录
            preview_shot = Path(self.account_file).parent / "wechat_mp_draft_preview.png"
            await page.screenshot(path=str(preview_shot))
            wechat_mp_logger.info(f"📸 公众号图文预览截图已保存: {preview_shot}")

            if self.dry_run:
                wechat_mp_logger.info("🛑 【预览模式】跳过保存与发布")
                return

            # 点击「保存为草稿」
            save_draft_btn = page.locator('button:has-text("保存为草稿"), #js_submit, a:has-text("保存为草稿")').first
            if await save_draft_btn.count():
                await save_draft_btn.click()
                wechat_mp_logger.info("已点击「保存为草稿」")
                await page.wait_for_timeout(4000)
                done_shot = Path(self.account_file).parent / "wechat_mp_draft_saved.png"
                await page.screenshot(path=str(done_shot))
                wechat_mp_logger.success(f"🎉 微信公众号已存入官方草稿箱！截图: {done_shot}")
            else:
                wechat_mp_logger.warning("未定位到「保存为草稿」按钮")

        finally:
            await browser.close()
