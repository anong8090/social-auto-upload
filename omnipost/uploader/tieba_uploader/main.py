# -*- coding: utf-8 -*-
"""百度贴吧帖子发布（Playwright）。第一期仅支持图文帖，且严禁自动点击「发布」——仅保存至贴吧暂存箱/草稿。

用户可通过前端指定目标贴吧（fid 贴吧 ID 或 kw 贴吧名），发帖流程：
1. 打开贴吧登录页，手动扫码/密码登录后保存 cookie；
2. 打开目标贴吧发表页，填写标题与正文；
3. 点击「暂存/存草稿」而非「发布」，避免违反「严禁自动发布」底线。
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
from utils.log import tieba_logger


TIEBA_LOGIN_URL = "https://passport.baidu.com/v2/?login"
TIEBA_HOME_URL = "https://tieba.baidu.com/"
TIEBA_POST_URL = "https://tieba.baidu.com/f/commit/post"

TITLE_MAX_LEN = 100
COVER_MAX_BYTES = 10 * 1024 * 1024
COVER_EXTS = {".jpg", ".jpeg", ".png", ".gif"}

# 贴吧关键步骤重试配置（应对超时/元素不可见等瞬时异常）
TIEBA_RETRY_ATTEMPTS = 3
TIEBA_RETRY_BASE_DELAY = 1.0  # 秒，指数退避


async def _retry_when_transient(fn, *, label: str, attempts: int = TIEBA_RETRY_ATTEMPTS, base_delay: float = TIEBA_RETRY_BASE_DELAY):
    """对超时、元素不可见等瞬时异常进行指数退避重试；非瞬时异常直接抛出。"""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            is_transient = (
                "timeout" in msg
                or "target page, context or browser has been closed" in msg
                or "not visible" in msg
                or "hidden" in msg
                or "element is not" in msg
                or "navigation" in msg
            )
            if attempt < attempts and is_transient:
                tieba_logger.warning(
                    f"{label} 第 {attempt} 次尝试失败（瞬时异常，{base_delay * attempt}s 后重试）: {exc}"
                )
                await asyncio.sleep(base_delay * attempt)
                continue
            tieba_logger.error(f"{label} 失败: {exc}")
            raise
    raise last_exc


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
    return any(k in u for k in ("passport.baidu.com", "/login", "passport", "account/login"))


async def _has_auth_cookie(context) -> bool:
    """贴吧登录成功后通常会有 BDUSS / STOKEN 等凭证。"""
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    names = {c.get("name") for c in cookies}
    return bool(names & {"BDUSS", "STOKEN", "BAIDUID"})


async def _wait_until_logged_in(page: Page, context, timeout_ms: int = 200_000) -> bool:
    """等待人工登录完成（优先检测 BDUSS cookie）。"""
    elapsed = 0
    step = 1000
    while elapsed < timeout_ms:
        if await _has_auth_cookie(context):
            return True
        if (not _is_login_url(page.url)) and ("tieba.baidu.com" in (page.url or "")):
            await page.wait_for_timeout(500)
            if await _has_auth_cookie(context):
                return True
        await page.wait_for_timeout(step)
        elapsed += step
    return await _has_auth_cookie(context)


async def cookie_auth(account_file) -> bool:
    """校验贴吧 cookie 是否仍有效。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS))
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(TIEBA_HOME_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            if _is_login_url(page.url):
                tieba_logger.error("[+] 百度贴吧 cookie 失效")
                return False
            if await _has_auth_cookie(context):
                tieba_logger.success("[+] 百度贴吧 cookie 有效")
                return True
            if "tieba.baidu.com" in page.url and not _is_login_url(page.url):
                tieba_logger.success("[+] 百度贴吧 cookie 有效（域名已登录）")
                return True
            tieba_logger.error("[+] 百度贴吧 cookie 失效")
            return False
        except Exception as exc:
            tieba_logger.warning(f"[+] cookie 校验异常，按失效处理: {exc}")
            return False
        finally:
            await browser.close()


async def tieba_cookie_gen(account_file):
    """CLI/本地调试：打开贴吧登录页，手动登录后保存 cookie。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto(TIEBA_LOGIN_URL, timeout=60000)
        tieba_logger.info("请在浏览器中完成百度贴吧登录；检测到 BDUSS 等登录凭证后会自动保存…")
        ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError("百度贴吧登录超时：未检测到登录凭证 BDUSS，请重新登录")
        Path(account_file).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=account_file)
        tieba_logger.success(f"cookie saved: {account_file}")
        await context.close()
        await browser.close()


async def tieba_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        tieba_logger.error("cookie文件不存在或已失效，即将打开浏览器，请手动登录百度贴吧")
        await tieba_cookie_gen(account_file)
    return True


class TiebaPost(object):
    """百度贴吧帖子发布（仅存草稿/暂存，不自动发布）。"""

    def __init__(
        self,
        title,
        body,
        tags,
        publish_date: datetime | int,
        account_file,
        fid=None,
        kw=None,
        dry_run=False,
        cover_path=None,
        is_draft=False,
    ):
        import re as _re

        raw_title = (title or "").strip()
        self.title = _re.sub(r"\s+", " ", raw_title)[:TITLE_MAX_LEN]
        if not self.title:
            raise ValueError("百度贴吧帖子标题不能为空")
        self.body = (body or "").strip()
        if not self.body:
            raise ValueError("百度贴吧帖子正文不能为空")
        self.tags = tags or []
        self.publish_date = publish_date
        self.account_file = account_file
        self.dry_run = bool(dry_run)
        self.is_draft = bool(is_draft)
        self.headless = LOCAL_CHROME_HEADLESS if not self.dry_run else False
        self.cover_path = self._normalize_cover(cover_path)
        # 用户指定的目标贴吧：优先 fid（贴吧 ID），其次 kw（贴吧名）
        self.fid = str(fid).strip() if fid else None
        self.kw = str(kw).strip() if kw else None
        if not self.fid and not self.kw:
            raise ValueError("百度贴吧发帖必须指定 fid（贴吧 ID）或 kw（贴吧名）")

    @staticmethod
    def _normalize_cover(cover_path) -> str | None:
        if not cover_path or not str(cover_path).strip():
            return None
        p = Path(str(cover_path).strip())
        if not p.is_file():
            tieba_logger.warning(f"封面不存在，将跳过: {p}")
            return None
        if p.suffix.lower() not in COVER_EXTS:
            tieba_logger.warning(f"封面格式不支持（需 jpg/jpeg/png/gif），将跳过: {p}")
            return None
        if p.stat().st_size > COVER_MAX_BYTES:
            tieba_logger.warning(f"封面超过 10MB，将跳过: {p}")
            return None
        return str(p.resolve())

    async def upload(self, playwright) -> None:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=self.headless))
        context = await browser.new_context(storage_state=str(self.account_file))
        context = await set_init_script(context)
        page = await context.new_page()
        try:
            # 进入目标贴吧发表页（含超时/元素不可见重试）
            await _retry_when_transient(
                lambda: self.goto_post_page(page),
                label="进入贴吧发表页",
            )
            # 检查登录态
            if _is_login_url(page.url):
                raise RuntimeError("百度贴吧 cookie 已失效，请重新登录")
            # 填写标题与正文（含瞬时异常重试）
            await _retry_when_transient(
                lambda: self.fill_title(page),
                label="填写帖子标题",
            )
            await _retry_when_transient(
                lambda: self.fill_body(page),
                label="填写帖子正文",
            )
            if self.cover_path:
                await _retry_when_transient(
                    lambda: self.upload_cover(page),
                    label="上传帖子封面",
                )
            # 安全底线：绝不自动点击「发布」。填完内容后等待用户手动操作。
            await self.save_draft(page)
            tieba_logger.info(
                f"✅ 百度贴吧帖子内容已自动填写完毕（标题={self.title}, fid={self.fid}, kw={self.kw})。"
                " 请在浏览器中手动点击「发布」或关闭页面，脚本绝不自动发布。"
            )
            try:
                preview = Path(self.account_file).with_name("tieba_post_draft_saved.png")
                await page.screenshot(full_page=True, path=str(preview))
                tieba_logger.info(f"📸 草稿凭证截图已保存: {preview}")
            except Exception as exc:
                tieba_logger.debug(f"草稿截图保存失败: {exc}")
        finally:
            await context.close()
            await browser.close()

    async def goto_post_page(self, page: Page) -> None:
        """进入目标贴吧并打开发表组件。

        直接访问 /f/commit/post?fid=... 会返回 404，因此先进入贴吧论坛页，
        关闭可能的登录弹窗，再点击「发贴」按钮弹出发表组件。
        优先 fid，若无则按 kw 检索贴吧。
        """
        if self.fid:
            url = f"https://tieba.baidu.com/f?kw={self.fid}"
        else:
            url = f"https://tieba.baidu.com/f?kw={self.kw}"
        tieba_logger.info(f"进入贴吧: {url}")
        await page.goto(url, timeout=90000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # 关闭登录弹窗（若有）
        try:
            close_btn = page.locator('.close-btn').first
            if await close_btn.count() and await close_btn.is_visible():
                await close_btn.click()
                tieba_logger.info("已关闭贴吧登录弹窗")
                await page.wait_for_timeout(2000)
        except Exception:
            pass

        # 点击「发贴」按钮弹出发表组件
        for sel in [
            '.button-wrapper.button-wrapper--gray.operate-btn.publish',
            'div:has-text("发贴").button-wrapper',
        ]:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(force=True)
                    await page.wait_for_timeout(5000)
                    tieba_logger.info("已点击「发贴」，发表组件已弹出")
                    return
            except Exception:
                continue
        tieba_logger.warning("未找到「发贴」按钮，发表组件可能未弹出")

    async def fill_title(self, page: Page) -> None:
        """百度贴吧标题为 contenteditable 编辑器（#tb-editor-title .ql-editor）。"""
        candidates = [
            page.locator('#tb-editor-title .ql-editor').first,
            page.locator('.post-title-wrapper .ql-editor').first,
            page.locator('input[placeholder*="标题"]').first,
            page.locator('input[placeholder*="请输入标题"]').first,
            page.locator('input[name="title"]').first,
        ]
        title_field = None
        for loc in candidates:
            try:
                if await loc.count() and await loc.is_visible():
                    title_field = loc
                    break
            except Exception:
                continue
        if not title_field:
            raise RuntimeError("未找到百度贴吧帖子标题输入框")
        await title_field.click(force=True)
        await page.wait_for_timeout(300)
        # contenteditable 不支持 fill，改用 evaluate 写入 innerHTML 并触发 input 事件
        try:
            await page.evaluate(
                """(text) => {
                    const el = document.querySelector('#tb-editor-title .ql-editor') || document.querySelector('.post-title-wrapper .ql-editor');
                    if (!el) return false;
                    el.focus();
                    el.innerHTML = text;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    return true;
                }""",
                self.title,
            )
        except Exception as exc:
            tieba_logger.debug(f"标题 evaluate 写入失败，回退 fill: {exc}")
            await title_field.fill(self.title)
        await page.wait_for_timeout(500)
        tieba_logger.info(f"已填写帖子标题: {self.title}")

    async def fill_body(self, page: Page) -> None:
        """百度贴吧正文为 Quill 编辑器（#tb-editor-content .ql-editor，contenteditable）。"""
        candidates = [
            page.locator('#tb-editor-content .ql-editor').first,
            page.locator('.post-editor-content .ql-editor').first,
            page.locator('.ueditor-container iframe').first,
            page.locator('.editor-container iframe').first,
            page.locator('iframe[class*="ueditor"]').first,
            page.locator('textarea[placeholder*="内容"]').first,
            page.locator('textarea[placeholder*="请输入"]').first,
            page.locator('div[contenteditable="true"]').first,
        ]
        editor = None
        editor_type = None
        for loc in candidates:
            try:
                if await loc.count() and await loc.is_visible():
                    editor = loc
                    tag = await loc.evaluate('el => el.tagName')
                    cls = await loc.get_attribute('class') or ''
                    if tag == 'IFRAME' or 'ueditor' in cls.lower():
                        editor_type = 'iframe'
                    else:
                        editor_type = 'contenteditable'
                    break
            except Exception:
                continue
        if not editor:
            raise RuntimeError("未找到百度贴吧帖子正文编辑器")

        if editor_type == 'iframe':
            try:
                frame = editor.content_frame()
                if frame:
                    body_el = frame.locator('body[contenteditable="true"], textarea').first
                    await body_el.click(force=True)
                    await page.evaluate(
                        """(text) => {
                            const el = document.querySelector('body[contenteditable="true"]') || document.querySelector('textarea');
                            if (!el) return false;
                            el.focus();
                            el.innerHTML = text;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            return true;
                        }""",
                        self.body,
                    )
                    tieba_logger.info(f"已填写正文（ueditor iframe），长度={len(self.body)}")
                    return
            except Exception:
                pass

        # contenteditable Quill 编辑器
        await editor.click(force=True)
        await page.wait_for_timeout(300)
        try:
            await page.evaluate(
                """(text) => {
                    const el = document.querySelector('#tb-editor-content .ql-editor') || document.querySelector('.post-editor-content .ql-editor') || document.querySelector('div[contenteditable="true"]') || document.querySelector('textarea');
                    if (!el) return false;
                    el.focus();
                    el.innerHTML = text;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    return true;
                }""",
                self.body,
            )
        except Exception as exc:
            tieba_logger.debug(f"正文 evaluate 写入失败: {exc}")
        await page.wait_for_timeout(1000)
        tieba_logger.info(f"已填写正文，长度={len(self.body)}")

    async def upload_cover(self, page: Page) -> None:
        if not self.cover_path or not os.path.exists(self.cover_path):
            tieba_logger.warning("封面文件未指定或不存在，跳过")
            return
        cover_input = page.locator('input[type="file"]').first
        if not await cover_input.count():
            tieba_logger.warning("未找到封面文件选择框，跳过封面")
            return
        try:
            await cover_input.set_input_files(self.cover_path)
            await page.wait_for_timeout(2500)
            tieba_logger.info(f"已上传封面: {self.cover_path}")
        except Exception as e:
            tieba_logger.warning(f"上传封面失败: {e}")

    async def save_draft(self, page: Page) -> None:
        """贴吧内容填写完毕后等待用户手动操作。

        安全底线：百度贴吧发表页目前仅露出「发布」按钮，不存在
        「暂存/存草稿」按钮。为遵守「严禁自动点击发布」底线，
        本方法绝不点击「发布」，而是填完内容后保存截图并提示
        用户手动点击「发布」或「暂存」，由用户自行决定是否发布。
        """
        try:
            preview = Path(self.account_file).with_name("tieba_post_draft_pending.png")
            await page.screenshot(full_page=True, path=str(preview))
            tieba_logger.info(f"📸 贴吧发帖凭证截图已保存: {preview}")
        except Exception as exc:
            tieba_logger.debug(f"草稿截图保存失败: {exc}")

        tieba_logger.warning(
            "⚠️ 百度贴吧发表页无「暂存/存草稿」按钮，仅露出「发布」按钮。"
            " 内容（标题/正文/封面）已自动填写完毕，请在浏览器中自行决定："
            " 手动点击「发布」发表，或关闭页面放弃。本脚本绝不自动点击「发布」。"
        )

    async def main(self):
        async with async_playwright() as playwright:
            await self.upload(playwright)
