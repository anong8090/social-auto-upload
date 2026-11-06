# -*- coding: utf-8 -*-
"""百度贴吧图文帖发布（Playwright），用户已授权自动发布。

用户可通过前端指定目标贴吧（fid 贴吧 ID 或 kw 贴吧名）。正式发布时点击「发布」，
按「发帖响应 tid → 标题匹配帖子列表 → 成功页 /p/ 链接」依次取证；
只有拿到可核验的 /p/ 帖子链接才向调用方回传成功证据，预览或草稿模式不点击发布。
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

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


def _normalize_title(text: str) -> str:
    """去掉空白与常见标点，用于把帖子列表标题与本次发帖标题对齐。"""
    return re.sub(r"[\s　，。！？、；：“”‘’（）()\[\]【】《》—\-·….,!?;:'\"]+", "", html_lib.unescape(text or ""))


def _tid_candidates(payload) -> set[str]:
    """从发帖响应 JSON / 表单文本中抽取帖子 tid；只接受纯数字主题 ID。"""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "ignore")
    if payload is None:
        return set()
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                key_text = str(key).lower().replace("_", "")
                # 只认主题 ID；post_id / pid 可能是楼层回复 ID，不能拼成 /p/ 链接
                if key_text in {"tid", "threadid"} and isinstance(value, (str, int)):
                    if re.fullmatch(r"\d{6,}", str(value)):
                        found.add(str(value))
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            text = html_lib.unescape(node)
            for match in re.finditer(r'(?:"|&quot;)?(?:tid|thread_id)(?:"|&quot;)?\s*[:=]\s*["\']?(\d{6,})', text, re.I):
                found.add(match.group(1))

    try:
        walk(json.loads(payload) if isinstance(payload, str) and payload.lstrip().startswith(("{", "[")) else payload)
    except (ValueError, TypeError):
        walk(payload)
    if isinstance(payload, str):
        for match in re.finditer(r'(?:"|&quot;)?(?:tid|thread_id)(?:"|&quot;)?\s*[:=]\s*["\']?(\d{6,})', html_lib.unescape(payload), re.I):
            found.add(match.group(1))
    return found


def _post_url_from_tids(tids: set[str] | None) -> str:
    """把发帖响应 tid 变成公开链接；多于一个时取数值最大的新主题 ID。"""
    valid = {str(x) for x in (tids or set()) if re.fullmatch(r"\d{6,}", str(x))}
    return f"https://tieba.baidu.com/p/{max(valid, key=int)}" if valid else ""


def _extract_post_url(text: str, *, tids: set[str] | None = None) -> str:
    """从 URL / href / Location 里取 /p/<tid>；响应 tid 证据优先。"""
    url = _post_url_from_tids(tids)
    if url:
        return url
    match = re.search(r"(?:https?://tieba\.baidu\.com)?/p/(\d{6,})", html_lib.unescape(text or ""))
    return f"https://tieba.baidu.com/p/{match.group(1)}" if match else ""


async def _response_tids(response) -> set[str]:
    """只观察与「提交帖子」相关的 POST 响应，避免把论坛列表旧帖 tid 当成本期新帖。"""
    try:
        method = (response.request.method or "").upper()
    except Exception:
        method = ""
    if method != "POST":
        return set()
    url = (response.url or "").lower()
    if not any(k in url for k in ("/c/c/", "commit", "addpost", "publish", "/post")):
        return set()
    try:
        body = await response.text()
        headers = response.headers or {}
    except Exception:
        return set()
    found = _tid_candidates(body[:2_000_000])
    location = html_lib.unescape(str(headers.get("location") or headers.get("Location") or ""))
    found.update(re.findall(r"/p/(\d{6,})", f"{html_lib.unescape(response.url)} {location}"))
    return found


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
    """百度贴吧图文帖发布；正式模式点击发布并回传 /p/ 链接。"""

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
        self.post_url = ""
        self.published_tids: set[str] = set()
        self.publish_succeeded = False
        self.publish_started = False
        self.pending_responses: set[asyncio.Task] = set()
        # 进入论坛页时已有大量旧帖链接；发帖前记录，避免把旧帖误当本期新帖
        self.known_tids: set[str] = set()
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

    async def upload(self, playwright) -> str:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=self.headless))
        context = await browser.new_context(storage_state=str(self.account_file))
        context = await set_init_script(context)
        page = await context.new_page()

        async def on_response(response):
            """捕获本次「发布」动作对应的响应 tid，而不是列表页旧帖 tid。"""
            if self.dry_run or self.is_draft or not self.publish_started:
                return
            for tid in await _response_tids(response):
                self.published_tids.add(tid)
                tieba_logger.info(f"贴吧发帖响应给出主题 ID: {tid}")

        def schedule_response(response):
            task = asyncio.create_task(on_response(response))
            self.pending_responses.add(task)
            task.add_done_callback(self.pending_responses.discard)

        page.on("response", schedule_response)
        try:
            # 进入目标贴吧发表页（含超时/元素不可见重试）
            await _retry_when_transient(
                lambda: self.goto_post_page(page),
                label="进入贴吧发表页",
            )
            # 检查登录态
            if _is_login_url(page.url):
                raise RuntimeError("百度贴吧 cookie 已失效，请重新登录")
            # 打开发表组件后立刻登记旧帖，避免发帖成功页把列表旧帖误当新帖
            self.known_tids = await self._page_tids(page)
            if self.known_tids:
                tieba_logger.info(f"已记录 {len(self.known_tids)} 个发帖前旧帖 ID，避免误取链接")
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
            await self.apply_ai_declaration(page)
            if self.dry_run or self.is_draft:
                await self.save_draft(page)
                tieba_logger.info(
                    f"✅ 百度贴吧帖子内容已自动填写完毕（标题={self.title}, fid={self.fid}, kw={self.kw}）。"
                    " 本次为预览/草稿模式，未点击「发布」。"
                )
            else:
                await self.publish_post(page)
            await self._save_evidence_screenshots(page)
        finally:
            await context.close()
            await browser.close()
        return self.post_url

    @staticmethod
    async def _page_tids(page: Page) -> set[str]:
        """读取当前论坛页已存在的帖子 ID；读取失败时返回空集合。"""
        try:
            html = await page.content()
        except Exception:
            return set()
        return set(re.findall(r"/p/(\d{6,})", html_lib.unescape(html)))

    async def _save_evidence_screenshots(self, page: Page) -> None:
        """把发布后页面截图同步到项目核验目录，截图失败不影响发布结果。"""
        shots = [
            Path(self.account_file).with_name("tieba_post_draft_saved.png"),
            Path(__file__).resolve().parents[3] / "logs/screenshots/tieba_post_published.png",
        ]
        for shot in shots:
            try:
                shot.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(full_page=True, path=str(shot))
                tieba_logger.info(f"📸 贴吧发布核验截图已保存: {shot}")
            except Exception as exc:
                tieba_logger.debug(f"贴吧截图保存失败 {shot}: {exc}")

    async def goto_post_page(self, page: Page) -> None:
        """进入目标贴吧并打开发表组件。

        新版贴吧已下线 /f/commit/post 发表页，发布器统一挂在首页：
        点击「发布到吧」(.add-btn.button-wrapper--add-post) 弹出 #publish 组件，
        在 `input[placeholder="选择吧"]` 检索并点选目标吧。

        选吧后必须验证「已选吧」状态（输入框值 + 版块分区出现），
        否则搜索面板关闭但实际未选中，发帖会落到错误吧或直接失败。

        优先 fid，若无则按 kw 检索贴吧。
        """
        forum = str(self.fid or self.kw or "")
        if not forum:
            raise RuntimeError("未指定目标贴吧（fid 或 kw）")

        # 1) 先做吧级发帖权限预检：账号被封禁时前端只弹 toast，
        #    表现为「点开发布器却找不到编辑框」，难以定位；这里提前拦。
        await self._check_forum_permission(page, forum)

        # 2) 打开首页发布器
        tieba_logger.info(f"进入贴吧首页发布器，目标吧: {forum}")
        await page.goto(TIEBA_HOME_URL, timeout=90000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        add_btn = page.locator(".add-btn.button-wrapper--add-post").first
        if not await add_btn.count():
            raise RuntimeError("未找到贴吧首页「发布到吧」按钮")
        await add_btn.click(force=True)
        try:
            await page.wait_for_selector("#publish", state="visible", timeout=20000)
        except Exception as exc:
            raise RuntimeError(f"贴吧发布组件未弹出: {exc}") from exc
        tieba_logger.info("贴吧发表组件已弹出")

        # 3) 选吧：输入吧名并点选精确匹配项
        search_box = page.locator('#publish input[placeholder="选择吧"]').first
        await search_box.click(force=True)
        await search_box.fill("")
        await search_box.type(forum, delay=120)
        await page.wait_for_timeout(2500)

        items = page.locator("#publish .forum-item")
        picked = ""
        for idx in range(await items.count()):
            name_el = items.nth(idx).locator(".name").first
            if not await name_el.count():
                continue
            name = (await name_el.inner_text() or "").strip()
            if name == forum:
                await items.nth(idx).click(force=True)
                picked = name
                break
        if not picked:
            candidates = []
            for idx in range(await items.count()):
                name_el = items.nth(idx).locator(".name").first
                if await name_el.count():
                    candidates.append((await name_el.inner_text() or "").strip())
            raise RuntimeError(
                f"贴吧发布器未找到名为「{forum}」的吧，候选: {candidates[:10]}"
            )
        tieba_logger.info(f"已选吧: {picked}")

        # 4) 验证「已选吧」状态：输入框值等于目标吧，且出现版块分区
        await page.wait_for_timeout(1200)
        state = await page.evaluate(
            """() => {
                const root = document.querySelector('#publish');
                if (!root) return {ok: false, reason: 'no_publish_root'};
                const input = root.querySelector('input[placeholder="选择吧"]');
                const text = root.innerText || '';
                return {
                    ok: true,
                    inputValue: input ? input.value : '',
                    hasSection: text.includes('版块分区'),
                };
            }"""
        )
        if not state.get("ok"):
            raise RuntimeError("贴吧发布组件消失，无法确认选吧状态")
        if state.get("inputValue") != forum:
            raise RuntimeError(
                f"贴吧选吧未生效：输入框值为「{state.get('inputValue')}」，期望「{forum}」"
            )
        if not state.get("hasSection"):
            raise RuntimeError(f"贴吧选吧未生效：未出现版块分区，目标吧「{forum}」可能未真正选中")
        tieba_logger.success(f"贴吧选吧状态已确认: {forum}")

    async def _check_forum_permission(self, page: Page, forum: str) -> None:
        """进入目标吧页面，读取发帖权限响应；账号被封禁时立即中止。

        贴吧在账号级封禁时会对目标吧返回 can_post=0，
        此时发布器打不开编辑框，必须提前拦下并给出可读原因。
        """
        captured: list[str] = []

        async def grab(response):
            try:
                captured.append(await response.text())
            except Exception:
                pass

        def on_response(response):
            if "/c/f/frs/page_pc" in (response.url or ""):
                asyncio.ensure_future(grab(response))

        page.on("response", on_response)
        try:
            await page.goto(
                f"https://tieba.baidu.com/f?kw={quote(forum)}",
                timeout=90000,
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(6000)
            # 等抓取协程跑完，避免漏掉刚返回的权限响应
            await asyncio.sleep(0.2)
        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

        for body in captured:
            can_post = re.search(r'"can_post"\s*:\s*"?(\d)"?', body)
            block_info = re.search(r'"block_info"\s*:\s*"([^"]*)"', body)
            days = re.search(r'"days_tofree"\s*:\s*(\d+)', body)
            if not can_post:
                continue
            if can_post.group(1) == "1":
                tieba_logger.info(f"贴吧「{forum}」发帖权限正常")
                return
            info = (block_info.group(1) if block_info else "") or "账号在目标吧无发帖权限"
            free = days.group(1) if days else ""
            raise RuntimeError(
                f"百度贴吧「{forum}」当前不可发帖：{info}"
                + (f"（封禁剩余 {free} 天）" if free else "")
            )
        # 响应没抓到时不阻塞发布流程，但记录为未知
        tieba_logger.warning(f"未取得贴吧「{forum}」发帖权限响应，继续尝试发布")

    async def fill_title(self, page: Page) -> None:
        """填写贴吧标题：新版发布器标题是 Quill 编辑器（data-placeholder 含「贴子标题」）。"""
        candidates = [
            page.locator('#publish .ql-editor[data-placeholder*="贴子标题"]').first,
            page.locator('#publish .ql-editor[data-placeholder*="标题"]').first,
            page.locator('#tb-editor-title .ql-editor').first,
            page.locator('.post-title-wrapper .ql-editor').first,
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
        # contenteditable 用键盘输入，确保 Quill 内部状态同步
        await page.keyboard.insert_text(self.title)
        await page.wait_for_timeout(600)
        actual = await self._editor_text(title_field)
        if self.title not in actual:
            raise RuntimeError(
                f"贴吧标题写入校验失败：期望包含「{self.title}」，实际「{actual[:40]}」"
            )
        tieba_logger.info(f"已填写帖子标题: {self.title}")

    async def fill_body(self, page: Page) -> None:
        """填写贴吧正文：新版发布器正文是 Quill 编辑器（data-placeholder 含「正文」）。"""
        candidates = [
            page.locator('#publish .ql-editor[data-placeholder*="正文"]').first,
            page.locator('#tb-editor-content .ql-editor').first,
            page.locator('.post-editor-content .ql-editor').first,
            page.locator('.ueditor-container iframe').first,
            page.locator('textarea[placeholder*="正文"]').first,
        ]
        editor = None
        editor_type = None
        for loc in candidates:
            try:
                if await loc.count() and await loc.is_visible():
                    editor = loc
                    tag = await loc.evaluate("el => el.tagName")
                    cls = (await loc.get_attribute("class")) or ""
                    if tag == "IFRAME" or "ueditor" in cls.lower():
                        editor_type = "iframe"
                    else:
                        editor_type = "contenteditable"
                    break
            except Exception:
                continue
        if not editor:
            raise RuntimeError("未找到百度贴吧帖子正文编辑器")

        if editor_type == "iframe":
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

        # contenteditable Quill：逐行键盘输入，空行用 Enter
        await editor.click(force=True)
        await page.wait_for_timeout(300)
        for line_no, line in enumerate(self.body.splitlines()):
            if line_no:
                await page.keyboard.press("Enter")
            if line.strip():
                await page.keyboard.insert_text(line)
        await page.wait_for_timeout(800)
        actual = await self._editor_text(editor)
        probe = next((ln.strip() for ln in self.body.splitlines() if ln.strip()), "")
        if probe and probe[:12] not in actual:
            raise RuntimeError(
                f"贴吧正文写入校验失败：期望包含「{probe[:12]}」，实际长度={len(actual)}"
            )
        tieba_logger.info(f"已填写正文，长度={len(self.body)}，编辑器实际={len(actual)}")

    @staticmethod
    async def _editor_text(loc) -> str:
        """读取 contenteditable 编辑器当前可见文本。"""
        try:
            return (await loc.inner_text()) or ""
        except Exception:
            return ""

    async def apply_ai_declaration(self, page: Page) -> None:
        """勾选「内容声明 -> 贴子含AI内容」。

        新版发布器用 radio 实现声明：range-radio0=贴子含AI内容，
        range-radio1=贴子原创声明。radio input 与其 <label for> 都是空壳且不可见，
        真正可点的是同一组里的 <span class="label"> 文字。

        必须校验 checked 状态；找不到控件时只告警，不谎报已勾选。
        """
        ai_radio = page.locator('#publish input#range-radio0').first
        if not await ai_radio.count():
            tieba_logger.warning("未找到贴吧「贴子含AI内容」声明控件，请人工确认")
            return
        try:
            if not await ai_radio.is_checked():
                text_label = page.locator(
                    '#publish .t-radio-group-item:has-text("贴子含AI内容") span.label'
                ).first
                if await text_label.count() and await text_label.is_visible():
                    await text_label.scroll_into_view_if_needed(timeout=5000)
                    await text_label.click(force=True, timeout=8000)
                else:
                    await page.locator(
                        '#publish .t-radio-group-item:has-text("贴子含AI内容")'
                    ).first.click(force=True, timeout=8000)
                await page.wait_for_timeout(800)
            if await ai_radio.is_checked():
                tieba_logger.success("已勾选贴吧内容声明：贴子含AI内容")
            else:
                tieba_logger.warning("贴吧「贴子含AI内容」声明点击后未呈选中态，请人工确认")
        except Exception as exc:
            tieba_logger.warning(f"勾选贴吧 AI 内容声明失败: {exc}")

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

    def _forum_url(self) -> str:
        """当前目标贴吧论坛页；fid 参数沿用既有实现，按 kw 语义拼装。"""
        return f"https://tieba.baidu.com/f?kw={quote(str(self.fid or self.kw or ''))}"

    async def _find_post_url_in_page(self, page: Page) -> str:
        """优先用本期标题匹配 /p/ 链接；也允许地址栏直接给出 /p/ 帖子页。"""
        if "/p/" in (page.url or ""):
            url = _extract_post_url(page.url)
            tid = url.rsplit("/", 1)[-1]
            if url and tid not in self.known_tids:
                return url

        title_key = _normalize_title(self.title)
        try:
            anchors = await page.locator('a[href*="/p/"]').all()
        except Exception:
            anchors = []
        for anchor in anchors:
            try:
                href = await anchor.get_attribute("href") or ""
                text = (await anchor.inner_text()) or ""
                card = (await anchor.evaluate(
                    """el => {
                        const box = el.closest('li, tr, [class*="item"], [class*="card"], [class*="thread"]');
                        return (box || el.parentElement || el).innerText || '';
                    }"""
                )) or ""
            except Exception:
                continue
            url = _extract_post_url(href)
            if not url:
                continue
            tid = url.rsplit("/", 1)[-1]
            if tid in self.known_tids:
                continue
            haystack = _normalize_title(f"{text}\n{card}\n{unquote(href)}")
            if title_key and title_key in haystack:
                return url
        return ""

    async def _refresh_forum(self, page: Page, *, search: bool = False) -> None:
        """发帖成功后重新打开论坛/吧内搜索页，让新帖链接从后台列表出现。"""
        forum = str(self.fid or self.kw or "")
        if search:
            params = (
                f"ie=utf-8&qw={quote(self.title)}&kw={quote(forum)}"
                "&only_thread=1&sm=1&rn=20&pn=1"
            )
            target = f"https://tieba.baidu.com/f/search/res?{params}"
        else:
            target = self._forum_url()
        try:
            await page.goto(target, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            if not search:
                latest = page.locator("text=最新").first
                if await latest.count() and await latest.is_visible():
                    await latest.click(force=True)
                    await page.wait_for_timeout(2000)
            tieba_logger.info(f"贴吧链接回捞页面: {page.url}")
        except Exception as exc:
            tieba_logger.warning(f"贴吧链接回捞页面打开失败: {exc}")

    async def publish_post(self, page: Page) -> str:
        """点击「发布」，把响应 tid / 标题匹配 /p/ 链接作为成功证据回传。"""
        btn = None
        for sel in (
            '#publish .issue-btn.button-wrapper--primary',
            '#publish .footer-safe-issue .issue-btn',
            'div.issue-btn.button-wrapper--primary',
            '.publisher-container .issue-btn',
            'button:has-text("发布")',
            'div[class*="submit"]:has-text("发布")',
        ):
            loc = page.locator(sel).first
            try:
                if await loc.count() and await loc.is_visible():
                    btn = loc
                    break
            except Exception:
                continue
        if not btn:
            raise RuntimeError("未找到贴吧「发布」按钮")

        # 只把点击发布之后的响应 tid 作为证据；此前上传图片/填表的 POST 一律不算
        self.published_tids.clear()
        self.publish_started = True
        await btn.click(force=True)
        tieba_logger.info("已点击贴吧「发布」，等待平台确认…")

        for attempt in range(40):
            await page.wait_for_timeout(1000)

            # 等待尚未完成的 response.text() 任务，避免响应 tid 因异步调度丢失
            if self.pending_responses:
                await asyncio.gather(*tuple(self.pending_responses), return_exceptions=True)

            # 发帖接口响应 tid 是最直接的证据，不依赖页面是否跳转
            url = _post_url_from_tids(self.published_tids)
            if url:
                self.post_url = url
                tieba_logger.success(f"✅ 从发帖响应取得贴吧帖子链接: {self.post_url}")
                return self.post_url

            try:
                if await page.locator("text=/发表成功|发布成功|发送成功/").count():
                    self.publish_succeeded = True
            except Exception:
                pass

            url = await self._find_post_url_in_page(page)
            if url:
                self.post_url = url
                tieba_logger.success(f"✅ 从成功页取得本期贴吧帖子链接: {self.post_url}")
                return self.post_url

            if self.publish_succeeded and attempt in (4, 12):
                await self._refresh_forum(page)
            elif self.publish_succeeded and attempt == 20:
                await self._refresh_forum(page, search=True)

        if self.publish_succeeded:
            raise RuntimeError(
                f"贴吧提示发帖成功，但响应 tid={sorted(self.published_tids)}、成功页与列表页均无本期 /p/ 帖子链接，不能标记已发布"
            )
        raise RuntimeError(f"贴吧点击发布后未确认成功，当前页面: {page.url}")

    async def save_draft(self, page: Page) -> None:
        """预览/草稿模式：只填表与截图，不执行任何对外发表动作。

        贴吧发表页当前主要露出「发布」按钮，草稿落库链路不可信；
        因此预览/草稿只保留本地证据截图，不宣称已保存官方草稿。
        """
        try:
            preview = Path(self.account_file).with_name("tieba_post_draft_pending.png")
            await page.screenshot(full_page=True, path=str(preview))
            tieba_logger.info(f"📸 贴吧发帖凭证截图已保存: {preview}")
        except Exception as exc:
            tieba_logger.debug(f"草稿截图保存失败: {exc}")

        tieba_logger.warning(
            "⚠️ 预览/草稿模式只完成标题、正文与封面填写并保留截图，"
            "不点击「发布」、不宣称保存官方草稿；正式发布请走非草稿链路。"
        )

    async def main(self) -> str:
        async with async_playwright() as playwright:
            return await self.upload(playwright)
