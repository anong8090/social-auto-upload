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
# 后台真实入口是 /dashboard/index（旧版 /dashboard 已 302 到 404 页）
DAYU_HOME_URL = "https://mp.dayu.com/dashboard/index"
DAYU_WRITE_URL = "https://mp.dayu.com/dashboard/article/write"

TITLE_MAX_LEN = 30
COVER_MAX_BYTES = 10 * 1024 * 1024
COVER_EXTS = {".jpg", ".jpeg", ".png"}

# 大鱼号正文编辑器：多个候选，按出现顺序取第一个命中的。
EDITOR_SELECTOR = (
    'div[contenteditable="true"][data-placeholder*="正文"], '
    'div.ProseMirror[contenteditable="true"], '
    '.public-DraftEditor-content[contenteditable="true"], '
    'div[contenteditable="true"]'
)
# 封面回读：大鱼号封面区常见结构，若干候选依次尝试。
COVER_IMAGE_SELECTOR = (
    '.cover-container img, [class*=cover] img, [class*=Cover] img, '
    '[class*=cover] [class*=thumb] img, [class*=coverImg], [class*=cover-img]'
)


def _is_public_dayu_url(url: str) -> bool:
    """只认大鱼号公开详情页；写稿页 / 预览页 / 后台路由不算公开链接。"""
    u = (url or "").strip()
    if not u:
        return False
    if re.search(r"/(write|edit|preview|create|new|draft)(/|\?|$)", u, re.IGNORECASE):
        return False
    return bool(
        re.match(r"^https?://mp\.dayu\.com/dashboard/article/detail/[A-Za-z0-9_-]+", u)
        or re.match(r"^https?://mp\.dayu\.com/content/[A-Za-z0-9_-]+", u)
        or re.match(r"^https?://mp\.dayu\.com/article/[A-Za-z0-9_-]+", u)
    )


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
    """大鱼号登录成功后通常会有 _dayu_s_ 或 uc_token / cna。

    注意：cna 是阿里通用 cookie，未登录的访客也会带，
    因此不能单独作为登录判据，必须同时进入后台路由才算有效。
    """
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    names = {c.get("name") for c in cookies}
    return bool(names & {"_dayu_s_", "uc_token", "login_token"})


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
        prepare_only=False,
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
        # prepare_only：只填内容并等待人工点「发布」
        self.prepare_only = bool(prepare_only)
        self.headless = LOCAL_CHROME_HEADLESS if not self.dry_run else False
        if self.prepare_only:
            self.headless = False
        self.published_url = ""

    async def main(self) -> str:
        async with async_playwright() as playwright:
            await self.upload(playwright)
        return self.published_url

    # ---------------------------------------------------------------- 填写

    async def fill_fields(self, page: Page) -> None:
        """把标题、正文、封面、内文图填进大鱼号发文页。"""
        # 标题
        title_input = page.locator(
            'input[placeholder*="标题"], textarea[placeholder*="标题"], '
            '[contenteditable="true"][data-placeholder*="标题"]'
        ).first
        if not await title_input.count():
            raise RuntimeError("未找到大鱼号标题输入框")
        await title_input.click(force=True)
        await page.wait_for_timeout(300)
        tag = await title_input.evaluate("el => el.tagName")
        if tag == "INPUT":
            await title_input.fill(self.title)
        else:
            await page.keyboard.insert_text(self.title)
        await page.wait_for_timeout(600)
        actual = (await title_input.inner_text() or "") if tag != "INPUT" else (await title_input.input_value() or "")
        if self.title not in actual:
            raise RuntimeError(f"大鱼号标题写入校验失败：期望含「{self.title}」，实际「{actual[:40]}」")
        dayu_logger.info(f"已填写大鱼号标题: {self.title} ({len(self.title)}字)")

        # 正文
        editor = page.locator(EDITOR_SELECTOR).first
        if not await editor.count():
            raise RuntimeError("未找到大鱼号正文编辑器")
        await editor.click(force=True)
        await page.wait_for_timeout(400)
        for idx, line in enumerate(self.body.splitlines()):
            if idx:
                await page.keyboard.press("Enter")
            if line.strip():
                await page.keyboard.insert_text(line)
        await page.wait_for_timeout(900)
        actual = (await editor.inner_text() or "")
        probe = next((ln.strip() for ln in self.body.splitlines() if ln.strip()), "")
        if probe and probe[:12] not in actual:
            raise RuntimeError(
                f"大鱼号正文写入校验失败：期望含「{probe[:12]}」，实际长度={len(actual)}"
            )
        dayu_logger.info(f"已填写大鱼号正文，长度={len(self.body)}，编辑器实际={len(actual)}")

        # 封面：优先找封面区域的图片上传入口
        if not await self._try_cover(page):
            raise RuntimeError("大鱼号封面上传未通过回读校验，禁止带病发布")

        # 内文图
        for img in self.file_list:
            if not img:
                continue
            ok = await self._try_inline_image(page, img)
            if ok:
                dayu_logger.success(f"已插入大鱼号内文图: {img}")
            else:
                raise RuntimeError(f"大鱼号内文图插入失败，禁止带病发布: {img}")

    async def _try_cover(self, page: Page) -> bool:
        """大鱼号封面：找图片上传 input（隐藏 input 需配合按钮触发）。

        上传后必须回读封面 `<img>` 的 src 是否变化；找不到封面图元素时
        只做「已上传」告警而不谎报成功，避免带病发布。
        """
        if not self.cover_path:
            dayu_logger.warning("大鱼号未提供封面")
            return False
        old_src = await self._cover_src(page)
        inputs = page.locator('input[type="file"][accept*="image"], input[type="file"][accept*="imgs"]')
        count = await inputs.count()
        for idx in range(count):
            try:
                await inputs.nth(idx).set_input_files(self.cover_path)
                await page.wait_for_timeout(4000)
                if await self._cover_changed(page, old_src):
                    dayu_logger.success(f"已上传大鱼号封面: {self.cover_path}")
                    return True
            except Exception:
                continue
        # 没有直接可用的 input，尝试点「上传封面」类按钮
        for sel in ('text=上传封面', 'text=设置封面', '[class*=cover] [class*=upload]'):
            btn = page.locator(sel).first
            if await btn.count():
                try:
                    await btn.click(force=True)
                    await page.wait_for_timeout(2000)
                    fi = page.locator('input[type="file"][accept*="image"]').first
                    if await fi.count():
                        await fi.set_input_files(self.cover_path)
                        await page.wait_for_timeout(4000)
                        if await self._cover_changed(page, old_src):
                            dayu_logger.success(f"已上传大鱼号封面: {self.cover_path}")
                            return True
                except Exception:
                    continue
        dayu_logger.warning("大鱼号封面未找到可用上传入口或回读未变化，需人工处理")
        return False

    async def _cover_src(self, page: Page) -> str:
        """回读封面区域当前图片 src（取不到时返回空串）。"""
        for selector in COVER_IMAGE_SELECTOR.split(", "):
            try:
                img = page.locator(selector).first
                if await img.count():
                    return (await img.get_attribute("src")) or ""
            except Exception:
                continue
        return ""

    async def _cover_changed(self, page: Page, old_src: str) -> bool:
        """封面 src 是否发生变化；页面没有封面元素时按「无法确认」处理。"""
        new_src = await self._cover_src(page)
        if not new_src:
            return False
        return new_src != old_src

    async def _try_inline_image(self, page: Page, image_path: str) -> bool:
        """内文图：优先直接用隐藏 file input 灌入，并用编辑器 `<img>` 数量回读确认。

        只看到「已选择文件」不等于图已插进正文；上传后必须确认正文图片数量增加，
        否则按插入失败处理，交由调用方 fail-closed。
        """
        editor = page.locator(EDITOR_SELECTOR).first
        before = await editor.locator("img").count() if await editor.count() else 0
        inputs = page.locator('input[type="file"][accept*="image"], input[type="file"][accept*="imgs"]')
        count = await inputs.count()
        for idx in range(count):
            try:
                await inputs.nth(idx).set_input_files(image_path)
                await page.wait_for_timeout(3500)
                after = await editor.locator("img").count() if await editor.count() else 0
                if after > before:
                    return True
            except Exception:
                continue
        dayu_logger.warning(f"大鱼号内文图未真正插入编辑器（img {before} 未增加）: {image_path}")
        return False

    async def wait_publish(self, page: Page, timeout_s: int = 900) -> str:
        """等待人工点「发布」，然后从内容管理页取公开链接。"""
        dayu_logger.info("⏳ 等待人工点击「发布」…")
        elapsed = 0
        while elapsed < timeout_s:
            await page.wait_for_timeout(5000)
            elapsed += 5
            try:
                url = (page.url or "").lower()
                body = (await page.evaluate("() => document.body.innerText") or "")
            except Exception:
                continue
            if "发布成功" in body or "已发布" in body or "content" in url and "write" not in url:
                dayu_logger.success("检测到大鱼号发布完成，开始回收公开链接")
                break
        else:
            dayu_logger.warning(f"等待人工发布超时（{timeout_s} 秒）")
            return ""

        try:
            await page.goto(
                "https://mp.dayu.com/dashboard/article/list",
                timeout=90000,
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(6000)
            hrefs = await page.evaluate(
                r"""(title) => {
                    const norm = s => (s||'').replace(/[\s　，。！？、；：“”‘’（）()\[\]【】《》—\-·….,!?;:'"#]+/g,'');
                    const key = norm(title);
                    const out = [];
                    document.querySelectorAll('a').forEach(a => {
                        const href = a.getAttribute('href') || '';
                        if(!/article|content|detail/i.test(href)) return;
                        const box = a.closest('li,tr,[class*=item],[class*=card],[class*=row]') || a;
                        if(norm(box.innerText||'').includes(key)) out.push(href);
                    });
                    return out.slice(0,5);
                }""",
                self.title,
            )
            for href in hrefs or []:
                if href.startswith("//"):
                    href = "https:" + href
                if href.startswith("/"):
                    href = "https://mp.dayu.com" + href
                if href.startswith("http"):
                    candidate = href.split("?", 1)[0]
                    if not _is_public_dayu_url(candidate):
                        continue
                    self.published_url = candidate
                    dayu_logger.success(f"✅ 大鱼号公开链接: {self.published_url}")
                    return self.published_url
        except Exception as exc:
            dayu_logger.warning(f"大鱼号公开链接采集异常: {exc}")
        dayu_logger.warning("大鱼号内容管理页未取到公开链接")
        return ""

    async def upload(self, playwright) -> None:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=self.headless))
        context = await browser.new_context(
            storage_state=self.account_file,
            viewport={"width": 1440, "height": 900},
        )
        context = await set_init_script(context)
        page = await context.new_page()
        try:
            dayu_logger.info("正在打开大鱼号发文页...")
            await page.goto(DAYU_WRITE_URL, timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            if _is_login_url(page.url):
                raise RuntimeError(f"大鱼号 cookie 已失效，跳转登录页: {page.url}")

            # 统一走 fill_fields：标题/正文/封面/内文图全部带回读校验，
            # 旧版 upload() 里的 innerHTML 注入没有校验，会静默失败。
            await self.fill_fields(page)

            await page.wait_for_timeout(2000)

            # 截图保存预览
            preview_shot = Path(self.account_file).parent / "dayu_article_preview.png"
            await page.screenshot(path=str(preview_shot))
            dayu_logger.info(f"📸 大鱼号发布预览截图已保存: {preview_shot}")

            if self.dry_run:
                dayu_logger.info("🛑 【预览模式】跳过保存与发布")
                return

            # 代填模式：填好后交人工点「发布」，脚本只等待并回收公开链接
            if self.prepare_only:
                dayu_logger.info(
                    "⏸️ 【代填模式】内容已全部填写完毕，请在浏览器窗口点击「发布」"
                    "（本进程会保持窗口并自动回收公开链接）"
                )
                await self.wait_publish(page)
                return

            # 操作按钮
            action_btn = None
            if self.is_draft:
                action_btn = page.locator('button:has-text("存草稿"), button:has-text("保存草稿")').first
            else:
                action_btn = page.locator('button:has-text("发布"), button:has-text("确认发布")').first

            if action_btn and await action_btn.count() and await action_btn.is_visible():
                await action_btn.click(force=True)
                dayu_logger.info("已点击大鱼号操作按钮")
                await page.wait_for_timeout(4000)
                done_shot = Path(self.account_file).parent / "dayu_article_done.png"
                await page.screenshot(path=str(done_shot))
                dayu_logger.success(f"🎉 大鱼号操作完成！截图: {done_shot}")
                self.published_url = await self.wait_publish(page, timeout_s=60)
            else:
                dayu_logger.warning("未定位到大鱼号操作按钮")

        finally:
            # 代填模式下窗口要留给人工点发布，不能由脚本关闭
            if not self.prepare_only:
                await browser.close()
