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
    """构建浏览器启动参数。

    ⚠️ 微信公众平台必崩坑（2026-11-09 实测）：Playwright 自带的 Chromium
    内核打开 mp.weixin.qq.com 会稳定触发 `Page.goto: Page crashed`，
    空 context 也崩，与 cookie 文件无关；换系统 Chrome
    (`channel="chrome"`) 则正常返回 200。四种启动参数变体
    （UA / --single-process / 关闭站点隔离 / 禁用 JS）都救不了 bundled 内核。
    所以这里对微信域名固定优先走系统 Chrome，失败再回退到默认内核，
    并保留 user 配置的 executable_path 优先级。

    另外：用户明确要求「走无头浏览器模式，不要干扰工作」，
    因此扫码登录分支也强制 headless=True，绝不弹出可见窗口。
    """
    options = {
        "headless": True,  # 强制无头：公众号任何路径都不弹浏览器
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
    # 微信站点与 bundled Chromium 不兼容；优先系统 Chrome。
    # channel 与 executable_path 互斥，两者都设会让 Playwright 报错。
    options["channel"] = "chrome"
    return options


async def _launch_wechat_browser(playwright):
    """启动微信专用浏览器；系统 Chrome 不可用时降级到 bundled 内核。"""
    try:
        return await playwright.chromium.launch(**_build_launch_kwargs(True))
    except Exception as exc:
        wechat_mp_logger.warning(
            f"系统 Chrome 不可用（{exc}），降级到 Playwright 自带内核；"
            "微信页面仍可能 Page crashed"
        )
        fallback = _build_launch_kwargs(True)
        fallback.pop("channel", None)
        return await playwright.chromium.launch(**fallback)


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
        browser = await _launch_wechat_browser(playwright)
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
    """无头扫码登录并保存微信公众号 cookie。

    无头模式下二维码不在屏幕上，所以把二维码**截图落盘**，由人工扫码；
    绝不弹出可见浏览器（用户明确要求不干扰工作）。截图路径通过日志给出。
    """
    async with async_playwright() as playwright:
        browser = await _launch_wechat_browser(playwright)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto(WECHAT_MP_LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
        qr_path = Path(account_file).with_suffix(".login_qr.png")
        qr_locator = page.locator("img.login__type__container__scan__qrcode")
        # 登录页首屏可能先渲染深色导航和空白区域；必须等二维码图片真正加载，
        # 再裁二维码元素，否则交付给人工的是一张空白图，扫码也不会落到本进程。
        await qr_locator.wait_for(state="visible", timeout=30000)
        await page.wait_for_function(
            """() => {
                const img = document.querySelector('img.login__type__container__scan__qrcode');
                return img && img.complete && img.naturalWidth > 100;
            }""",
            timeout=30000,
        )

        async def _save_qr():
            await qr_locator.screenshot(path=str(qr_path))

        try:
            await _save_qr()
            wechat_mp_logger.info(
                f"无头登录二维码截图已保存：{qr_path}，请用微信扫码并在手机端确认"
            )
        except Exception as exc:
            await context.close()
            await browser.close()
            raise RuntimeError(f"二维码截图失败：{exc}") from exc

        # 二维码会过期刷新，等待扫码期间定期重截同一个有效二维码元素，
        # 保证人工拿到的是当前有效二维码，且始终由本进程保存登录态。
        async def _refresh_qr():
            elapsed = 0
            while elapsed < 200_000:
                await page.wait_for_timeout(10_000)
                elapsed += 10_000
                try:
                    if await qr_locator.count():
                        await _save_qr()
                except Exception:
                    return

        refresh_task = asyncio.create_task(_refresh_qr())
        try:
            ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
        finally:
            refresh_task.cancel()
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError(
                f"微信公众号登录超时：未检测到扫码确认；最新二维码在 {qr_path}，请重试"
            )
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
        wechat_mp_logger.error(
            "cookie 文件不存在或已失效；将在无头模式下生成二维码截图，"
            "请按日志中的截图路径扫码（不弹出可见浏览器）"
        )
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
        file_list=None,
        existing_draft_title="",
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
        self.file_list = [
            str(Path(p).resolve()) for p in (file_list or [])
            if p and Path(p).is_file()
        ]
        # 未单传封面时，把第一张正文图作为「从正文选择」的封面来源。
        cover = str(Path(cover_path).resolve()) if cover_path and Path(cover_path).is_file() else ""
        self.cover_path = cover or (self.file_list[0] if self.file_list else "")
        self.existing_draft_title = (existing_draft_title or "").strip()
        self._draft_patch_mode = bool(self.existing_draft_title)
        # 用户明确要求全部走无头，dry_run 也不弹可见窗口
        self.headless = True

    async def main(self) -> None:
        async with async_playwright() as playwright:
            await self.upload(playwright)

    @staticmethod
    async def _dismiss_education_dialog(page: Page) -> None:
        """关闭新版编辑器的教育提示/引导弹窗，避免遮挡标题与正文输入。"""
        wrappers = page.locator(
            ".weui-desktop-dialog__wrp.education-dialog, "
            ".weui-desktop-dialog__wrp:has(.weui-desktop-dialog__hd)"
        )
        try:
            count = await wrappers.count()
        except Exception:
            return
        for idx in range(count):
            wrapper = wrappers.nth(idx)
            try:
                if not await wrapper.is_visible():
                    continue
            except Exception:
                continue
            close_selectors = (
                'button:has-text("我知道了"), button:has-text("知道了"), '
                'button:has-text("关闭"), .weui-desktop-dialog__close, '
                '[aria-label="关闭"], [aria-label="Close"]'
            )
            closer = wrapper.locator(close_selectors).first
            try:
                if await closer.count() and await closer.is_visible():
                    await closer.click(timeout=3000)
                else:
                    await wrapper.evaluate(
                        """(el) => {
                            el.style.display = 'none';
                            el.setAttribute('data-agent-dismissed', '1');
                        }"""
                    )
            except Exception:
                try:
                    await wrapper.evaluate(
                        """(el) => {
                            el.style.display = 'none';
                            el.setAttribute('data-agent-dismissed', '1');
                        }"""
                    )
                except Exception:
                    pass
        await page.wait_for_timeout(500)

    @staticmethod
    async def _body_editor(page: Page):
        """定位正文 ProseMirror；排除标题编辑框与隐藏的旧版镜像输入。"""
        editor = page.locator(
            '#edui1_iframeholder .rich_media_content .ProseMirror[contenteditable="true"], '
            '#ueditor_0 .rich_media_content .ProseMirror[contenteditable="true"], '
            '#js_editor_area .ProseMirror[contenteditable="true"], '
            '#js_editor_area .rich_media_content[contenteditable="true"]'
        ).first
        if not (await editor.count() and await editor.is_visible()):
            editor = page.locator(
                '.ProseMirror[contenteditable="true"]:not([data-placeholder*="标题"]):visible'
            ).first
        if not (await editor.count() and await editor.is_visible()):
            raise RuntimeError("未找到微信公众号正文编辑器")
        return editor

    @staticmethod
    async def _editor_image_count(editor) -> int:
        """只统计正文里真正上传成功、带 src 的图片；忽略 ProseMirror 占位符。"""
        return int(await editor.evaluate(
            """(el) => [...el.querySelectorAll('img')].filter(img =>
                (img.getAttribute('src') || '').trim() &&
                !img.classList.contains('ProseMirror-separator')
            ).length"""
        ))

    @staticmethod
    async def _cover_ready(page: Page) -> bool:
        """封面区域 background-image 必须真实非空，不把 url(\"\") 当已设封面。"""
        return bool(await page.evaluate(r"""() => {
            const nodes = [...document.querySelectorAll(
                '.js_cover_preview_new,.js_cover_preview_square,' +
                '.card_appmsg_thumb,.weui-desktop-publish__cover__thumb'
            )];
            return nodes.some((el) => {
                const bg = getComputedStyle(el).backgroundImage || '';
                if (!bg || bg === 'none') return false;
                if (/url\(\s*["']?\s*["']?\s*\)/.test(bg)) return false;
                if (bg.includes('url("")') || bg.includes("url('')")) return false;
                return true;
            });
        }"""))

    @staticmethod
    async def _click_or_dom(page: Page, selector: str, timeout: int = 8000) -> bool:
        """先正常点击，遇到新版编辑器遮挡/视口外时由元素自身派发 click。"""
        loc = page.locator(selector).first
        if not await loc.count():
            return False
        try:
            await loc.evaluate(
                "(el) => el.scrollIntoView({block: 'center', inline: 'center'})"
            )
            await loc.click(timeout=timeout)
            return True
        except Exception:
            await loc.evaluate("(el) => el.click()")
            return True

    async def _open_existing_draft(self, page: Page, context) -> Page:
        """从 type=77 草稿箱精确打开本期既有草稿，避免重跑时新建重复草稿。"""
        draft_link = page.locator(
            'a[href*="action=list_card"][href*="type=77"], '
            'a[href*="action=list_card"]'
        ).first
        href = await draft_link.get_attribute("href") or "" if await draft_link.count() else ""
        if href.startswith("/"):
            href = "https://mp.weixin.qq.com" + href
        elif href and not href.startswith("http"):
            href = ""
        if not href:
            raise RuntimeError("未定位到公众号 type=77 草稿箱链接，无法修补既有草稿")
        await page.goto(href, timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

        cards = page.locator(".publish_card_container").filter(
            has_text=self.existing_draft_title
        )
        if await cards.count():
            card = cards.first
        else:
            # 草稿箱标题可能把空格渲染成 NBSP；按归一化文本再兜底一次。
            normalized_title = re.sub(
                r"\s+", "", self.existing_draft_title.replace("\xa0", " ")
            )
            card = None
            for candidate in await page.locator(".publish_card_container").all():
                raw = await candidate.inner_text()
                normalized_raw = re.sub(r"\s+", "", raw.replace("\xa0", " "))
                if normalized_title in normalized_raw:
                    card = candidate
                    break
            if card is None:
                raise RuntimeError(
                    f"公众号草稿箱未命中标题「{self.existing_draft_title}」，不得新建重复草稿"
                )

        await card.hover()
        await page.wait_for_timeout(800)
        edit_wrap = card.locator(
            ".weui-desktop-card__action .weui-desktop-tooltip__wrp"
        ).filter(has_text="编辑").first
        edit_icon = edit_wrap.locator("a.weui-desktop-icon-btn").first
        if not (await edit_icon.count() and await edit_icon.is_visible()):
            raise RuntimeError("公众号既有草稿的「编辑」入口不可见，停止修补")
        async with context.expect_page(timeout=15000) as popup_info:
            await edit_icon.click(timeout=15000)
        editor_page = await popup_info.value
        await editor_page.wait_for_load_state("domcontentloaded", timeout=30000)
        await editor_page.wait_for_timeout(5000)
        await self._dismiss_education_dialog(editor_page)
        appmsgid = ""
        match = re.search(r"[?&]appmsgid=(\d+)", editor_page.url or "")
        if match:
            appmsgid = match.group(1)
        wechat_mp_logger.info(
            f"已进入既有公众号草稿编辑页: appmsgid={appmsgid or 'unknown'}"
        )
        return editor_page

    async def _insert_inline_images(self, page: Page, editor) -> int:
        """把当日配图真正插入正文；已有图片时不重复上传。"""
        count = await self._editor_image_count(editor)
        if count:
            wechat_mp_logger.info(f"公众号正文已有 {count} 张图片，跳过重复插图")
            return count
        images = [p for p in (self.file_list or ([self.cover_path] if self.cover_path else [])) if p]
        if not images:
            raise RuntimeError("公众号保存草稿前必须提供正文配图/封面图")
        images = images[:2]
        file_input = page.locator(
            '#js_editor_insertimage input[type="file"][name="file"][accept*="image"], '
            'input[type="file"][name="file"][accept*="image"]'
        ).first
        if not await file_input.count():
            raise RuntimeError("未定位到公众号编辑器本地图片上传 input")
        await file_input.set_input_files(images, timeout=30000)
        for _ in range(30):
            await page.wait_for_timeout(1000)
            count = await self._editor_image_count(editor)
            if count:
                break
        if not count:
            raise RuntimeError("公众号图片上传后正文仍无有效 img，不得保存缺图草稿")
        wechat_mp_logger.info(f"公众号正文插图完成，有效图片 {count} 张")
        return count

    async def _set_cover_from_body(self, page: Page) -> None:
        """用正文图设置封面，必须走「从正文选择 → 下一步 → 确认」并回读封面。"""
        if await self._cover_ready(page):
            wechat_mp_logger.info("公众号封面已存在，跳过重复设封面")
            return
        if not await self._click_or_dom(
            page,
            '#js_cover_area .select-cover__btn.js_cover_btn_area, '
            '.select-cover__btn.js_cover_btn_area',
        ):
            raise RuntimeError("未定位到公众号「拖拽或选择封面」入口")
        await page.wait_for_timeout(1200)
        if not await self._click_or_dom(
            page,
            '.js_cover_null_pop .js_selectCoverFromContent:visible, '
            '.js_selectCoverFromContent:visible',
        ):
            raise RuntimeError("未定位到可见的「从正文选择」入口")
        await page.wait_for_timeout(2200)
        picker_text = await page.evaluate("() => document.body.innerText")
        if "正文中无可用做封面的图片" in picker_text:
            raise RuntimeError("公众号封面弹层提示正文无可用图片，停止设封面")
        if not await self._click_or_dom(
            page, 'li.appmsg_content_img_item .appmsg_content_img.cover'
        ):
            raise RuntimeError("公众号「选择图片」弹层里没有正文图可选")
        await page.wait_for_timeout(800)
        selected = page.locator('li.appmsg_content_img_item.selected')
        if not await selected.count():
            raise RuntimeError("公众号正文封面图未被选中，不继续点「下一步」")
        if not await self._click_or_dom(
            page,
            '.weui-desktop-dialog_img-picker:visible '
            'button.weui-desktop-btn_primary:not(.weui-desktop-btn_disabled):has-text("下一步")',
        ):
            raise RuntimeError("公众号封面「下一步」按钮未启用，停止设封面")
        await page.wait_for_timeout(3000)
        if not await self._click_or_dom(
            page,
            '.weui-desktop-dialog__wrp:visible:has-text("编辑封面") '
            'button.weui-desktop-btn_primary:has-text("确认")',
        ):
            raise RuntimeError("未定位到公众号「编辑封面」的确认按钮")
        for _ in range(15):
            await page.wait_for_timeout(1000)
            if await self._cover_ready(page):
                break
        if not await self._cover_ready(page):
            raise RuntimeError("公众号封面确认后 background-image 仍为空，不得保存缺封面草稿")
        wechat_mp_logger.info("公众号封面已从正文图设置并回读为非空")

    async def _assert_media_ready(self, page: Page, editor) -> None:
        images = await self._editor_image_count(editor)
        cover = await self._cover_ready(page)
        if not images or not cover:
            raise RuntimeError(
                f"公众号保存前图文闸门未通过：正文图片={images}，封面非空={cover}"
            )
        wechat_mp_logger.info(
            f"公众号保存前图文核验通过：正文图片={images}，封面非空={cover}"
        )

    async def _save_or_publish(self, page: Page) -> None:
        """只保存草稿；仅当调用方显式把 is_draft=False 才进入既有发表链路。"""
        preview_shot = Path(self.account_file).parent / "wechat_mp_draft_preview.png"
        await page.screenshot(path=str(preview_shot))
        wechat_mp_logger.info(f"📸 公众号图文预览截图已保存: {preview_shot}")

        if self.dry_run:
            wechat_mp_logger.info("🛑 【预览模式】跳过保存与发布")
            return

        if self.is_draft:
            save_draft_btn = page.locator(
                '#js_submit button:has-text("保存为草稿"), '
                'button:has-text("保存为草稿"), '
                'a:has-text("保存为草稿")'
            ).first
            if not (await save_draft_btn.count() and await save_draft_btn.is_visible()):
                raise RuntimeError("未定位到可见的「保存为草稿」按钮")
            # 新版编辑器的底部操作栏可能渲染在 900px 视口之外，
            # Playwright 的 scroll_into_view_if_needed 仍判定 outside viewport；
            # 显式滚到中间后由按钮自身派发 click，绝不调用「发表」选择器。
            try:
                await save_draft_btn.evaluate(
                    "(el) => el.scrollIntoView({block: 'center', inline: 'center'})"
                )
                await save_draft_btn.click(timeout=8000)
            except Exception:
                await save_draft_btn.evaluate("(el) => el.click()")
            wechat_mp_logger.info("已点击「保存为草稿」")
            await page.wait_for_timeout(4000)
            done_shot = Path(self.account_file).parent / "wechat_mp_draft_saved.png"
            await page.screenshot(path=str(done_shot))
            wechat_mp_logger.success(f"🎉 微信公众号已存入官方草稿箱！截图: {done_shot}")
            return

        send_btn = page.locator(
            '#js_send .mass_send, #js_send button:has-text("发表")'
        ).first
        if not (await send_btn.count() and await send_btn.is_visible()):
            raise RuntimeError("未定位到可见的「发表」按钮")
        await send_btn.click()
        await page.wait_for_timeout(2000)

        confirm = page.locator(
            '.mass-send__footer .weui-desktop-btn_primary:has-text("发表"), '
            '.weui-desktop-dialog button.weui-desktop-btn_primary:has-text("发表")'
        ).first
        if await confirm.count() and await confirm.is_visible():
            await confirm.click()
        await page.wait_for_timeout(2000)

        continue_btn = page.locator('button:has-text("继续发表")').first
        if await continue_btn.count() and await continue_btn.is_visible():
            await continue_btn.click()

        # 管理员扫码确认无法由 DOM 完成，遇到就算未完成，交给上层标记失败
        if await page.locator('text=扫描二维码, text=管理员确认').count():
            raise RuntimeError("微信公众号发表需要管理员扫码确认，无法自动完成")

        await page.wait_for_timeout(3000)
        wechat_mp_logger.success("已点击「发表」链路")

    async def upload(self, playwright) -> None:
        browser = await _launch_wechat_browser(playwright)
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

            if self.existing_draft_title:
                # 修补既有草稿：只打开本期标题对应的 appmsgid，绝不新建重复草稿。
                page = await self._open_existing_draft(page, context)
                self._draft_patch_mode = True
            else:
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

            # 新版公众号编辑器首次进入会弹出「教育提示」对话框，遮住标题输入框；
            # 不关掉会让 Playwright 的 click 一直被弹窗 intercept。
            await self._dismiss_education_dialog(page)

            # 修补既有草稿时标题已正确落库，不重写，避免触发编辑器脏数据。
            patch_mode = bool(getattr(self, "_draft_patch_mode", False))

            # 填充标题：新版可见标题是 ProseMirror 覆盖层，隐藏 textarea#title 只是镜像
            title_input = page.locator(
                '.title-editor__input .ProseMirror[contenteditable="true"], '
                '.title-editor-overlay .ProseMirror[contenteditable="true"], '
                '[contenteditable="true"][data-placeholder*="标题"]'
            ).first

            if patch_mode:
                wechat_mp_logger.info(
                    f"修补既有草稿，保留原标题：{self.existing_draft_title}"
                )
            elif await title_input.count() and await title_input.is_visible():
                await title_input.wait_for(state="visible", timeout=15000)
                await title_input.click()
                await page.keyboard.press("ControlOrMeta+A")
                await page.keyboard.insert_text(self.title)
                await page.evaluate(
                    """(t) => {
                        const el = document.querySelector('#title');
                        if (!el) return;
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLTextAreaElement.prototype, 'value'
                        )?.set;
                        if (setter) setter.call(el, t);
                        else el.value = t;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    self.title,
                )
                wechat_mp_logger.info(f"已填写文章标题: {self.title}")
            else:
                legacy_title = page.locator(
                    'textarea#title:visible, '
                    'input[placeholder*="标题"]:visible, '
                    'textarea[placeholder*="标题"]:visible'
                ).first
                if await legacy_title.count():
                    await legacy_title.fill(self.title)
                    wechat_mp_logger.info(f"已填写文章标题（旧版编辑器）: {self.title}")
                else:
                    raise RuntimeError("未找到微信公众号可见标题编辑器")

            # 修补既有草稿时保留原标题与原作者，避免覆盖用户后台已确认的内容。
            if not patch_mode:
                author_input = page.locator('#author, input[placeholder*="作者"]').first
                if await author_input.count():
                    await author_input.fill(self.author)

            # 正文：修补既有草稿时不重写，避免把刚上传的图片块整段冲掉。
            editor = await self._body_editor(page)
            if patch_mode:
                got = len(((await editor.inner_text()) or "").strip())
                if got < max(80, int(len(self.body) * 0.6)):
                    raise RuntimeError(
                        f"既有公众号草稿正文异常：期望约 {len(self.body)} 字，实际 {got} 字"
                    )
                wechat_mp_logger.info(f"修补既有草稿，保留原文，读回 {got} 字")
                await editor.click(position={"x": 20, "y": 20})
            else:
                await editor.click()
                await page.wait_for_timeout(500)
                await editor.evaluate(
                    """(el, text) => {
                        const esc = (s) => s.replace(/&/g, '&amp;')
                            .replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        const lines = text.split(/\\r?\\n/);
                        el.innerHTML = lines.map(p =>
                            p.trim() ? '<p>' + esc(p) + '</p>' : '<p><br></p>'
                        ).join('');
                        el.dispatchEvent(new InputEvent('input', {
                            bubbles: true, inputType: 'insertHTML'
                        }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    self.body,
                )
                await page.wait_for_timeout(800)
                got = len(((await editor.inner_text()) or "").strip())
                if got < max(80, int(len(self.body) * 0.6)):
                    raise RuntimeError(
                        f"公众号正文注入校验失败：期望约 {len(self.body)} 字，实际 {got} 字"
                    )
                wechat_mp_logger.info(f"正文注入完成，长度={len(self.body)}，读回 {got} 字")

            # 图文硬闸门：上传器必须真正插图、设置非空封面后才能保存。
            await self._insert_inline_images(page, editor)
            await self._set_cover_from_body(page)
            await self._assert_media_ready(page, editor)
            await page.wait_for_timeout(1200)
            await self._save_or_publish(page)

        finally:
            await browser.close()
