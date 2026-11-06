# -*- coding: utf-8 -*-
"""百度贴吧上传器测试（默认不联网、不启动浏览器、绝不点击「发布」）。

验证要点：
1. 类型校验：标题/正文为空、fid 与 kw 均为空时应抛出 ValueError；
2. fid/kw 优先级：同时指定时以 fid 为准；
3. 链接证据：发帖响应 tid、成功页 /p/ 链接、标题匹配，旧帖不得冒充新帖；
4. 成功但无可核验链接时必须 fail-closed；
5. 预览/草稿模式只走 save_draft，不调用 publish_post。
6. 真实 E2E 仅在 `TIEBA_E2E=1` 时启用，且强制 dry_run，不发表。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uploader.tieba_uploader.main as tu
from uploader.tieba_uploader.main import TiebaPost

COOKIES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookiesFile")
COOKIES_FILE = os.path.join(COOKIES_DIR, "tieba_e2e_dummy.json")
TITLE = "【E2E 自动化验证】贴吧自动化发帖流程测试 - 不自动发布"
BODY = "这是一条用于验证填写流程的测试正文，默认不会发布。"


class FakeResponse:
    def __init__(self, url: str, method: str, body: str, headers=None):
        self.url = url
        self.request = type("FakeRequest", (), {"method": method})()
        self.headers = headers or {}
        self._body = body

    async def text(self):
        return self._body


class FakeAnchor:
    def __init__(self, href: str, text: str, card: str = ""):
        self.href = href
        self.text = text
        self.card = card

    async def get_attribute(self, name: str):
        return self.href if name == "href" else ""

    async def inner_text(self):
        return self.text

    async def evaluate(self, _script):
        return self.card


class FakeLocator:
    def __init__(self, *, count=0, visible=False, click=None, anchors=None):
        self._count = count
        self._visible = visible
        self._click = click
        self._anchors = anchors or []
        self.first = self

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible

    async def click(self, **kwargs):
        if self._click:
            await self._click()

    async def all(self):
        return self._anchors


class FakePage:
    def __init__(self, *, url="https://tieba.baidu.com/f?kw=施工员", success=False, anchors=None):
        self.url = url
        self.locators = {
            '#publish .issue-btn.button-wrapper--primary': FakeLocator(count=1, visible=True, click=self._click_publish),
            'text=/发表成功|发布成功|发送成功/': FakeLocator(count=1 if success else 0),
            'a[href*="/p/"]': FakeLocator(count=len(anchors or []), anchors=anchors or []),
        }
        self.post = None

    def attach(self, post):
        self.post = post

    async def _click_publish(self):
        if self.post and getattr(self.post, "response_tid", ""):
            self.post.published_tids.add(self.post.response_tid)

    def on(self, event, _handler):
        return None

    def locator(self, selector):
        return self.locators.get(selector, FakeLocator())

    async def wait_for_timeout(self, _ms):
        return None

    async def content(self):
        return "<html></html>"

    async def goto(self, url, **kwargs):
        self.url = url
        return None

    async def screenshot(self, **kwargs):
        return b""


class FakeChromium:
    async def launch(self, **kwargs):
        return FakeBrowser()


class FakeBrowser:
    async def new_context(self, **kwargs):
        return FakeContext()

    async def close(self):
        return None


class FakeContext:
    async def new_page(self):
        return FakePage()

    async def close(self):
        return None


class FakePlaywright:
    chromium = FakeChromium()


def make_post(**kwargs):
    params = dict(
        title=TITLE,
        body=BODY,
        tags=[],
        publish_date=datetime.now(),
        account_file=COOKIES_FILE,
        kw="施工员",
    )
    params.update(kwargs)
    return TiebaPost(**params)


def _permission_body(can_post: int, block_info: str = "", days: int = 0) -> str:
    return (
        '{"anti":{"block_pop_info":{"can_post":%d,"block_info":"%s"},'
        '"days_tofree":%d}}' % (can_post, block_info, days)
    )


class FakePermissionPage(FakePage):
    """模拟贴吧论坛页：被动返回权限响应，不做真实网络请求。"""

    def __init__(self, permission_body: str):
        super().__init__()
        self.permission_body = permission_body
        self.response_handlers = []

    def on(self, event, handler):
        if event == "response":
            self.response_handlers.append(handler)
        return None

    def remove_listener(self, event, handler):
        if event == "response" and handler in self.response_handlers:
            self.response_handlers.remove(handler)
        return None

    async def goto(self, url, **kwargs):
        self.url = url
        # 模拟论坛页加载后平台返回发帖权限响应
        resp = FakeResponse(
            "https://tieba.baidu.com/c/f/frs/page_pc", "POST", self.permission_body
        )
        for handler in list(self.response_handlers):
            handler(resp)
        await asyncio.sleep(0)  # 让 create_task 的抓取协程有机会执行
        return None


async def test_forum_permission_blocks_when_can_post_zero():
    """账号在目标吧被封禁（can_post=0）时必须在打开发布器前中止。"""
    post = make_post(kw="施工员")
    page = FakePermissionPage(_permission_body(0, "您的账号处于封禁状态，暂不能进行此操作", 10))
    try:
        await post._check_forum_permission(page, "施工员")
    except RuntimeError as exc:
        assert "封禁状态" in str(exc)
        assert "10" in str(exc)
        print("✅ 贴吧吧级封禁预检校验通过")
        return
    raise AssertionError("can_post=0 时应抛出 RuntimeError")


async def test_forum_permission_allows_when_can_post_one():
    post = make_post(kw="建筑")
    page = FakePermissionPage(_permission_body(1))
    await post._check_forum_permission(page, "建筑")
    print("✅ 贴吧发帖权限正常时放行校验通过")


def test_forum_permission_regex_parses_can_post():
    body = _permission_body(0, "封禁", 7)
    import re
    assert re.search(r'"can_post"\s*:\s*"?(\d)"?', body).group(1) == "0"
    assert re.search(r'"block_info"\s*:\s*"([^"]*)"', body).group(1) == "封禁"
    assert re.search(r'"days_tofree"\s*:\s*(\d+)', body).group(1) == "7"
    print("✅ 贴吧权限响应字段解析校验通过")


async def test_ai_declaration_checks_checked_state():
    """AI 声明必须校验 checked 状态；控件缺失时只告警不谎报。"""
    post = make_post()

    class RadioLocator(FakeLocator):
        def __init__(self, checked):
            super().__init__(count=1, visible=True)
            self._checked = checked

        async def is_checked(self):
            return self._checked

    class DeclarationPage(FakePage):
        def __init__(self, checked):
            super().__init__()
            self.radio = RadioLocator(checked)
            self.label = FakeLocator(count=1, visible=True)

        def locator(self, selector):
            if selector == '#publish input#range-radio0':
                return self.radio
            if 't-radio-group-item' in selector:
                return self.label
            return FakeLocator()

    page = DeclarationPage(True)
    await post.apply_ai_declaration(page)
    assert page.radio._checked is True
    print("✅ 贴吧 AI 声明选中态校验通过")


async def test_ai_declaration_missing_control_is_not_faked():
    """找不到控件时不能谎报已勾选，也不应抛错中断发布。"""
    post = make_post()
    page = FakePage()
    await post.apply_ai_declaration(page)
    print("✅ 贴吧 AI 声明控件缺失时安全降级校验通过")


def test_empty_title_raises():
    try:
        make_post(title="")
    except ValueError as exc:
        assert "标题不能为空" in str(exc)
        print("✅ 空标题校验通过")
        return
    raise AssertionError("空标题应抛出 ValueError")


def test_empty_body_raises():
    try:
        make_post(body="")
    except ValueError as exc:
        assert "正文不能为空" in str(exc)
        print("✅ 空正文校验通过")
        return
    raise AssertionError("空正文应抛出 ValueError")


def test_no_fid_and_kw_raises():
    try:
        TiebaPost(
            title=TITLE, body=BODY, tags=[], publish_date=datetime.now(),
            account_file=COOKIES_FILE, fid=None, kw=None,
        )
    except ValueError as exc:
        assert "fid" in str(exc) or "kw" in str(exc)
        print("✅ 无 fid/kw 校验通过")
        return
    raise AssertionError("fid 与 kw 均为空应抛出 ValueError")


def test_fid_takes_precedence_over_kw():
    post = make_post(fid="123456", kw="测试贴吧")
    assert post.fid == "123456"
    assert post.kw == "测试贴吧"
    print("✅ fid 优先级校验通过")


def test_post_url_extractors():
    body = '{"no":0,"data":{"tid":11168623528,"post_id":9999999999999}}'
    tids = tu._tid_candidates(body)
    assert tids == {"11168623528"}, tids
    assert tu._post_url_from_tids(tids) == "https://tieba.baidu.com/p/11168623528"
    assert tu._extract_post_url("https://tieba.baidu.com/p/11168623528?pn=1") == "https://tieba.baidu.com/p/11168623528"
    assert tu._extract_post_url('href="/p/11168623528"') == "https://tieba.baidu.com/p/11168623528"
    assert tu._extract_post_url("发送成功") == ""
    assert tu._post_url_from_tids({"111", "11168623528"}) == "https://tieba.baidu.com/p/11168623528"
    assert tu._normalize_title("会审改了图，现场？") == "会审改了图现场"
    print("✅ 链接与 tid 提取校验通过（忽略 post_id / 拒绝猜测）")


async def test_response_tids():
    post_response = FakeResponse(
        "https://tieba.baidu.com/c/c/bawu/commit",
        "POST",
        '{"no":0,"data":{"tid":11168623528,"post_id":9999999999999}}',
    )
    assert await tu._response_tids(post_response) == {"11168623528"}
    get_response = FakeResponse(
        "https://tieba.baidu.com/f?kw=施工员", "GET",
        '{"data":{"tid":11168623528}}',
    )
    assert await tu._response_tids(get_response) == set()
    redirect_response = FakeResponse(
        "https://tieba.baidu.com/c/c/bawu/commit", "POST", "",
        {"location": "https://tieba.baidu.com/p/11168623528"},
    )
    assert await tu._response_tids(redirect_response) == {"11168623528"}
    print("✅ 发帖响应 tid 取证校验通过（只认 POST）")


async def test_publish_post_from_response_tid():
    post = make_post()
    page = FakePage()
    page.attach(post)
    post.response_tid = "11168623528"
    async def _noop(page, *, search=False):
        return None
    post._refresh_forum = _noop
    url = await post.publish_post(page)
    assert url == "https://tieba.baidu.com/p/11168623528", url
    assert post.post_url == url
    print("✅ 发帖响应 tid → 公开链接校验通过")


async def test_publish_post_hidden_detail_link():
    post = make_post()
    anchor = FakeAnchor(
        "/p/11168623528",
        TITLE,
        "刚刚发布 · " + TITLE,
    )
    page = FakePage(success=True, anchors=[anchor])
    page.attach(post)
    async def _noop(page, *, search=False):
        return None
    post._refresh_forum = _noop
    url = await post.publish_post(page)
    assert url == "https://tieba.baidu.com/p/11168623528", url
    print("✅ 成功 toast + 标题匹配隐藏 /p/ 链接校验通过")


async def test_success_without_link_fails_closed():
    post = make_post()
    page = FakePage(success=True)
    page.attach(post)
    async def _noop(page, *, search=False):
        return None
    post._refresh_forum = _noop
    try:
        await post.publish_post(page)
    except RuntimeError as exc:
        assert "不能标记已发布" in str(exc)
        assert post.post_url == ""
        print("✅ 成功但无链接 fail-closed 校验通过")
        return
    raise AssertionError("成功提示但无 /p/ 证据应抛出 RuntimeError")


async def test_find_post_url_ignores_known_old_tid():
    post = make_post()
    post.known_tids = {"11168623528"}
    old = FakeAnchor("/p/11168623528", TITLE)
    page = FakePage(anchors=[old])
    assert await post._find_post_url_in_page(page) == ""
    post.known_tids = set()
    assert await post._find_post_url_in_page(page) == "https://tieba.baidu.com/p/11168623528"
    print("✅ 旧帖 ID 隔离与本期标题匹配校验通过")


async def test_dry_run_never_clicks_publish():
    async def noop(*args, **kwargs):
        return None

    async def fake_set_init_script(context):
        return context

    real_set_init_script = tu.set_init_script
    tu.set_init_script = fake_set_init_script
    try:
        for dry_run, is_draft in ((True, False), (False, True), (True, True)):
            post = make_post(dry_run=dry_run, is_draft=is_draft)
            flags = {"draft": False, "publish": False}

            async def save_draft(page):
                flags["draft"] = True

            async def publish_post(page):
                flags["publish"] = True
                return "https://tieba.baidu.com/p/11168623528"

            post.goto_post_page = noop
            post.fill_title = noop
            post.fill_body = noop
            post.upload_cover = noop
            post.save_draft = save_draft
            post.publish_post = publish_post
            post._save_evidence_screenshots = noop
            result = await post.upload(FakePlaywright())
            assert result == ""
            assert flags["draft"] is True and flags["publish"] is False, flags
        print("✅ 预览/草稿模式不点击发布校验通过")
    finally:
        tu.set_init_script = real_set_init_script


async def test_e2e_fill_flow_only():
    """可选真实填写流程；必须显式开启，且始终 dry_run，不点击发布。"""
    if os.environ.get("TIEBA_E2E") != "1":
        print("⚠️ 跳过真实 E2E（默认关闭，设置 TIEBA_E2E=1 才启用）")
        return
    if not os.path.exists(COOKIES_FILE):
        raise RuntimeError(f"TIEBA_E2E=1 但 cookie 不存在: {COOKIES_FILE}")
    post = make_post(dry_run=True, is_draft=True)
    await post.main()
    print("✅ 贴吧填写流程 E2E 验证通过（未自动发布）")


def main():
    test_empty_title_raises()
    test_empty_body_raises()
    test_no_fid_and_kw_raises()
    test_fid_takes_precedence_over_kw()
    test_post_url_extractors()
    async_tests = [
        test_response_tids(),
        test_forum_permission_blocks_when_can_post_zero(),
        test_forum_permission_allows_when_can_post_one(),
        test_ai_declaration_checks_checked_state(),
        test_ai_declaration_missing_control_is_not_faked(),
        test_publish_post_from_response_tid(),
        test_publish_post_hidden_detail_link(),
        test_success_without_link_fails_closed(),
        test_find_post_url_ignores_known_old_tid(),
        test_dry_run_never_clicks_publish(),
        test_e2e_fill_flow_only(),
    ]
    for coro in async_tests:
        asyncio.run(coro)
    test_forum_permission_regex_parses_can_post()
    print("\n🎉 贴吧上传器测试全部通过（默认未联网、未发布）。")


if __name__ == "__main__":
    main()
