# -*- coding: utf-8 -*-
"""知乎文章发布（Playwright）。第一期仅文章。"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page, async_playwright

from conf import LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from utils.base_social_media import set_init_script
from utils.log import zhihu_logger

ZHIHU_LOGIN_URL = "https://www.zhihu.com/signin"
ZHIHU_WRITE_URL = "https://zhuanlan.zhihu.com/write"
ZHIHU_CREATOR_URL = "https://www.zhihu.com/creator"
TITLE_MAX_LEN = 100
COVER_MAX_BYTES = 10 * 1024 * 1024
COVER_EXTS = {".jpg", ".jpeg", ".png"}

# 知乎写文章页「创作声明」下拉选项
ZHIHU_CREATION_STATEMENT_OPTIONS = (
    "包含剧透",
    "包含医疗建议",
    "虚构创作",
    "包含理财内容",
    "包含 AI 辅助创作 作者对内容负责",
    "无声明",
)
ZHIHU_CREATION_STATEMENT_DEFAULT = "包含 AI 辅助创作 作者对内容负责"

# 发布入口在清洗后的正文里保留这些位置标记；上传器逐段插图后必须全部替换。
INLINE_MARKER_RE = re.compile(r"\[\[插图(\d+)\]\]")


def _split_inline_body(body: str) -> list[tuple[str, int]]:
    """按插图位置标记拆正文，返回 [(文本段, 后随插图序号)]。"""
    parts = INLINE_MARKER_RE.split(body or "")
    out: list[tuple[str, int]] = []
    for i in range(0, len(parts), 2):
        text = parts[i]
        marker = int(parts[i + 1]) if i + 1 < len(parts) else 0
        if text or marker:
            out.append((text, marker))
    return out


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
    return any(k in u for k in ("/signin", "/login", "passport", "account/login"))


async def _has_auth_cookie(context) -> bool:
    """知乎登录成功后会有 z_c0。"""
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    return any(c.get("name") == "z_c0" for c in cookies)


async def _wait_until_logged_in(page: Page, context, timeout_ms: int = 200_000) -> bool:
    """等待人工登录完成（优先检测 z_c0）。"""
    elapsed = 0
    step = 1000
    while elapsed < timeout_ms:
        if await _has_auth_cookie(context):
            return True
        if (not _is_login_url(page.url)) and ("zhihu.com" in (page.url or "")):
            # 已离开登录页时再确认一次 cookie
            await page.wait_for_timeout(500)
            if await _has_auth_cookie(context):
                return True
        await page.wait_for_timeout(step)
        elapsed += step
    return await _has_auth_cookie(context)


async def cookie_auth(account_file) -> bool:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS))
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(ZHIHU_WRITE_URL, timeout=60000)
            await page.wait_for_timeout(3000)
            if _is_login_url(page.url):
                zhihu_logger.error("[+] 知乎 cookie 失效")
                return False
            # 写文章页或创作中心标记
            markers = [
                'textarea[placeholder*="标题"]',
                'input[placeholder*="标题"]',
                'text=发布',
                'text=写文章',
            ]
            for sel in markers:
                try:
                    if await page.locator(sel).count():
                        zhihu_logger.success("[+] 知乎 cookie 有效")
                        return True
                except Exception:
                    continue
            if "zhihu.com" in page.url and not _is_login_url(page.url):
                zhihu_logger.success("[+] 知乎 cookie 有效（域名已登录）")
                return True
            zhihu_logger.error("[+] 知乎 cookie 失效")
            return False
        except Exception as exc:
            zhihu_logger.warning(f"[+] cookie 校验异常，按失效处理: {exc}")
            return False
        finally:
            await browser.close()


async def zhihu_cookie_gen(account_file):
    """CLI/本地调试：打开登录页，手动登录后保存 cookie。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto(ZHIHU_LOGIN_URL, timeout=60000)
        zhihu_logger.info("请在浏览器中完成知乎登录；检测到登录凭证 z_c0 后会自动保存…")
        ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError("知乎登录超时：未检测到登录凭证 z_c0，请重新登录")
        Path(account_file).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=account_file)
        zhihu_logger.success(f"cookie saved: {account_file}")
        await context.close()
        await browser.close()


