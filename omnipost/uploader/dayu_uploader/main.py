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
# 内容管理页真实路径（左侧「我的作品」/「我的草稿」）。
# 旧代码用的 /dashboard/article/list 会跳 404 页，取链接必然失败。
DAYU_CONTENT_LIST_URL = "https://mp.dayu.com/dashboard/contents"
DAYU_DRAFT_LIST_URL = "https://mp.dayu.com/dashboard/draft"

TITLE_MAX_LEN = 30
COVER_MAX_BYTES = 10 * 1024 * 1024
COVER_EXTS = {".jpg", ".jpeg", ".png"}

# 大鱼号正文编辑器：UEditor，真实可编辑区在 iframe 内（body[contenteditable=true]）。
# 顶层页面只有 .edui-editor / .edui-editor-iframeholder 外壳，直接对顶层定位会永远找不到。
EDITOR_SELECTOR = (
    'div[contenteditable="true"][data-placeholder*="正文"], '
    'div.ProseMirror[contenteditable="true"], '
    '.public-DraftEditor-content[contenteditable="true"], '
    'div[contenteditable="true"]'
)
# iframe 内的真实可编辑体是 <body contenteditable="true">，不是 div，必须单独一条。
FRAME_EDITOR_SELECTOR = (
    'body[contenteditable="true"], '
    'div[contenteditable="true"][data-placeholder*="正文"], '
    'div.ProseMirror[contenteditable="true"], '
    '.public-DraftEditor-content[contenteditable="true"], '
    'div[contenteditable="true"]'
)
# 标题输入框：写稿页真实类名为 .article-write_box-title-input，
# placeholder 是「在此输入文章标题，最少5个字」（不含「标题」以外的固定文案）。
TITLE_SELECTOR = (
    'input.article-write_box-title-input, '
    'input[placeholder*="文章标题"], '
    'input[placeholder*="标题"], '
    'textarea[placeholder*="标题"], '
    '[contenteditable="true"][data-placeholder*="标题"]'
)
# 封面回读：大鱼号封面区常见结构，若干候选依次尝试。
COVER_IMAGE_SELECTOR = (
    '.cover-container img, [class*=cover] img, [class*=Cover] img, '
    '[class*=cover] [class*=thumb] img, [class*=coverImg], [class*=cover-img]'
)
# UEditor 图片按钮：点开「图片」面板后，弹窗内是「添加本地图片」的 webuploader。
TOOLBAR_PIC_SELECTOR = (
    'div.edui-for-pic, '
    'div.edui-icon.iconfont.wm-icon-pic, '
    'div[class*=edui-for-pic]'
)
# 图片弹窗的本地文件 input（webuploader 隐藏元素）
LOCAL_FILE_INPUT_SELECTOR = (
    'input.webuploader-element-invisible, '
    'input[type="file"][accept*="image"], '
    'input[type="file"][accept*="imgs"]'
)
# 图片弹窗底部「确认」
MATERIAL_CONFIRM_SELECTOR = (
    '.article-material-image-dialog_btn button:has-text("确认"), '
    '.article-material-image-dialog button:has-text("确认")'
)
# 封面区：三封面/单封面切换与「设置封面」入口
COVER_MODE_SELECTOR = 'span.article-write-article-cover_normal-option-text'
COVER_ITEM_SELECTOR = (
    'div.article-write-article-cover_normal-item, '
    'div.article-write-article-cover-item'
)
# 「信息来源」单选项：大鱼号合规声明
AI_SOURCE_LABEL = "AI生成"


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
    if any(k in u for k in ("/login", "passport", "auth")):
        return True
    # 未登录时访问后台路由会被重定向到首页并带上 redirect_url 回跳参数，
    # URL 里既没有 /login，也没有 dashboard，单看路径会漏判成「已登录」。
    # 注意 redirect_url 参数本身含 "dashboard"，只能拿 path 部分判断。
    path = u.split("?", 1)[0].split("#", 1)[0]
    if "redirect_url=" in u and "dashboard" not in path:
        return True
    return False


# 登录页特征：出现密码/验证码输入框或登录方式切换，说明当前是登录页而非后台。
LOGIN_FORM_SELECTORS = (
    'input[placeholder*="账号"]',
    'input[placeholder*="手机号"]',
    'input[type="password"]',
    'text=密码登录',
    'text=验证码登录',
)


