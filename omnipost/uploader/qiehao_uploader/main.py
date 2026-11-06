# -*- coding: utf-8 -*-
"""企鹅号（腾讯内容开放平台）文章发布与草稿管理（Playwright）。

企鹅号发文页为 https://om.qq.com/main/creation/article，
基于 ProseMirror 编辑器（ExEditor）：

- 标题：`span[data-placeholder^="请输入标题"]`（contenteditable）
- 正文：`div.ProseMirror[contenteditable="true"]`
- 工具栏图片按钮：`exeditor-toolbar-button`（自定义元素，无 class）
- 封面上传：点 `.cover-container .omui-thumb__action span:has-text("更换")`（旧稿已带封面时
  DOM 里没有加号按钮），弹层切「本地上传」→ `input[type=file]` → 确认后回读封面 src
- 自主声明：点「添加内容自主声明」→ 选「该文章由AI生成」
- 内容管理页：`/main/management/articleManage`，公开链接为 `page.om.qq.com/page/<id>`
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
QIEHAO_PUBLISH_URL = "https://om.qq.com/main/creation/article"
QIEHAO_MANAGE_URL = "https://om.qq.com/main/management/articleManage"
INLINE_MARKER_RE = re.compile(r"\[\[插图(\d+)\]\]")

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


def _normalize_title(text: str) -> str:
    """去掉空白与常见标点，便于和后台旧稿/重复标题做包含匹配。"""
    return re.sub(
        r"[\s　，。！？、；：“”‘’（）()\[\]【】《》—\-·….,!?;:'\"]+",
        "",
        text or "",
    )


def _normalize_title(text: str) -> str:
    """去掉空白与常见标点，便于和后台旧稿/重复标题做包含匹配。"""
    return re.sub(
        r"[\s　，。！？、；：“”‘’（）()\[\]【】《》—\-·….,!?;:'\"]+",
        "",
        text or "",
    )


def _title_match(candidate: str, title: str) -> bool:
    """标题包含匹配；企鹅号旧稿残留时可能出现「旧标题+新标题」。"""
    a, b = _normalize_title(candidate), _normalize_title(title)
    return bool(a and b and (a in b or b in a))


def _is_public_qiehao_url(url: str) -> bool:
    """只认企鹅号对外文章页，拒绝后台编辑/内容管理链接。"""
    return bool(re.match(r"^https?://page\.om\.qq\.com/page/[A-Za-z0-9_-]+", url or ""))


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
        # prepare_only：只把内容填好并保留浏览器窗口，由人工点击「发布」，
        # 脚本在后台等待发布落地后回收公开链接。
        self.prepare_only = bool(prepare_only)
        self.headless = LOCAL_CHROME_HEADLESS if not self.dry_run else False
        if self.prepare_only:
            self.headless = False
        self.published_url = ""

    async def main(self) -> str:
        async with async_playwright() as playwright:
            await self.upload(playwright)
        return self.published_url

    # ---------------------------------------------------------------- 步骤

    async def _fill_title(self, page: Page) -> None:
        """标题是 contenteditable 的 autogrowing span，必须用键盘输入并回读校验。

        企鹅号发文页会自动恢复上一篇文章的标题/正文，
        直接追加会写成「旧标题+新标题」，因此先清空再输入。
        """
        title_box = page.locator(
            'span[data-placeholder^="请输入标题"], .omui-articletitle__input [contenteditable="true"]'
        ).first
        if not await title_box.count():
            raise RuntimeError("未找到企鹅号标题输入框")
        await title_box.click(force=True)
        await page.wait_for_timeout(300)
        # 清空自动恢复的旧标题
        await page.keyboard.press("ControlOrMeta+a")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(400)
        await page.keyboard.insert_text(self.title)
        await page.wait_for_timeout(600)
        actual = (await title_box.inner_text() or "").strip()
        if actual != self.title:
            raise RuntimeError(f"企鹅号标题写入校验失败：期望「{self.title}」，实际「{actual[:40]}」")
        qiehao_logger.info(f"已填写企鹅号标题: {self.title} ({len(self.title)}字)")

    async def _fill_body(self, page: Page) -> None:
        """正文为 ProseMirror 编辑器：逐行键盘输入，空行用 Enter。

        同标题，发文页会自动恢复上一篇文章正文，必须先清空。
        """
        editor = page.locator('div.ProseMirror[contenteditable="true"]').first
        if not await editor.count():
            raise RuntimeError("未找到企鹅号正文编辑器")
        await editor.click(force=True)
        await page.wait_for_timeout(300)
        # 清空自动恢复的旧正文
        await page.keyboard.press("ControlOrMeta+a")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(500)
        for idx, line in enumerate(self.body.splitlines()):
            if idx:
                await page.keyboard.press("Enter")
            if line.strip():
                await page.keyboard.insert_text(line)
        await page.wait_for_timeout(800)
        actual = (await editor.inner_text() or "")
        probe = next((ln.strip() for ln in self.body.splitlines() if ln.strip()), "")
        if probe and probe[:12] not in actual:
            raise RuntimeError(
                f"企鹅号正文写入校验失败：期望含「{probe[:12]}」，实际长度={len(actual)}"
            )
        # 旧正文残留会导致重复，这里校验开头就是第一段
        if actual.strip()[:20] != probe[:20]:
            raise RuntimeError(
                f"企鹅号正文开头异常（疑似残留旧稿）：实际开头「{actual.strip()[:20]}」"
            )
        qiehao_logger.info(f"已填写企鹅号正文，长度={len(self.body)}，编辑器实际={len(actual)}")

    async def _insert_image(self, page: Page, image_path: str) -> bool:
        """通过工具栏「插入图片」上传内文图，并以正文 `<img>` 数量回读确认。

        只看到「已选择文件」并不等于图片插入 ProseMirror；发布前必须确认
        编辑器里确实多了一张图，否则会伪装成「已插图」带病发布。
        """
        await self._close_overlays(page)
        editor = page.locator('div.ProseMirror[contenteditable="true"]').first
        before = await editor.locator("img").count() if await editor.count() else 0

        btn = page.locator("exeditor-toolbar-button").first
        if not await btn.count():
            return False
        await btn.click(force=True)
        await page.wait_for_timeout(1800)

        dialog = page.locator(".omui-dialog-wrapper.open").first
        tab = dialog.locator(".omui-tab__label:has-text('本地上传')").first
        if await tab.count():
            await tab.click(force=True)
            await page.wait_for_timeout(1500)
        file_input = dialog.locator('input[type="file"][accept*="image"]').first
        if not await file_input.count():
            return False
        await file_input.set_input_files(image_path)
        await page.wait_for_timeout(4000)

        confirm = dialog.locator("button:has-text('确认')").first
        if not await confirm.count():
            return False
        await confirm.click(force=True)
        await page.wait_for_timeout(2500)

        after = await editor.locator("img").count() if await editor.count() else 0
        if after <= before:
            qiehao_logger.warning(
                f"企鹅号内文图未真正插入编辑器（img {before}→{after}）: {image_path}"
            )
            return False
        return True

    async def _close_overlays(self, page: Page) -> None:
        """关闭已确认的弹层，避免遮挡后续控件。

        只处理企鹅号真实的 `.omui-dialog-wrapper.open` 与确定性的取消按钮；
        不再按视口坐标盲点 SVG，避免误关或把表单状态点坏。
        """
        for _ in range(3):
            wrapper = page.locator(".omui-dialog-wrapper.open").first
            if not await wrapper.count():
                return
            close = wrapper.locator(".omui-dialog-close, button:has-text('取消')").first
            if await close.count():
                try:
                    await close.click(force=True, timeout=5000)
                except Exception:
                    await page.keyboard.press("Escape")
            else:
                await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)

    async def _upload_cover(self, page: Page, cover_path: str) -> bool:
        """封面：点「更换」→ 弹层切「本地上传」→ 上传 → 确认并回读 src。

        企鹅号发文页会恢复上篇稿件，若已有自动抓取的封面，
        DOM 里是 `.cover-container img`，没有 `button[class*=addCoverBtn]`；
        继续找旧加号会误报「未找到封面添加按钮」。
        """
        await self._close_overlays(page)
        old_src = ""
        cover_img = page.locator(".cover-container img").first
        if await cover_img.count():
            old_src = (await cover_img.get_attribute("src")) or ""

        change = page.locator(
            '.cover-container .omui-thumb__action span:has-text("更换"), '
            'button[class*="addCoverBtn"], [class*="addCoverBtn"]'
        ).first
        if not await change.count():
            qiehao_logger.warning("未找到企鹅号封面添加/更换按钮")
            return False
        try:
            await change.scroll_into_view_if_needed(timeout=8000)
        except Exception:
            pass
        await change.click(force=True)
        await page.wait_for_timeout(2000)

        tab = page.locator(".omui-dialog .omui-tab__label:has-text('本地上传')").first
        if not await tab.count():
            qiehao_logger.warning("企鹅号封面弹层未出现「本地上传」")
            return False
        await tab.click(force=True)
        await page.wait_for_timeout(1800)

        file_input = page.locator(
            '.omui-dialog input[type="file"][accept*="image"]'
        ).first
        if not await file_input.count():
            qiehao_logger.warning("企鹅号封面上传控件未出现")
            return False
        await file_input.set_input_files(cover_path)
        await page.wait_for_timeout(4500)

        confirm = page.locator(".omui-dialog button:has-text('确认')").first
        if not await confirm.count():
            qiehao_logger.warning("企鹅号封面上传后未出现「确认」按钮")
            return False
        await confirm.click(force=True)
        await page.wait_for_timeout(3500)

        new_src = ""
        if await cover_img.count():
            new_src = (await cover_img.get_attribute("src")) or ""
        if not new_src or new_src == old_src:
            qiehao_logger.warning("企鹅号封面上传后 src 未变化，不能确认封面已生效")
            return False
        qiehao_logger.success(f"已上传企鹅号封面: {cover_path}")
        return True

    async def _apply_ai_declaration(self, page: Page) -> bool:
        """自主声明 → 勾选「该文章由AI生成」并回读选中态。

        页面可能已恢复上篇稿件的声明（显示为 `.omui-contentbutton`），
        也可能只有「添加内容自主声明」按钮；两种状态分别处理。
        校验只在 `.selfDeclareDialog` 内进行，避免误读封面「单图」radio。
        """
        await self._close_overlays(page)
        selected = page.locator(
            '#articlePublish-selfDeclaration .omui-contentbutton__title'
        ).first
        if await selected.count():
            picked = (await selected.inner_text() or "").strip()
            if "AI生成" in picked:
                qiehao_logger.success(f"企鹅号已保留自主声明：{picked}")
                return True

        trigger = page.locator(
            '#articlePublish-selfDeclaration button:has-text("添加内容自主声明")'
        ).first
        if not await trigger.count():
            qiehao_logger.warning("未找到企鹅号「添加内容自主声明」控件")
            return False
        await trigger.click(force=True)
        await page.wait_for_timeout(1800)

        dialog = page.locator(".selfDeclareDialog, .omui-dialog-wrapper.open").first
        label = dialog.locator("label.omui-radio:has-text('该文章由AI生成')").first
        if not await label.count():
            qiehao_logger.warning("未找到企鹅号「该文章由AI生成」选项")
            return False
        await label.click(force=True)
        await page.wait_for_timeout(800)

        picked = await dialog.evaluate(
            """el => {
                const labels = [...el.querySelectorAll('label.omui-radio')];
                const hit = labels.find(l => {
                    const r = l.querySelector('input[type=radio]');
                    return r && r.checked;
                });
                return hit ? (hit.innerText || '').trim() : '';
            }"""
        )
        if "AI生成" not in (picked or ""):
            qiehao_logger.warning(
                f"企鹅号 AI 声明未确认选中（当前选中：{picked or '无'}）"
            )
            return False
        confirm = dialog.locator("button:has-text('确认')").first
        if not await confirm.count():
            qiehao_logger.warning("企鹅号自主声明弹层未出现「确认」按钮")
            return False
        await confirm.click(force=True)
        await page.wait_for_timeout(1200)
        qiehao_logger.success(f"已勾选企鹅号自主声明：{picked}")
        return True

    async def _fill_body_with_inline_images(self, page: Page) -> None:
        """按 `[[插图N]]` 标记把 2 张 16:9 图插到母本指定位置。

        企鹅号正文是 ProseMirror；这里先按标记切段逐行输入，再把光标处的
        标记文本选中并调用工具栏插入图片。完成后必须回读：标记全部消失、
        编辑器恰好新增 len(file_list) 张图，否则中止发布。
        """
        editor = page.locator('div.ProseMirror[contenteditable="true"]').first
        if not await editor.count():
            raise RuntimeError("未找到企鹅号正文编辑器")

        parts = INLINE_MARKER_RE.split(self.body)
        images = [Path(str(p)).resolve() for p in self.file_list if Path(p).is_file()]
        expected = [int(x) for x in INLINE_MARKER_RE.findall(self.body)]
        if not expected or sorted(expected) != list(range(1, len(images) + 1)):
            raise RuntimeError(
                f"企鹅号插图标记与内文图数量不一致: markers={expected}, images={len(images)}"
            )

        await editor.click(force=True)
        await page.wait_for_timeout(250)
        await page.keyboard.press("ControlOrMeta+a")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(400)

        async def type_chunk(text: str) -> None:
            lines = text.strip("\n").splitlines()
            for idx, line in enumerate(lines):
                if idx:
                    await page.keyboard.press("Enter")
                if line.strip():
                    await page.keyboard.insert_text(line)

        # parts = [段1, 插图序号, 段2, 插图序号, ...]
        for i in range(0, len(parts), 2):
            await type_chunk(parts[i])
            if i + 1 >= len(parts):
                break
            marker_no = int(parts[i + 1])
            marker = f"[[插图{marker_no}]]"
            await page.keyboard.press("Enter")
            await page.keyboard.insert_text(marker)
            await page.wait_for_timeout(200)
            selected = await page.evaluate(
                """(needle) => {
                    const ed = document.querySelector('div.ProseMirror[contenteditable="true"]');
                    if (!ed) return false;
                    const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
                    while (walker.nextNode()) {
                        const node = walker.currentNode;
                        const pos = (node.textContent || '').indexOf(needle);
                        if (pos < 0) continue;
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
                marker,
            )
            if not selected:
                raise RuntimeError(f"企鹅号正文中未找到插图位置标记 {marker}")
            if not await self._insert_image(page, str(images[marker_no - 1])):
                raise RuntimeError(f"企鹅号插图 {marker_no} 上传失败: {images[marker_no - 1]}")

        await page.wait_for_timeout(800)
        actual = (await editor.inner_text() or "")
        left = INLINE_MARKER_RE.findall(actual)
        if left:
            raise RuntimeError(f"企鹅号插图位置标记未被图片替换: {left}")
        img_count = await editor.locator("img").count()
        if img_count != len(images):
            raise RuntimeError(
                f"企鹅号正文内文图数量不正确：期望 {len(images)} 张，实际 {img_count} 张"
            )
        probe = next(
            (ln.strip() for ln in parts[0].splitlines() if ln.strip()), ""
        )
        if probe and actual.strip()[:20] != probe[:20]:
            raise RuntimeError(
                f"企鹅号正文开头异常（疑似残留旧稿）：实际开头「{actual.strip()[:20]}」"
            )
        qiehao_logger.info(
            f"已填写企鹅号正文并按位置插入 {img_count} 张内文图，编辑器文本长度={len(actual)}"
        )

    async def _collect_published_url(self, page: Page) -> str:
        """到真实内容管理页按标题取 `page.om.qq.com/page/<id>` 公开链接。

        匹配与公开性判定放在 Python 侧做，便于离线单测；页面侧只负责
        把候选 href 与所在卡片的文本一起取回来。
        """
        try:
            await page.goto(QIEHAO_MANAGE_URL, timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(6000)
            for _ in range(4):
                candidates = await page.evaluate(
                    """() => {
                        const out = [];
                        document.querySelectorAll('a[href]').forEach(a => {
                            const href = a.getAttribute('href') || '';
                            if(!/^https?:\\/\\/page\\.om\\.qq\\.com\\/page\\//.test(href)) return;
                            const card = a.closest('.listblock, [class*=card], li, tr') || a;
                            out.push({href: href, text: (card.innerText || '') + '\\n' + (a.innerText || '')});
                        });
                        return out.slice(0, 30);
                    }"""
                )
                for item in candidates or []:
                    href = str(item.get("href") or "")
                    if not _is_public_qiehao_url(href):
                        continue
                    if _title_match(str(item.get("text") or ""), self.title):
                        return href.split("?", 1)[0]
                await page.wait_for_timeout(2500)
        except Exception as exc:
            qiehao_logger.warning(f"企鹅号公开链接采集异常: {exc}")
        return ""

    async def _wait_and_collect(self, page: Page, timeout_s: int = 900) -> str:
        """等待人工点击「发布」，然后回收公开链接。

        企鹅号发布成功后停留在内容管理页，或弹出「发布成功」提示。
        这里轮询这两个信号，命中后再去内容管理页按标题取链接。
        """
        qiehao_logger.info("⏳ 等待人工点击「发布」…")
        deadline = timeout_s
        elapsed = 0
        while elapsed < deadline:
            await page.wait_for_timeout(5000)
            elapsed += 5
            try:
                url = (page.url or "").lower()
                body = (await page.evaluate("() => document.body.innerText") or "")
            except Exception:
                continue
            if "发布成功" in body or "已发布" in body or "内容管理" in url:
                qiehao_logger.success("检测到企鹅号发布完成，开始回收公开链接")
                break
        else:
            qiehao_logger.warning(f"等待人工发布超时（{deadline} 秒），未取得公开链接")
            return ""

        url = await self._collect_published_url(page)
        if url:
            self.published_url = url
            qiehao_logger.success(f"✅ 企鹅号公开链接: {url}")
        else:
            qiehao_logger.warning("企鹅号内容管理页未取到公开链接")
        return url

    # ---------------------------------------------------------------- 主流程

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
            await page.wait_for_timeout(5000)

            if _is_login_url(page.url):
                raise RuntimeError(f"企鹅号 cookie 已失效，跳转登录页: {page.url}")

            await self._fill_title(page)
            if INLINE_MARKER_RE.search(self.body):
                # 带插图标记的正文由专用流程一次填完，避免先写一遍再清空。
                pass
            else:
                await self._fill_body(page)

            # 内文图：优先按母本 `[[插图N]]` 精确穿插；旧素材无标记时保持文末追加兼容。
            if INLINE_MARKER_RE.search(self.body):
                await self._fill_body_with_inline_images(page)
            else:
                for img in self.file_list:
                    if not img:
                        continue
                    ok = await self._insert_image(page, img)
                    if ok:
                        qiehao_logger.success(f"已插入企鹅号内文图: {img}")
                    else:
                        qiehao_logger.warning(f"企鹅号内文图插入失败（未找到图片控件）: {img}")

            # 封面
            if not self.cover_path or not Path(self.cover_path).is_file():
                raise RuntimeError("企鹅号未提供可用封面，按合规要求禁止发布")
            if not await self._upload_cover(page, self.cover_path):
                raise RuntimeError("企鹅号封面上传未通过回读校验，禁止带病发布")

            # 自主声明
            if not await self._apply_ai_declaration(page):
                raise RuntimeError("企鹅号 AI 自主声明未通过回读校验，禁止带病发布")

            await page.wait_for_timeout(2000)

            # 截图保存预览
            preview_shot = Path(self.account_file).parent / "qiehao_article_preview.png"
            await page.screenshot(path=str(preview_shot))
            qiehao_logger.info(f"📸 企鹅号发布预览截图已保存: {preview_shot}")

            if self.dry_run:
                qiehao_logger.info("🛑 【预览模式】跳过保存与发布")
                return

            # 只填内容，交给人工点「发布」
            if self.prepare_only:
                qiehao_logger.info(
                    "⏸️ 【代填模式】内容已全部填写完毕，请在浏览器窗口点击「发布」"
                    "（本进程会保持窗口并自动回收公开链接）"
                )
                await self._wait_and_collect(page)
                return

            # 操作按钮
            action_btn = None
            if self.is_draft:
                action_btn = page.locator('button:has-text("存草稿"), button:has-text("保存草稿")').first
            else:
                action_btn = page.locator('button:has-text("发布"), button:has-text("发表")').first

            if action_btn and await action_btn.count() and await action_btn.is_visible():
                await action_btn.click(force=True)
                qiehao_logger.info("已点击企鹅号发布按钮，等待平台确认…")
                await page.wait_for_timeout(6000)
                done_shot = Path(self.account_file).parent / "qiehao_article_done.png"
                await page.screenshot(path=str(done_shot))
                qiehao_logger.success(f"🎉 企鹅号发布流程结束！截图: {done_shot}")
                self.published_url = await self._collect_published_url(page)
                if self.published_url:
                    qiehao_logger.success(f"✅ 企鹅号公开链接: {self.published_url}")
                else:
                    qiehao_logger.warning("企鹅号内容管理页未取到公开链接")
            else:
                qiehao_logger.warning("未定位到企鹅号发布按钮")

        finally:
            # 代填模式下窗口要留给人工点发布，不能由脚本关闭
            if not self.prepare_only:
                await browser.close()
