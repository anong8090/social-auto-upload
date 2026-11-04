# -*- coding: utf-8 -*-
"""微博内容发布与凭证管理（Playwright）。
支持图文动态发布与草稿预览。
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
from utils.log import weibo_logger

WEIBO_LOGIN_URL = "https://weibo.com/login.php"
WEIBO_HOME_URL = "https://weibo.com/"
WEIBO_CREATOR_URL = "https://creator.weibo.com/"

TITLE_MAX_LEN = 100
COVER_MAX_BYTES = 20 * 1024 * 1024
COVER_EXTS = {".jpg", ".jpeg", ".png", ".gif"}


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
    return any(k in u for k in ("/login", "passport", "account/login", "visitor", "newlogin", "openlogin"))


async def _has_auth_cookie(context) -> bool:
    """微博真正登录成功后，除了 SUB，还会存在 ALF 或 SSOLoginState 等登录会话凭证。"""
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    names = {c.get("name") for c in cookies}
    return bool(names & {"SSOLoginState", "ALF", "unick", "wvr", "WBStorage"})


async def _wait_until_logged_in(page: Page, context, timeout_ms: int = 200_000) -> bool:
    """等待人工登录完成。"""
    elapsed = 0
    step = 1000
    while elapsed < timeout_ms:
        cur_url = (page.url or "").lower()
        if (not _is_login_url(cur_url)) and ("weibo.com" in cur_url):
            if await _has_auth_cookie(context):
                return True
            try:
                if await page.locator('[placeholder*="新鲜事"], .woo-avatar, [class*="avatar"]').count() > 0:
                    return True
            except Exception:
                pass
        await page.wait_for_timeout(step)
        elapsed += step
    return await _has_auth_cookie(context)


async def cookie_auth(account_file) -> bool:
    """校验微博 Cookie 是否有效。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS))
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(WEIBO_HOME_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)

            cur_url = (page.url or "").lower()
            if _is_login_url(cur_url):
                weibo_logger.error("[+] 微博 cookie 已失效（已跳转至登录页）")
                return False

            if await _has_auth_cookie(context):
                weibo_logger.success("[+] 微博 cookie 有效（检测到登录会话凭证）")
                return True

            # 检测主页发布框或用户头像标记
            markers = [
                'textarea',
                'div[contenteditable="true"]',
                'text=有什么新鲜事想告诉大家',
                '[placeholder*="新鲜事"]',
                '.woo-box-flex',
            ]
            for sel in markers:
                try:
                    if await page.locator(sel).count():
                        weibo_logger.success("[+] 微博 cookie 有效")
                        return True
                except Exception:
                    continue

            weibo_logger.error("[+] 微博 cookie 失效")
            return False
        except Exception as exc:
            weibo_logger.warning(f"[+] 微博 cookie 校验异常: {exc}")
            return False
        finally:
            await browser.close()