async def _looks_logged_out(page: Page) -> bool:
    """页面上是否出现登录表单；作为 URL 判据之外的第二道兜底。"""
    for sel in LOGIN_FORM_SELECTORS:
        try:
            if await page.locator(sel).count():
                return True
        except Exception:
            continue
    return False


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
            if _is_login_url(cur_url) or await _looks_logged_out(page):
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
        self.draft_saved = False

    async def main(self) -> str:
        async with async_playwright() as playwright:
            await self.upload(playwright)
        return self.published_url

    # ---------------------------------------------------------------- 编辑器定位

    async def _find_editor_frame(self, page: Page):
        """找到真正承载正文的 frame。

        大鱼号用 UEditor：顶层页面只有 .edui-editor-iframeholder 外壳，
        真正可编辑的是 iframe 内的 body[contenteditable=true]。
        """
        # 优先在顶层找（部分版本把编辑器直接挂在顶层）
        if await page.locator(EDITOR_SELECTOR).count():
            return None
        for frame in page.frames:
            try:
                if await frame.locator(FRAME_EDITOR_SELECTOR).count():
                    return frame
            except Exception:
                continue
        return None

    async def _editor_locator(self, page: Page):
        """返回 (locator, 是否位于 iframe)。frame 为 None 时表示顶层。"""
        frame = await self._find_editor_frame(page)
        target = frame if frame is not None else page
        selector = FRAME_EDITOR_SELECTOR if frame is not None else EDITOR_SELECTOR
        return target.locator(selector).first, frame

    # ---------------------------------------------------------------- 填写

    async def fill_fields(self, page: Page) -> None:
        """把标题、正文、封面、内文图填进大鱼号发文页。"""
        # 标题
        title_input = page.locator(TITLE_SELECTOR).first
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

        # 正文（UEditor 在 iframe 内，键盘事件必须打到该 frame 上）
        editor, frame = await self._editor_locator(page)
        if not await editor.count():
            raise RuntimeError("未找到大鱼号正文编辑器")
        await editor.click(force=True)
        await page.wait_for_timeout(400)

        # 正文按 [[插图N]] 标记切段，插图要落在标记所在位置而不是文末
        segments = self._split_body_by_markers(self.body)
        inline_index = 0
        for text, marker_no in segments:
            for idx, line in enumerate(text.splitlines()):
                if idx:
                    await page.keyboard.press("Enter")
                if line.strip():
                    await page.keyboard.insert_text(line)
            if marker_no is None:
                continue
            inline_index += 1
            image_path = (
                self.file_list[marker_no - 1]
                if marker_no - 1 < len(self.file_list)
                else (self.file_list[inline_index - 1] if inline_index - 1 < len(self.file_list) else "")
            )
            if not image_path:
                raise RuntimeError(f"大鱼号插图 {marker_no} 缺少对应图片文件")
            ok = await self._try_inline_image(page, image_path)
            if not ok:
                raise RuntimeError(f"大鱼号内文图插入失败，禁止带病发布: {image_path}")
            dayu_logger.success(f"已插入大鱼号内文图 {marker_no}: {image_path}")

        await page.wait_for_timeout(900)
        actual = (await editor.inner_text() or "")
        probe = next((ln.strip() for ln in self.body.splitlines() if ln.strip()), "")
        if probe and probe[:12] not in actual:
            raise RuntimeError(
                f"大鱼号正文写入校验失败：期望含「{probe[:12]}」，实际长度={len(actual)}"
            )
        if "[[插图" in actual:
            raise RuntimeError("大鱼号正文仍残留插图标记，禁止发布")
        dayu_logger.info(
            f"已填写大鱼号正文，长度={len(self.body)}，编辑器实际={len(actual)}，"
            f"插图 {inline_index} 张（{'iframe 内 UEditor' if frame is not None else '顶层编辑器'}）"
        )

        # 封面：优先找封面区域的图片上传入口
        if not await self._try_cover(page):
            raise RuntimeError("大鱼号封面上传未通过回读校验，禁止带病发布")

        # 合规声明：「信息来源」勾选 AI生成
        await self._select_ai_source(page)

    @staticmethod
    def _split_body_by_markers(body: str):
        """把正文按 [[插图N]] 标记切成 [(文本, 标记号 or None), ...]。

        标记是上传器内部定位插图位置的锚点；正文写完后必须不再残留标记。
        """
        segments = []
        buffer = []
        for line in (body or "").splitlines():
            match = re.fullmatch(r"\s*\[\[插图(\d+)\]\]\s*", line)
            if match:
                segments.append(("\n".join(buffer), int(match.group(1))))
                buffer = []
            else:
                buffer.append(line)
        segments.append(("\n".join(buffer), None))
        return segments

    async def _select_ai_source(self, page: Page) -> None:
        """勾选「信息来源 → AI生成」，满足平台对 AI 内容的声明要求。"""
        try:
            label = page.locator(f'label:has-text("{AI_SOURCE_LABEL}")').first
            if await label.count() and await label.is_visible():
                await label.click(force=True)
                await page.wait_for_timeout(800)
                dayu_logger.success(f"已勾选大鱼号信息来源：{AI_SOURCE_LABEL}")
                return
            radio = page.locator('input[type="radio"]').nth(1)
            if await radio.count():
                await radio.click(force=True)
                await page.wait_for_timeout(800)
                dayu_logger.success(f"已勾选大鱼号信息来源：{AI_SOURCE_LABEL}")
                return
            dayu_logger.warning("大鱼号未找到「信息来源 - AI生成」单选项，需人工确认")
        except Exception as exc:
            dayu_logger.warning(f"大鱼号 AI 声明勾选失败: {exc}")

    async def _try_cover(self, page: Page) -> bool:
        """大鱼号封面：素材库为空时走「临时插图 → 从正文中选择 → 保存」。

        大鱼号封面区的真实结构是
        `div.article-write-article-cover_normal-items` 下挂 `..._cover-item_set`（文字「设置封面」）。
        点它弹出的是 `div.article-material-image-dialog` **素材库选择**弹窗，
        账号素材库为空（显示「暂无可用图片」），弹窗里只有「取消 / 下一步」，
        没有任何本地文件 input——所以常规「本地上传」路径根本不存在。

        唯一可行的通道是封面槽位上的「从正文中选择」按钮：
        正文里已插入的图片会出现在同一个弹窗里，选中 →「下一步」→「保存」，
        封面区随即回读到 `wemedia/s/upload/...` 的封面图。

        由此得到实现顺序：
          1. 把 16:9 头图临时插进正文（此时正文有 3 张图）；
          2. 「从正文中选择」→ 选中刚插入的那张 →「下一步」→「保存」；
          3. 回读封面区 src 确认变化；
          4. 删掉正文里的临时图，恢复「2 张内文图」的正文结构。

        「从正文中选择」按钮默认隐藏（hover 封面槽位才显示），必须先 hover。
        """
        if not self.cover_path:
            dayu_logger.warning("大鱼号未提供封面")
            return False

        old_src = await self._cover_src(page)
        editor, _frame = await self._editor_locator(page)
        if not await editor.count():
            dayu_logger.warning("大鱼号正文编辑器不存在，无法借道设封面")
            return False

        base_imgs = await editor.locator("img").count()

        # 1) 临时把头图插进正文
        if not await self._try_inline_image(page, self.cover_path):
            dayu_logger.warning("大鱼号封面：临时插图失败，无法借道设封面")
            return False
        await page.wait_for_timeout(1500)
        if await editor.locator("img").count() <= base_imgs:
            dayu_logger.warning("大鱼号封面：临时图未进入正文")
            return False

        try:
            # 2) 切单封面 + hover 出「从正文中选择」
            await self._switch_single_cover(page)
            opened = await self._open_choose_from_body(page)
            if not opened:
                return False

            # 3) 选中刚插入的那张图（正文末尾那张 = 新插入的）
            imgs = page.locator(
                "div.article-material-image-dialog .article-material-image_image-choose img"
            )
            count = await imgs.count()
            if not count:
                dayu_logger.warning("大鱼号封面：「从正文中选择」弹窗里没有可选图片")
                return False
            await imgs.nth(count - 1).click(force=True)
            await page.wait_for_timeout(1500)

            # 4) 下一步 → 保存
            nxt = page.locator(
                '.article-material-image-dialog_btn button:has-text("下一步")'
            ).first
            if not await nxt.count():
                dayu_logger.warning("大鱼号封面：弹窗缺少「下一步」")
                return False
            await nxt.click(force=True)
            await page.wait_for_timeout(4000)

            save = page.locator(
                '.article-material-image-dialog button:has-text("保存")'
            ).first
            if not await save.count():
                dayu_logger.warning("大鱼号封面：预览弹窗缺少「保存」")
                return False
            await save.click(force=True)
            await page.wait_for_timeout(5000)

            changed = await self._cover_changed(page, old_src)
        finally:
            # 5) 无论成败都要把临时图从正文里删掉，正文结构不能被封面污染
            await self._remove_last_editor_image(page, editor)

        if changed:
            dayu_logger.success(f"已设置大鱼号封面: {self.cover_path}")
            return True
        dayu_logger.warning("大鱼号封面未回读到新封面，需人工处理")
        return False

    async def _switch_single_cover(self, page: Page) -> None:
        """切到「单封面」模式。

        三封面模式要求 3 张图，母本只备了 1 张 16:9 头图；
        单选框是一个自定义 div（`w-radio`），点文字不会切换，必须点 radio 本体。
        """
        try:
            radio = page.locator(
                'div.article-write-article-cover_normal-option:has-text("单封面") div.w-radio'
            ).first
            if not await radio.count():
                return
            checked = await radio.evaluate(
                "el => el.className.includes('w-radio_checked')"
            )
            if not checked:
                await radio.click(force=True)
                await page.wait_for_timeout(2000)
        except Exception as exc:
            dayu_logger.warning(f"大鱼号切换单封面失败: {exc}")

    async def _open_choose_from_body(self, page: Page) -> bool:
        """点开封面槽位上的「从正文中选择」。

        该按钮默认隐藏（需 hover 封面项才显示），且页面里存在多个同名按钮
        （横版/竖版/隐藏的三封面槽位各一份），必须只点可见的那个。
        """
        item = page.locator(
            "div.article-write-article-cover_normal-item "
            "div.article-write-article-cover-cover-item"
        ).first
        try:
            if await item.count():
                await item.hover()
                await page.wait_for_timeout(1200)
        except Exception:
            pass

        btns = page.locator('button:has-text("从正文中选择")')
        for idx in range(await btns.count()):
            btn = btns.nth(idx)
            try:
                if not await btn.is_visible():
                    continue
                await btn.click(force=True)
                await page.wait_for_timeout(4000)
                return True
            except Exception:
                continue
        dayu_logger.warning("大鱼号未找到可见的「从正文中选择」按钮")
        return False

    async def _remove_last_editor_image(self, page: Page, editor) -> None:
        """删掉正文里最后一张（用于封面借道的临时图）。

        UEditor 的图片点一下即选中，按 Delete 即可删除。
        """
        try:
            imgs = editor.locator("img")
            count = await imgs.count()
            if not count:
                return
            await imgs.nth(count - 1).click(force=True)
            await page.wait_for_timeout(800)
            await page.keyboard.press("Delete")
            await page.wait_for_timeout(1500)
            after = await editor.locator("img").count()
            if after < count:
                dayu_logger.info(f"已移除正文临时封面图，剩余图片 {after} 张")
            else:
                dayu_logger.warning("大鱼号正文临时封面图未能删除")
        except Exception as exc:
            dayu_logger.warning(f"大鱼号正文临时封面图删除失败: {exc}")

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
        """内文图：走 UEditor 工具栏「图片」按钮 →「添加本地图片」→ 确认。

        大鱼号正文编辑器是 UEditor，正文在 iframe 内；工具栏的「图片」按钮会弹出
        一个图片选择面板，面板里才有 webuploader 的本地文件 input。
        直接对页面找 `input[type=file]` 找不到——那个 input 只在弹窗打开后才存在。

        只看到「已选择文件」不等于图已插进正文；必须回读到编辑器内 `<img>`
        数量增加，否则按插入失败处理，交由调用方 fail-closed。
        """
        editor, _frame = await self._editor_locator(page)
        if not await editor.count():
            dayu_logger.warning("大鱼号正文编辑器不存在，无法插图")
            return False
        before = await editor.locator("img").count()

        # 1) 点开工具栏「图片」按钮
        opened = False
        for sel in (TOOLBAR_PIC_SELECTOR, 'i.iconfont.wm-icon-pic', 'text=图片'):
            loc = page.locator(sel).first
            try:
                if not await loc.count():
                    continue
                await loc.scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                await loc.click(force=True)
                opened = True
                break
            except Exception:
                continue
        if not opened:
            dayu_logger.warning("大鱼号未找到工具栏「图片」按钮")
            return False
        await page.wait_for_timeout(2500)

        # 2) 灌入本地文件（弹窗打开后 webuploader 的隐藏 input 才存在）
        file_input = page.locator(LOCAL_FILE_INPUT_SELECTOR).first
        if not await file_input.count():
            file_input = page.locator('input[type="file"]').first
        if not await file_input.count():
            dayu_logger.warning("大鱼号图片弹窗未出现文件输入框")
            return False
        try:
            await file_input.set_input_files(image_path)
        except Exception as exc:
            dayu_logger.warning(f"大鱼号内文图上传失败: {exc}")
            return False
        await page.wait_for_timeout(5000)

        # 3) 点「确认」把图插进编辑器
        confirm = page.locator(MATERIAL_CONFIRM_SELECTOR).first
        try:
            if await confirm.count() and await confirm.is_visible():
                await confirm.click(force=True)
                await page.wait_for_timeout(3500)
        except Exception as exc:
            dayu_logger.warning(f"大鱼号图片弹窗确认失败: {exc}")

        after = await editor.locator("img").count()
        if after > before:
            return True
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
                DAYU_CONTENT_LIST_URL,
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

            if _is_login_url(page.url) or await _looks_logged_out(page):
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

            # 操作按钮。写稿页底栏真实按钮文案是「发表 / 预览 / 清空 / 保存」，
            # 旧选择器只认「发布」「存草稿」，两个都匹配不到。
            action_btn = None
            action_kind = ""
            if self.is_draft:
                candidates = (
                    'button:has-text("保存"):not(:has-text("保存中"))',
                    'button:has-text("存草稿")',
                    'button:has-text("保存草稿")',
                )
                action_kind = "存草稿"
            else:
                candidates = (
                    'button:has-text("发表")',
                    'button:has-text("发布")',
                    'button:has-text("确认发布")',
                )
                action_kind = "发表"

            for sel in candidates:
                btn = page.locator(sel).first
                if await btn.count() and await btn.is_visible():
                    action_btn = btn
                    break

            if action_btn is not None:
                await action_btn.click(force=True)
                dayu_logger.info(f"已点击大鱼号「{action_kind}」按钮")
                await page.wait_for_timeout(4000)
                done_shot = Path(self.account_file).parent / "dayu_article_done.png"
                await page.screenshot(path=str(done_shot))
                dayu_logger.success(f"🎉 大鱼号{action_kind}完成！截图: {done_shot}")
                if self.is_draft:
                    self.draft_saved = await self._verify_draft_saved(page)
                    if not self.draft_saved:
                        raise RuntimeError(
                            "大鱼号草稿保存未通过核验：内容管理页未找到本期标题，"
                            "不得让调用方误判为已存草稿"
                        )
                else:
                    self.published_url = await self.wait_publish(page, timeout_s=60)
            else:
                dayu_logger.warning("未定位到大鱼号操作按钮")

        finally:
            # 代填模式下窗口要留给人工点发布，不能由脚本关闭
            if not self.prepare_only:
                await browser.close()

    async def _verify_draft_saved(self, page: Page) -> bool:
        """存草稿后回「我的草稿」页核对标题是否真的落库。

        只点了「保存」按钮不等于草稿已保存：页面可能弹校验失败、可能被拦在
        必填项上。这里跳到草稿列表按标题回读，确认后才置 draft_saved。
        """
        try:
            await page.goto(
                DAYU_DRAFT_LIST_URL,
                timeout=90000,
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(6000)
        except Exception as exc:
            dayu_logger.warning(f"大鱼号草稿核验：跳转草稿页失败 {exc}")
            return False

        body = ""
        try:
            body = (await page.evaluate("() => document.body.innerText") or "")
        except Exception:
            pass
        if self.title and self.title in body:
            dayu_logger.success(f"✅ 大鱼号草稿核验通过：内容管理页已出现「{self.title}」")
            shot = Path(self.account_file).parent / "dayu_article_draft.png"
            await page.screenshot(path=str(shot))
            return True
        dayu_logger.warning(
            f"大鱼号草稿核验未通过：内容管理页未出现「{self.title}」，请人工到后台确认"
        )
        shot = Path(self.account_file).parent / "dayu_article_draft.png"
        await page.screenshot(path=str(shot))
        return False
