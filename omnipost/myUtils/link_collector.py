# -*- coding: utf-8 -*-
"""发布后公开链接采集。

背景：多数 uploader 的 `main()` 不返回发布链接，导致发布成功但链接丢失，
只能事后写一次性脚本反爬。本模块按平台从各自的「内容管理」后台抓取，
并优先用标题匹配目标文章，匹配不到才退回列表首条。

约定：采集失败返回空字符串，绝不抛异常打断发布主流程。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

PLATFORM_NAMES = {
    5: "百家号",
    7: "今日头条",
    8: "搜狐号",
    9: "知乎",
    10: "百度贴吧",
    11: "微博",
    12: "微信公众号",
    13: "网易号",
    14: "大鱼号",
    15: "企鹅号",
}

# 核验截图文件名中的平台短名，保持历史文件命名习惯。
PLATFORM_SLUGS = {
    5: "baijiahao",
    7: "toutiao",
    8: "sohu",
    9: "zhihu",
    10: "tieba",
    11: "weibo",
    12: "wechat_mp",
    13: "wangyi",
    14: "dayu",
    15: "qiehao",
}

# 各平台内容管理页
MANAGE_URLS = {
    5: "https://baijiahao.baidu.com/builder/rc/content?currentPage=1&pageSize=20&type=news",
    7: "https://mp.toutiao.com/profile_v4/graphic/articles",
    8: "https://mp.sohu.com/mpfe/v4/contentManagement/first/page",
    9: "https://www.zhihu.com/creator/manage/creation/all",
    11: "https://weibo.com/",
    12: "https://mp.weixin.qq.com/cgi-bin/appmsg?begin=0&count=10&type=77&action=list_card",
    13: "https://mp.163.com/",
    14: "https://mp.dayu.com/dashboard/article/list",
    # 企鹅号旧地址 /main/content-manage 与 /articleManager/articleList 均已失效。
    15: "https://om.qq.com/main/management/articleManage",
}


def _normalize(text: str) -> str:
    """去掉空白与常见标点，便于跨平台标题比对。"""
    if not text:
        return ""
    return re.sub(r"[\s　，。！？、；：“”‘’（）()\[\]【】《》—\-·….,!?;:'\"]+", "", text)


def _title_hit(candidate: str, title: str) -> bool:
    a, b = _normalize(candidate), _normalize(title)
    if not a or not b:
        return False
    return a in b or b in a


async def _first_matching_link(page, selectors: list[str], title: str) -> str:
    """在内容管理页按标题匹配公开链接。

    注意：绝不能在标题匹配失败时退回首条链接；首条通常是上一期文章，
    会把错误链接回填到台账。只有调用方未给标题时才允许退回列表首条。
    """
    fallback = ""
    for selector in selectors:
        try:
            anchors = await page.locator(selector).all()
        except Exception:
            continue
        for anchor in anchors:
            try:
                href = await anchor.get_attribute("href")
                text = (await anchor.inner_text()) or ""
                card = (await anchor.evaluate(
                    """el => {
                        const box = el.closest('li, tr, [class*="item"], [class*="card"], [class*="articleItem"]');
                        return (box || el.parentElement || el).innerText || '';
                    }"""
                )) or ""
            except Exception:
                continue
            if not href:
                continue
            url = href if href.startswith("http") else "https:" + href
            if "/preview" in url or "/edit" in url:
                continue
            # 后台列表链接常带统计参数，回台账时只保留公开地址
            if "toutiao.com/item/" in url:
                url = url.split("?", 1)[0]
            if url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            if not fallback:
                fallback = url
            if _title_hit(text, title) or _title_hit(card, title) or _title_hit(url, title):
                return url
    return fallback if not title else ""


def _derive_baijiahao_id(url: str) -> str:
    match = re.search(r"[?&]id=(\d+)", url or "")
    return match.group(1) if match else ""


async def _collect_zhihu_by_api(page, title: str) -> str:
    """按标题从知乎创作中心列表 API 补采文章公开链接。"""
    api = (
        "https://www.zhihu.com/api/v4/creators/creations/v2/all"
        "?limit=20&offset=0&need_co_creation=1&sort_type=created"
    )
    for _page_no in range(5):
        try:
            payload = await page.evaluate(
                """async (url) => {
                    const resp = await fetch(url, {credentials: 'include'});
                    return await resp.json();
                }""",
                api,
            )
        except Exception:
            await page.wait_for_timeout(1500)
            continue
        for item in payload.get("data") or []:
            data = item.get("data") or {}
            candidate = str(data.get("title") or "")
            article_id = str(data.get("id") or data.get("url_token") or "")
            if article_id and _title_hit(candidate, title):
                return f"https://zhuanlan.zhihu.com/p/{article_id}"
        next_url = ((payload.get("paging") or {}).get("next") or "").replace("\\u0026", "&")
        if not next_url or next_url == api:
            break
        api = next_url
    return ""


async def _collect_sohu_by_api(page, title: str, headers: Optional[dict] = None,
                               base_url: str = "") -> str:
    """按标题从搜狐号后台「已发布」列表补采公开链接。

    搜狐号发文后先进入平台审核，审核不通过时后台只留一条被驳回记录，
    没有任何公开页面。此时绝不能把驳回条目的 ID 拼成公开链接。

    因此这里只查 statusType=2（已发布列表），并且只接受
    auditStatus/status == 4 且 rejectReason 为空的条目。
    驳回条目（auditStatus=8）只出现在 statusType=1/7 中，不在已发布列表内。

    该接口要求复用管理页自身的请求头（x-requested-with / sp-cm / dv-id 等）
    与查询参数（accountId 等），否则返回空列表或 1301「获取客户端失败」。
    """
    headers = headers or {}
    if not headers.get("x-requested-with"):
        return ""

    url = base_url or (
        "https://mp.sohu.com/mpbp/bp/news/v4/users/news"
        "?psize=20&newsType=0&statusType=2&columnId=&pno=1&streamId="
    )
    # 强制切到「已发布」列表
    url = re.sub(r"([?&])statusType=\d+", r"\1statusType=2", url)
    if "statusType=" not in url:
        url += ("&" if "?" in url else "?") + "statusType=2"

    try:
        payload = await page.evaluate(
            """async ([url, headers]) => {
                const resp = await fetch(url, {credentials: 'include', headers});
                const text = await resp.text();
                try { return {ok: true, data: JSON.parse(text)}; }
                catch (e) { return {ok: false, text: text.slice(0, 300)}; }
            }""",
            [url, headers],
        )
    except Exception:
        return ""

    if not payload.get("ok"):
        return ""

    data = payload.get("data") or {}
    items = data.get("news") or []
    if not isinstance(items, list):
        return ""

    # 同一标题可能存在历史条目（含被驳回记录），只认最新一条
    newest: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("title") or item.get("mobileTitle") or "")
        if not _title_hit(candidate, title):
            continue
        created = int(item.get("createdTime") or item.get("postTime") or 0)
        if created and created <= newest.get("created", 0):
            continue
        newest = {
            "id": str(item.get("id") or ""),
            "audit": str(item.get("auditStatus")),
            "status": str(item.get("status")),
            "reason": str(item.get("rejectReason") or "").strip(),
            "userId": str(item.get("userId") or ""),
            "created": created,
        }

    if not newest or not newest["id"]:
        return ""
    # 只认审核通过且无驳回原因的条目
    if newest["audit"] != "4" and newest["status"] != "4":
        return ""
    if newest["reason"]:
        return ""
    if newest["userId"]:
        return f"https://www.sohu.com/a/{newest['id']}_{newest['userId']}"
    return f"https://www.sohu.com/a/{newest['id']}"


def _item_id_from_url(platform_type: int, url: str) -> str:
    """从公开链接中抽取平台条目 ID，便于后续数据回捞。"""
    if not url:
        return ""
    patterns = {
        5: r"[?&]id=(\d+)",
        7: r"/(?:article|item|w|i)/(\d+)",
        8: r"/a/(\d+)",
        9: r"/p/(\d+)",
        10: r"/p/(\d+)",
        11: r"weibo\.com/\d+/([A-Za-z0-9]{6,})",
        12: r"/s/([A-Za-z0-9_-]{6,})",
        13: r"/article/([A-Za-z0-9_-]+)",
        14: r"/content/([A-Za-z0-9_-]+)",
        15: r"/page/([A-Za-z0-9_-]+)",
    }
    match = re.search(patterns.get(platform_type, r"/([A-Za-z0-9_-]{6,})"), url)
    return match.group(1) if match else ""


async def _collect_with_page(platform_type: int, cookie: Path, title: str, timeout: int, episode: str = "") -> str:
    from playwright.async_api import async_playwright

    if not title:
        return ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(
            storage_state=str(cookie),
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )
        page = await context.new_page()
        # 搜狐后台接口需要复用管理页自身的请求头（x-requested-with / sp-cm / dv-id），
        # 否则返回 1301「获取客户端失败」；这里在加载管理页时被动捕获。
        sohu_headers: dict = {}
        sohu_list_url: str = ""

        async def _capture_sohu_headers(request):
            nonlocal sohu_list_url
            if "/users/news?" not in (request.url or ""):
                return
            if not sohu_list_url:
                sohu_list_url = request.url
            try:
                raw = await request.all_headers()
            except Exception:
                return
            for key in ("x-requested-with", "sp-cm", "dv-id", "referer",
                        "accept", "accept-language"):
                for k, v in raw.items():
                    if k.lower() == key and v:
                        sohu_headers.setdefault(key, v)

        page.on("request", lambda r: asyncio.ensure_future(_capture_sohu_headers(r)))
        try:
            await page.goto(MANAGE_URLS[platform_type], timeout=timeout, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            selectors = {
                5: ['a[href*="baijiahao.baidu.com/s?id="]', 'a[href*="/builder/preview/s?id="]'],
                7: [
                    'a[href*="toutiao.com/item/"]',
                    'a[href*="toutiao.com/article/"]',
                    'a[href*="/w/"]',
                    'a[href*="/i/"]',
                ],
                8: ['a[href*="sohu.com/a/"]', 'a[href*="/a/"]'],
                9: ['a[href*="zhuanlan.zhihu.com/p/"]'],
                11: [
                    'a[href*="/status/"]',
                    'a[href*="/detail/"]',
                    'a[href*="weibo.com/"][href*="/"][href*="?"]',
                ],
                12: [
                    'a[href*="mp.weixin.qq.com/s/"]',
                    'a[href*="/s?__biz="]',
                ],
                13: ['a[href*="/article/"]'],
                14: ['a[href*="/content/"]'],
                15: ['a[href^="https://page.om.qq.com/page/"]'],
            }.get(platform_type, ['a[href]'])

            url = await _first_matching_link(page, selectors, title)
            # 知乎内容管理页按「created」排序，刚发布文章可能出现在后续分页；
            # 用创作中心列表 API 按标题精确补采，避免误取上一期文章链接。
            if not url and platform_type == 9:
                url = await _collect_zhihu_by_api(page, title)
            # 搜狐号：页面列表可能还没刷新出刚审核通过的文章，
            # 用后台「已发布」列表 API 补采；审核未通过的条目不在该列表内。
            if not url and platform_type == 8:
                url = await _collect_sohu_by_api(page, title, sohu_headers, sohu_list_url)

            # 百家号预览链接可推导出正式链接
            if platform_type == 5 and url and "/builder/preview" in url:
                bid = _derive_baijiahao_id(url)
                if bid:
                    url = f"https://baijiahao.baidu.com/s?id={bid}"

            # 内容管理页留一张本期核验截图；截图失败不影响链接采集结果
            if url and episode:
                try:
                    shot_dir = Path(__file__).resolve().parents[2] / "logs" / "screenshots"
                    shot_dir.mkdir(parents=True, exist_ok=True)
                    shot = shot_dir / f"ep{episode}_{PLATFORM_SLUGS.get(platform_type, platform_type)}_published.png"
                    await page.screenshot(full_page=True, path=str(shot))
                except Exception:
                    pass
            return url
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass


def _is_public_qiehao_url(url: str) -> bool:
    """企鹅号只认 `page.om.qq.com/page/<id>`，后台编辑链接不能回填台账。"""
    return bool(re.match(r"^https://page\.om\.qq\.com/page/[A-Za-z0-9_-]+/?$", url or ""))


def _is_public_dayu_url(url: str) -> bool:
    """大鱼号只认公开文章详情页，写稿/预览/后台路由一律不算公开链接。

    大鱼号的公开详情页尚未完成 E2E 校验，这里把已知形态显式列出；
    命中不了的一律判为「不可回填」，避免把写稿页地址当成公开链接写进台账。
    """
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


async def collect(
    platform_type: int,
    cookie_path: Path,
    title: str = "",
    *,
    timeout: int = 60000,
    episode: str = "",
) -> str:
    """采集单个平台的公开链接；失败返回空字符串。"""
    platform_type = int(platform_type)
    if platform_type not in MANAGE_URLS:
        return ""
    cookie = Path(cookie_path)
    if not cookie.exists():
        return ""
    try:
        url = await _collect_with_page(platform_type, cookie, title, timeout, episode)
        # 微博首页/游戏页等不是文章链接，必须严格校验，避免把错误链接回填台账
        if platform_type == 11 and url:
            if not re.search(r"weibo\.com/\d+/[A-Za-z0-9]{6,}", url) and not re.search(
                r"/(?:status|detail)/", url
            ):
                print(f"⚠️ 微博链接不是文章页，已丢弃: {url}", flush=True)
                return ""
        # 企鹅号内容管理页同时存在「公开文章链接」与「后台编辑链接」，
        # 只保留前者；旧版误把 `/main/creation/article?articleId=...` 当公开链接。
        if platform_type == 15 and url:
            url = url.split("?", 1)[0]
            if not _is_public_qiehao_url(url):
                print(f"⚠️ 企鹅号链接不是公开文章页，已丢弃: {url}", flush=True)
                return ""
        # 大鱼号同理：写稿页/预览页不是公开链接，不能回填台账。
        if platform_type == 14 and url:
            url = url.split("?", 1)[0]
            if not _is_public_dayu_url(url):
                print(f"⚠️ 大鱼号链接不是公开文章页，已丢弃: {url}", flush=True)
                return ""
        return url
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ {PLATFORM_NAMES.get(platform_type, platform_type)} 链接采集失败: {exc}", flush=True)
        return ""


def collect_sync(platform_type: int, cookie_path: Path, title: str = "", episode: str = "") -> tuple[str, str]:
    """同步包装，返回 (published_url, platform_item_id)。"""
    url = asyncio.run(collect(platform_type, cookie_path, title, episode=episode))
    return url, _item_id_from_url(platform_type, url)