async def weibo_cookie_gen(account_file):
    """CLI/本地打开浏览器登录并保存微博 cookie。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto(WEIBO_LOGIN_URL, timeout=60000)
        weibo_logger.info("请在打开的浏览器中完成微博扫码登录；检测到 SUB 登录凭证后会自动保存…")
        ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError("微博登录超时：未检测到登录凭证 SUB，请重新登录")
        Path(account_file).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=account_file)
        weibo_logger.success(f"微博 cookie 保存成功: {account_file}")
        await context.close()
        await browser.close()


async def weibo_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        weibo_logger.error("cookie文件不存在或已失效，即将打开浏览器，请手动登录微博")
        await weibo_cookie_gen(account_file)
    return True


class WeiboPost(object):
    """微博图文动态发布。"""

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
        self.headless = LOCAL_CHROME_HEADLESS if not self.dry_run else False
        self.post_url = ""

        # 收集图片文件
        self.images: list[str] = []
        if cover_path and str(cover_path).strip():
            cp = Path(str(cover_path).strip())
            if cp.is_file() and cp.suffix.lower() in COVER_EXTS:
                self.images.append(str(cp.resolve()))
        if file_list:
            for f in file_list:
                fp = Path(str(f).strip())
                if fp.is_file() and fp.suffix.lower() in COVER_EXTS:
                    resolved = str(fp.resolve())
                    if resolved not in self.images:
                        self.images.append(resolved)
        # 微博单条最多支持 9 张（长图文最多 18 张）
        self.images = self.images[:9]

    def _compose_content(self) -> str:
        parts = []
        if self.title:
            parts.append(f"【{self.title}】")
        if self.body:
            parts.append(self.body)
        if self.tags:
            tag_str = " ".join(f"#{t.strip('#')}#" for t in self.tags if t.strip())
            if tag_str:
                parts.append(tag_str)
        return "\n\n".join(parts)

    async def main(self) -> str:
        async with async_playwright() as playwright:
            return await self.upload(playwright)

    async def upload(self, playwright) -> str:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=self.headless))
        context = await browser.new_context(
            storage_state=self.account_file,
            viewport={"width": 1440, "height": 900},
        )
        context = await set_init_script(context)
        page = await context.new_page()
        try:
            weibo_logger.info("正在打开微博主页...")
            await page.goto(WEIBO_HOME_URL, timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)

            if _is_login_url(page.url):
                raise RuntimeError("微博 Cookie 已失效，请重新登录")

            content = self._compose_content()
            weibo_logger.info(f"准备注入微博正文，总字符数: {len(content)}")

            # 激活输入框
            editor = None
            selectors = [
                'textarea[placeholder*="新鲜事"]',
                'div[contenteditable="true"]',
                'textarea.Form_input_',
                'textarea',
                '[placeholder*="新鲜事"]',
            ]
            for sel in selectors:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    editor = loc
                    break

            if not editor:
                # 尝试点击新鲜事占位符
                placeholder = page.locator('text=有什么新鲜事想告诉大家').first
                if await placeholder.count():
                    await placeholder.click()
                    await page.wait_for_timeout(1000)
                    editor = page.locator('div[contenteditable="true"], textarea').first

            if not editor:
                raise RuntimeError("未找到微博发布输入框")

            await editor.click()
            await page.wait_for_timeout(500)

            # 填充文本（通过 fill 或 evaluate）
            try:
                await editor.fill(content)
            except Exception:
                await page.evaluate("""([sel, text]) => {
                    const el = document.querySelector(sel);
                    if (el) {
                        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
                            el.value = text;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                        } else {
                            el.innerText = text;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                        }
                    }
                }""", [selectors[0], content])

            weibo_logger.info("微博正文注入完毕")
            await page.wait_for_timeout(1000)

            # 上传图片配图
            if self.images:
                weibo_logger.info(f"正在上传配图: {len(self.images)} 张")
                file_input = page.locator('input[type="file"][accept*="image"], input[type="file"]').first
                if await file_input.count():
                    await file_input.set_input_files(self.images)
                    await page.wait_for_timeout(4000)
                    weibo_logger.info("配图已上传，等待渲染完成")
                else:
                    weibo_logger.warning("未找到图片上传 input[type=file]")

            # 截图记录
            preview_shot = Path(self.account_file).parent / "weibo_post_preview.png"
            await page.screenshot(path=str(preview_shot))
            weibo_logger.info(f"📸 微博发布预览截图已保存: {preview_shot}")

            if self.dry_run or self.is_draft:
                weibo_logger.info("🛑 【预览/存草稿模式】已完成内容注入并截图，跳过点击发送")
                return ""

            # 点击发送
            publish_btn = None
            btn_selectors = [
                'button:has-text("发送")',
                'button:has-text("发布")',
                'div[title="发布"]',
                'button.woo-button-primary',
                'a:has-text("发送")',
            ]
            for b_sel in btn_selectors:
                btn = page.locator(b_sel).first
                if await btn.count() and await btn.is_visible():
                    publish_btn = btn
                    break

            if publish_btn:
                await publish_btn.click()
                weibo_logger.info("已点击微博「发送/发布」按钮")
                await page.wait_for_timeout(6000)

                # 确保 logs/screenshots 目录存在
                logs_dir = Path("logs/screenshots")
                logs_dir.mkdir(parents=True, exist_ok=True)
                published_screenshot = logs_dir / "weibo_post_published.png"
                await page.screenshot(path=str(published_screenshot))

                done_shot = Path(self.account_file).parent / "weibo_post_published.png"
                await page.screenshot(path=str(done_shot))
                weibo_logger.success(f"🎉 微博发布成功！截图: {published_screenshot}")

                # 尝试抓取发布的微博真实公开链接
                try:
                    time_link = page.locator('a:has-text("刚刚"), a:has-text("秒前")').first
                    if await time_link.count():
                        href = await time_link.get_attribute("href")
                        if href:
                            self.post_url = href if href.startswith("http") else f"https://weibo.com{href}"
                    
                    if not self.post_url:
                        detail_links = page.locator('a[href*="/detail/"], a[href*="/status/"]')
                        if await detail_links.count():
                            href = await detail_links.first.get_attribute("href")
                            if href:
                                self.post_url = href if href.startswith("http") else f"https://weibo.com{href}"

                    if not self.post_url:
                        user_avatar_link = page.locator('a.woo-avatar[href^="/u/"], a._alink_1z046_65[href^="/u/"]').first
                        if await user_avatar_link.count():
                            u_href = await user_avatar_link.get_attribute("href")
                            if u_href:
                                profile_url = f"https://weibo.com{u_href}" if not u_href.startswith("http") else u_href
                                await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                                await page.wait_for_timeout(3000)
                                first_post = page.locator('a[href*="/detail/"], a:has-text("刚刚"), a:has-text("秒前")').first
                                if await first_post.count():
                                    f_href = await first_post.get_attribute("href")
                                    if f_href:
                                        self.post_url = f_href if f_href.startswith("http") else f"https://weibo.com{f_href}"
                    
                    if self.post_url:
                        weibo_logger.success(f"🔗 微博公开访问链接: {self.post_url}")
                except Exception as e_url:
                    weibo_logger.warning(f"获取微博链接异常: {e_url}")
            else:
                weibo_logger.warning("未定位到「发送」按钮，可能处于禁用状态或内容超限")

        finally:
            await browser.close()
        return self.post_url