async def zhihu_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        zhihu_logger.error("cookie文件不存在或已失效，即将打开浏览器，请手动登录知乎")
        await zhihu_cookie_gen(account_file)
    return True


class ZhiHuArticle(object):
    """知乎文章发布。"""

    def __init__(
        self,
        title,
        body,
        tags,
        publish_date: datetime | int,
        account_file,
        dry_run=False,
        cover_path=None,
        creation_statement=None,
        is_draft=False,
        inline_image_paths=None,
    ):
        import re
        raw_title = (title or "").strip()
        cleaned_title = re.sub(r"^【(?:知乎)?专栏\d+】\s*", "", raw_title).strip()
        self.title = cleaned_title[:TITLE_MAX_LEN]
        if not self.title:
            raise ValueError("知乎文章标题不能为空")
        self.body = (body or "").strip()
        if not self.body:
            raise ValueError("知乎文章正文不能为空")
        self.tags = tags or []
        self.publish_date = publish_date
        self.account_file = account_file
        self.dry_run = bool(dry_run)
        self.is_draft = bool(is_draft)
        self.headless = LOCAL_CHROME_HEADLESS if not self.dry_run else False
        self.cover_path = self._normalize_cover(cover_path)
        self.creation_statement = self._normalize_creation_statement(creation_statement)
        self.inline_image_paths = [
            str(Path(p).resolve()) for p in (inline_image_paths or [])
            if p and Path(p).is_file()
        ]

    @staticmethod
    def _normalize_cover(cover_path) -> str | None:
        if not cover_path or not str(cover_path).strip():
            return None
        p = Path(str(cover_path).strip())
        if not p.is_file():
            zhihu_logger.warning(f"封面不存在，将跳过: {p}")
            return None
        if p.suffix.lower() not in COVER_EXTS:
            zhihu_logger.warning(f"封面格式不支持（需 jpg/jpeg/png），将跳过: {p}")
            return None
        if p.stat().st_size > COVER_MAX_BYTES:
            zhihu_logger.warning(f"封面超过 10MB，将跳过: {p}")
            return None
        return str(p.resolve())

    @staticmethod
    def _normalize_creation_statement(creation_statement) -> str:
        if creation_statement is None or creation_statement == "":
            return ZHIHU_CREATION_STATEMENT_DEFAULT
        if isinstance(creation_statement, (list, tuple)):
            raw = next((str(x).strip() for x in creation_statement if str(x).strip()), "")
        else:
            raw = str(creation_statement).strip()
        if not raw:
            return ZHIHU_CREATION_STATEMENT_DEFAULT

        aliases = {
            "无": "无声明",
            "无特别声明": "无声明",
            "剧透": "包含剧透",
            "医疗": "包含医疗建议",
            "医疗建议": "包含医疗建议",
            "虚构": "虚构创作",
            "理财": "包含理财内容",
            "理财内容": "包含理财内容",
            "ai": "包含 AI 辅助创作 作者对内容负责",
            "AI": "包含 AI 辅助创作 作者对内容负责",
            "AI辅助": "包含 AI 辅助创作 作者对内容负责",
            "包含AI辅助创作": "包含 AI 辅助创作 作者对内容负责",
            "包含 AI 辅助创作": "包含 AI 辅助创作 作者对内容负责",
        }
        text = aliases.get(raw, aliases.get(raw.lower(), raw))
        if text in ZHIHU_CREATION_STATEMENT_OPTIONS:
            return text
        matched = next(
            (opt for opt in ZHIHU_CREATION_STATEMENT_OPTIONS if opt.startswith(text) or text in opt),
            None,
        )
        if matched:
            return matched
        zhihu_logger.warning(f"未知创作声明「{raw}」，回退为「{ZHIHU_CREATION_STATEMENT_DEFAULT}」")
        return ZHIHU_CREATION_STATEMENT_DEFAULT

    async def upload(self, playwright) -> str:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=self.headless))
        context = await browser.new_context(storage_state=str(self.account_file))
        context = await set_init_script(context)
        page = await context.new_page()
        try:
            await page.goto(ZHIHU_WRITE_URL, timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            if _is_login_url(page.url):
                raise RuntimeError("知乎 cookie 已失效，请重新登录")
            await self.fill_title(page)
            await self.fill_body(page)
            await self._insert_inline_images(page)
            if self.cover_path:
                await self.upload_cover(page)
            await self.apply_creation_statement(page)
            # 等待知乎自动保存草稿完成（底部常驻显示“刚刚 · 草稿”）
            await page.wait_for_timeout(3000)

            if self.is_draft:
                zhihu_logger.success(f"✅ 已成功将文章存入知乎官方草稿箱: {self.title}")
                try:
                    preview = Path(self.account_file).with_name("zhihu_article_draft_saved.png")
                    await page.screenshot(full_page=True, path=str(preview))
                    zhihu_logger.info(f"📸 草稿凭证截图已保存: {preview}")
                except Exception as exc:
                    zhihu_logger.debug(f"草稿截图保存失败: {exc}")
                return ""

            if self.dry_run:
                zhihu_logger.warning("🛑 【仅预览不发布】已跳过点击发布按钮")
                zhihu_logger.info("👀 请在浏览器窗口核对：标题、正文、封面、创作声明")
                zhihu_logger.info("⏳ 页面将保持打开约 120 秒后自动关闭（也可手动关浏览器）")
                try:
                    preview = Path(self.account_file).with_name("zhihu_article_dry_run_preview.png")
                    await page.screenshot(full_page=True, path=str(preview))
                    zhihu_logger.info(f"📸 预览截图已保存: {preview}")
                except Exception as exc:
                    zhihu_logger.debug(f"预览截图失败: {exc}")
                for i in range(120):
                    await asyncio.sleep(1)
                    if i in (30, 60, 90):
                        zhihu_logger.info(f"⏳ 预览中…还剩约 {120 - i} 秒自动关闭")
                zhihu_logger.info("🛑 预览结束，关闭浏览器（未点击发布）")
                return ""
            published_url = await self.click_publish(page)
            zhihu_logger.success("知乎文章发布流程已完成点击发布")
            return published_url
        finally:
            await context.close()
            await browser.close()

    async def apply_creation_statement(self, page: Page) -> None:
        """选择写文章页「创作声明」下拉项。"""
        label = self.creation_statement or ZHIHU_CREATION_STATEMENT_DEFAULT
        try:
            # 创作声明在编辑区下方，先滚入视口
            for _ in range(10):
                if await page.locator('label:has-text("创作声明")').count():
                    break
                await page.mouse.wheel(0, 500)
                await page.wait_for_timeout(200)

            row = page.locator('div:has(> label:has-text("创作声明"))').first
            if not await row.count():
                zhihu_logger.warning("未找到创作声明区域，请 dry_run 人工确认")
                return

            combo = row.locator('button[role="combobox"], button.Select-button').first
            if not await combo.count():
                zhihu_logger.warning("未找到创作声明下拉按钮，请 dry_run 人工确认")
                return

            await combo.scroll_into_view_if_needed()
            current = ((await combo.inner_text()) or "").strip().replace("\n", " ")
            if current == label:
                zhihu_logger.info(f"创作声明已是「{label}」")
                return

            await combo.click(timeout=5000, force=True)
            await page.wait_for_timeout(500)

            # 选项在 Popover 中：button.Select-option[role=option]
            option = page.get_by_role("option", name=label, exact=True).first
            if not await option.count():
                option = page.locator('button.Select-option[role="option"]').filter(has_text=label).first
            if not await option.count():
                option = page.locator('button.Select-option[role="option"]').filter(has_text="包含 AI 辅助创作").first
            if not await option.count():
                # 再试一次打开
                await combo.click(timeout=3000, force=True)
                await page.wait_for_timeout(600)
                option = page.get_by_role("option", name=label, exact=True).first

            if not await option.count():
                zhihu_logger.warning(f"下拉中未找到创作声明选项「{label}」")
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                return

            await option.click(timeout=5000, force=True)
            await page.wait_for_timeout(400)

            after = ((await combo.inner_text()) or "").strip().replace("\n", " ")
            if after == label or label in after:
                zhihu_logger.success(f"已选择创作声明: {label}")
            else:
                zhihu_logger.warning(f"创作声明点击后显示为「{after}」，期望「{label}」")
        except Exception as exc:
            zhihu_logger.warning(f"设置创作声明失败（可忽略）: {exc}")

    async def fill_title(self, page: Page) -> None:
        candidates = [
            page.locator('textarea[placeholder*="标题"]').first,
            page.locator('input[placeholder*="标题"]').first,
            page.get_by_placeholder("请输入标题").first,
            page.locator('[class*="WriteIndex"] textarea').first,
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
            raise RuntimeError("未找到知乎文章标题输入框")
        await title_field.click(force=True)
        await title_field.fill("")
        await title_field.fill(self.title)
        zhihu_logger.info(f"已填写文章标题: {self.title}")

    @staticmethod
    async def _paste_text(page: Page, text: str) -> bool:
        """通过真实 ClipboardEvent 把一段纯文本写入当前 Draft.js 选区。"""
        return bool(await page.evaluate(
            """(text) => {
                const el = document.querySelector('.public-DraftEditor-content') || document.querySelector('[contenteditable="true"]');
                if (!el) return false;
                const dt = new DataTransfer();
                dt.setData('text/plain', text);
                const evt = new ClipboardEvent('paste', {
                    bubbles: true,
                    cancelable: true,
                    clipboardData: dt
                });
                el.dispatchEvent(evt);
                return true;
            }""",
            text,
        ))

    @staticmethod
    async def _select_text(page: Page, needle: str, occurrence: int = 1) -> bool:
        """在正文中选中指定文本，供插图按钮原位替换。"""
        return bool(await page.evaluate(
            """([needle, occurrence]) => {
                const ed = document.querySelector('.public-DraftEditor-content');
                if (!ed) return false;
                const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
                let seen = 0;
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    const pos = (node.textContent || '').indexOf(needle);
                    if (pos < 0) continue;
                    seen += 1;
                    if (seen < occurrence) continue;
                    ed.focus();
                    const range = document.createRange();
                    range.setStart(node, pos);
                    range.setEnd(node, pos + needle.length);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                    return true;
                }
                return false;
            }""",
            [needle, occurrence],
        ))

    async def fill_body(self, page: Page) -> None:
        selectors = [
            ".public-DraftEditor-content",
            '[contenteditable="true"]',
            ".ProseMirror",
            'div[role="textbox"]',
            ".ql-editor",
        ]
        editor = None
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if await loc.count() and await loc.is_visible():
                    editor = loc
                    break
            except Exception:
                continue
        if not editor:
            raise RuntimeError("未找到知乎文章正文编辑器")
        await editor.click(force=True)
        await page.wait_for_timeout(300)

        # 优先使用标准的 ClipboardEvent + DataTransfer 注入 Draft.js 富文本编辑器（跨平台可靠）
        try:
            await self._paste_text(page, self.body)
        except Exception as exc:
            zhihu_logger.debug(f"模拟粘贴事件出错: {exc}")

        await page.wait_for_timeout(1000)
        text_now = (await editor.inner_text()).strip()

        if not text_now or len(text_now) < 10:
            # 降级方案 1: document.execCommand insertText
            try:
                await page.keyboard.insert_text(self.body)
                await page.wait_for_timeout(1000)
                text_now = (await editor.inner_text()).strip()
            except Exception as exc:
                zhihu_logger.debug(f"execCommand insertText 出错: {exc}")

        if not text_now or len(text_now) < 10:
            # 降级方案 2: 逐段按键输入
            for para in self.body.split("\n"):
                if para.strip():
                    await page.keyboard.type(para, delay=2)
                await page.keyboard.press("Enter")

        final_len = len(await editor.inner_text())
        zhihu_logger.info(f"已填写正文，文本长度={len(self.body)}，知乎编辑器实际录入字数={final_len}")
        if final_len < max(80, int(len(self.body.strip()) * 0.6)):
            raise RuntimeError(
                f"知乎正文写入校验失败：期望约 {len(self.body.strip())} 字，实际录入 {final_len} 字"
            )

    @staticmethod
    async def _editor_image_count(page: Page) -> int:
        editor = page.locator(".public-DraftEditor-content, .ProseMirror, [contenteditable='true']").first
        try:
            return await editor.locator("img").count()
        except Exception:
            return 0

    async def _insert_one_image(self, page: Page, path: Path, expected_count: int) -> None:
        """在已选中的插图位置替换一张 16:9 内文图，并校验图片增量。"""
        before = await self._editor_image_count(page)
        btn = page.locator(
            'button[aria-label*="图片"], '
            '.Editable-toolbar button:has-text("图片"), '
            'button:has-text("图片")'
        ).first
        if not await btn.count():
            raise RuntimeError("未找到知乎图片按钮")
        await btn.click(force=True, timeout=5000)
        await page.wait_for_timeout(1000)

        file_input = page.locator('.Modal-content input[type="file"]').last
        if not await file_input.count():
            file_input = page.locator('input[type="file"][accept*="image"]').last
        if not await file_input.count():
            raise RuntimeError("知乎图片弹窗无文件输入")
        await file_input.set_input_files(str(path))

        last_text = ""
        uploaded = False
        for _ in range(30):
            await page.wait_for_timeout(1000)
            state = await page.evaluate(
                """() => {
                    const modal = document.querySelector('.Modal-content');
                    if (!modal) return ['', 0, 0];
                    return [
                        (modal.innerText || '').replace(/\\s/g, ''),
                        modal.querySelectorAll('.DraggableTags-tag').length,
                        modal.querySelectorAll('.DraggableTags-tag .CircleLoadingBar').length,
                    ];
                }"""
            )
            last_text = str(state[0] or "")
            if (
                "已上传1张" in last_text
                and int(state[1] or 0) >= 1
                and int(state[2] or 0) == 0
                and "上传失败" not in last_text
                and "批量重新上传" not in last_text
            ):
                uploaded = True
                break
        if not uploaded:
            raise RuntimeError(f"知乎内文图上传失败: {path.name}，弹窗文本: {last_text[-160:]}")

        card = page.locator('.Modal-content .DraggableTags-tag').first
        if not await card.count():
            raise RuntimeError(f"知乎图片弹窗无已上传卡片: {path.name}")
        # 关键：新版知乎必须先选中卡片，直接点「插入图片」只会关闭弹窗。
        await card.locator("img").first.click(force=True, modifiers=["Meta"])
        await page.wait_for_timeout(300)

        insert_btn = page.locator(
            '.Modal-content button:has-text("插入图片"), '
            '.Modal-content :text-is("插入图片")'
        ).last
        if not await insert_btn.count():
            raise RuntimeError("未找到「插入图片」按钮")
        await insert_btn.click(force=True)

        after = before
        for _ in range(20):
            await page.wait_for_timeout(500)
            after = await self._editor_image_count(page)
            if after >= expected_count:
                break
        if after < expected_count:
            raise RuntimeError(
                f"内文图 {path.name} 未进入正文，编辑器图片数 {before}→{after}，已中止发布"
            )

    async def _insert_inline_images(self, page: Page) -> None:
        """按母本中的 `[[插图N]]` 精确替换内文图。

        没有位置标记时仍支持旧版：把全部图片追加到正文末尾。
        发布前始终校验图片增量与残留标记，避免缺图或把标记发到线上。
        """
        paths = [Path(p) for p in self.inline_image_paths if Path(p).is_file()]
        if not paths:
            return
        editor = page.locator(
            ".public-DraftEditor-content, .ProseMirror, [contenteditable='true']"
        ).first
        if not await editor.count():
            raise RuntimeError("未找到正文编辑器")
        before = await self._editor_image_count(page)
        markers = INLINE_MARKER_RE.findall(self.body)
        expected = before + len(paths)

        if markers:
            if sorted(int(x) for x in markers) != list(range(1, len(paths) + 1)):
                raise RuntimeError(f"插图位置标记与内文图数量不一致: markers={markers}, images={len(paths)}")
            for marker_no in range(1, len(paths) + 1):
                marker = f"[[插图{marker_no}]]"
                if not await self._select_text(page, marker):
                    raise RuntimeError(f"正文中未找到插图位置标记 {marker}")
                await self._insert_one_image(page, paths[marker_no - 1], before + marker_no)
        else:
            await editor.click(force=True)
            await page.keyboard.press("ControlOrMeta+End")
            for idx, path in enumerate(paths, 1):
                await self._insert_one_image(page, path, before + idx)
                await editor.click(force=True)
                await page.keyboard.press("ControlOrMeta+End")
                await page.keyboard.press("Enter")

        after = await self._editor_image_count(page)
        if after < expected:
            raise RuntimeError(
                f"内文图仅成功插入 {after - before}/{len(paths)} 张，已中止发布，避免正文缺图"
            )
        final_text = await editor.inner_text()
        left = INLINE_MARKER_RE.findall(final_text)
        if left:
            raise RuntimeError(f"插图位置标记未被图片替换: {left}，已中止发布")
        zhihu_logger.info(f"内文图 {len(paths)} 张已进入正文，图片数={after}")

    async def upload_cover(self, page: Page) -> None:
        if not self.cover_path or not os.path.exists(self.cover_path):
            zhihu_logger.warning("封面文件未指定或不存在，跳过")
            return

        cover_input = page.locator('input.UploadPicture-input[type="file"]').first
        if not await cover_input.count():
            cover_input = page.locator('input[type="file"]').last
        if not await cover_input.count():
            zhihu_logger.warning("未找到封面文件选择框，跳过封面")
            return

        try:
            await cover_input.set_input_files(self.cover_path)
            await page.wait_for_timeout(2500)
            for confirm in ('button:has-text("确定")', 'button:has-text("完成")', 'button:has-text("确认")'):
                try:
                    btn = page.locator(confirm).first
                    if await btn.count() and await btn.is_visible():
                        await btn.click(timeout=2000)
                        await page.wait_for_timeout(1000)
                        break
                except Exception:
                    continue
            zhihu_logger.info(f"已成功上传封面: {self.cover_path}")
        except Exception as e:
            zhihu_logger.warning(f"上传封面失败: {e}")

    async def click_publish(self, page: Page) -> str:
        draft_url = re.search(r"/p/(\d+)", page.url or "")
        draft_id = draft_url.group(1) if draft_url else ""
        candidates = [
            page.get_by_role("button", name="发布").first,
            page.locator('button:has-text("发布")').first,
        ]
        for loc in candidates:
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=5000)
                    await page.wait_for_timeout(3000)
                    # 二次确认弹窗
                    for confirm in ('button:has-text("确定")', 'button:has-text("确认发布")'):
                        try:
                            c = page.locator(confirm).first
                            if await c.count() and await c.is_visible():
                                await c.click(timeout=3000)
                                await page.wait_for_timeout(2000)
                        except Exception:
                            pass
                    # 编辑器自动保存的 /p/<id>/edit 与公开文章同 id，
                    # 只有页面离开编辑态且出现发布成功标记时才作为公开链接证据。
                    published = False
                    for marker in ("发布成功", "已发布"):
                        try:
                            if await page.get_by_text(marker, exact=False).count():
                                published = True
                                break
                        except Exception:
                            pass
                    if published and "/edit" not in (page.url or "") and draft_id:
                        return f"https://zhuanlan.zhihu.com/p/{draft_id}"
                    return ""
            except Exception:
                continue
        raise RuntimeError("未找到知乎发布按钮")

    async def main(self) -> str:
        async with async_playwright() as playwright:
            return await self.upload(playwright)
