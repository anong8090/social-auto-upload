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

# 各平台内容管理页
MANAGE_URLS = {
    5: "https://baijiahao.baidu.com/builder/rc/content?currentPage=1&pageSize=20&type=news",
    7: "https://mp.toutiao.com/profile_v4/graphic/articles",
    8: "https://mp.sohu.com/mpfe/v4/contentManagement/news",
    9: "https://www.zhihu.com/creator/manage/creation/all",
    11: "https://weibo.com/",
    12: "https://mp.weixin.qq.com/cgi-bin/appmsg?begin=0&count=10&type=77&action=list_card",
    13: "https://mp.163.com/",
    14: "https://mp.dayu.com/",
    15: "https://om.qq.com/articleManager/articleList",
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
            if "/preview" in url:
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


def _item_id_from_url(platform_type: int, url: str) -> str:
    """从公开链接中抽取平台条目 ID，便于后续数据回捞。"""
    if not url:
        return ""
    patterns = {
        5: r"[?&]id=(\d+)",
        7: r"/(?:article|item|w|i)/(\d+)",
        8: r"/a/(\d+)",
        9: r"/p/(\d+)",
        11: r"weibo\.com/\d+/([A-Za-z0-9]{6,})",
        12: r"/s/([A-Za-z0-9_-]{6,})",
        13: r"/article/([A-Za-z0-9_-]+)",
        14: r"/content/([A-Za-z0-9_-]+)",
        15: r"/(?:article|detail)/([A-Za-z0-9_-]+)",
    }
    match = re.search(patterns.get(platform_type, r"/([A-Za-z0-9_-]{6,})"), url)
    return match.group(1) if match else ""


async def _collect_with_page(platform_type: int, cookie: Path, title: str, timeout: int) -> str:
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
                8: ['a[href*="sohu.com/a/"]'],
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
                15: ['a[href*="article"]', 'a[href*="detail"]'],
            }.get(platform_type, ['a[href]'])

            url = await _first_matching_link(page, selectors, title)

            # 百家号预览链接可推导出正式链接
            if platform_type == 5 and url and "/builder/preview" in url:
                bid = _derive_baijiahao_id(url)
                if bid:
                    url = f"https://baijiahao.baidu.com/s?id={bid}"
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


async def collect(
    platform_type: int,
    cookie_path: Path,
    title: str = "",
    *,
    timeout: int = 60000,
) -> str:
    """采集单个平台的公开链接；失败返回空字符串。"""
    platform_type = int(platform_type)
    if platform_type not in MANAGE_URLS:
        return ""
    cookie = Path(cookie_path)
    if not cookie.exists():
        return ""
    try:
        url = await _collect_with_page(platform_type, cookie, title, timeout)
        # 微博首页/游戏页等不是文章链接，必须严格校验，避免把错误链接回填台账
        if platform_type == 11 and url:
            if not re.search(r"weibo\.com/\d+/[A-Za-z0-9]{6,}", url) and not re.search(
                r"/(?:status|detail)/", url
            ):
                print(f"⚠️ 微博链接不是文章页，已丢弃: {url}", flush=True)
                return ""
        return url
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ {PLATFORM_NAMES.get(platform_type, platform_type)} 链接采集失败: {exc}", flush=True)
        return ""


def collect_sync(platform_type: int, cookie_path: Path, title: str = "") -> tuple[str, str]:
    """同步包装，返回 (published_url, platform_item_id)。"""
    url = asyncio.run(collect(platform_type, cookie_path, title))
    return url, _item_id_from_url(platform_type, url)
