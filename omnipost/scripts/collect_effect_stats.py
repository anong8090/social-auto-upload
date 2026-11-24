#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回收知乎、贴吧、微博、公众号的效果数。

只读后台或公开页，不发布、不改规则、不回填飞书主线台账。
缺的指标写 NULL，后台明确给出的 0 会保留。

用法：
    omnipost/.venv/bin/python omnipost/scripts/collect_effect_stats.py
    omnipost/.venv/bin/python omnipost/scripts/collect_effect_stats.py --platforms 9,11
    omnipost/.venv/bin/python omnipost/scripts/collect_effect_stats.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests
from playwright.async_api import async_playwright

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = OMNIPOST_DIR.parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conf import BASE_DIR  # noqa: E402
from myUtils.effect_stats import (  # noqa: E402
    PLATFORM_NOTES,
    apply_snapshots,
    normalize_title,
    parse_tieba_action_texts,
    parse_timestamp,
    parse_wechat_publish_response,
    parse_weibo_status,
    parse_zhihu_html,
    topics_from_markdown,
)
from myUtils.preflight import get_account, resolve_cookie_path  # noqa: E402

DB_PATH = Path(BASE_DIR) / "db" / "database.db"
TOPICS_PATH = PROJECT_DIR / "PERSONAL_IP_TOPICS.md"
LOG_DIR = PROJECT_DIR / "logs" / "effect_stats"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

PLATFORM_ORDER = (9, 10, 11, 12)
PLATFORM_NAMES = {9: "知乎", 10: "百度贴吧", 11: "微博", 12: "微信公众号"}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _cookie_file(platform_type: int) -> Path:
    account = get_account(DB_PATH, platform_type)
    if not account:
        raise RuntimeError(f"{PLATFORM_NAMES[platform_type]} 没有有效账号")
    path = resolve_cookie_path(Path(BASE_DIR), str(account.get("filePath") or ""))
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"{PLATFORM_NAMES[platform_type]} 登录态文件不存在")
    return path


def _cookies_for_requests(path: Path) -> requests.cookies.RequestsCookieJar:
    data = json.loads(path.read_text(encoding="utf-8"))
    jar = requests.cookies.RequestsCookieJar()
    for item in data.get("cookies") or []:
        name = item.get("name")
        if not name:
            continue
        jar.set(name, item.get("value") or "", domain=item.get("domain") or "", path=item.get("path") or "/")
    return jar


def _load_history(platform_type: int) -> list[dict[str, Any]]:
    sql = """
        SELECT platform_type, platform_name, title, episode, published_url,
               platform_item_id, published_at
          FROM publish_history
         WHERE platform_type = ?
           AND status = 'published'
           AND COALESCE(published_url, '') != ''
         ORDER BY id
    """
    with _connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, (platform_type,))]
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        item_id = _history_item_id(row)
        if not item_id:
            continue
        row["item_id"] = item_id
        deduped[item_id] = row
    return list(deduped.values())


def _history_item_id(row: dict[str, Any]) -> Optional[str]:
    existing = str(row.get("platform_item_id") or "").strip()
    if existing:
        return existing
    url = str(row.get("published_url") or "")
    if row["platform_type"] in (9, 10):
        match = re.search(r"/p/(\d+)", url)
        return match.group(1) if match else None
    match = re.search(r"/([^/?#]+)/?$", url)
    return match.group(1) if match else None


def _load_topics() -> dict[str, str]:
    if not TOPICS_PATH.exists():
        return {}
    return topics_from_markdown(TOPICS_PATH.read_text(encoding="utf-8"))


def _save(record: dict[str, Any], now: datetime, dry_run: bool) -> list[str]:
    if dry_run:
        return ["dry-run"]
    with _connect() as conn:
        written = apply_snapshots(conn, now=now, **record)
        conn.commit()
        return written


def fetch_weibo(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    jar = _cookies_for_requests(_cookie_file(11))
    xsrf = ""
    for cookie in jar:
        if cookie.name == "XSRF-TOKEN":
            xsrf = cookie.value
            break
    session = requests.Session()
    session.cookies = jar
    session.headers.update({
        "User-Agent": UA,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-XSRF-TOKEN": xsrf,
    })
    parsed_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if index:
            time.sleep(0.3)
        try:
            response = session.get(
                "https://weibo.com/ajax/statuses/show",
                params={"id": item["item_id"]},
                headers={"Referer": item["published_url"]},
                timeout=25,
            )
        except requests.RequestException as exc:
            errors.append(f"微博 {item['item_id']} 请求失败：{exc.__class__.__name__}")
            continue
        if response.status_code != 200:
            errors.append(f"微博 {item['item_id']} HTTP {response.status_code}")
            if index == 0 and response.status_code in (401, 403):
                break
            continue
        try:
            payload = response.json()
        except ValueError:
            errors.append(f"微博 {item['item_id']} 不是 JSON")
            continue
        if not isinstance(payload, dict) or (
            "attitudes_count" not in payload and "comments_count" not in payload
        ):
            errors.append(f"微博 {item['item_id']} 返回里没有互动字段")
            continue
        parsed = parse_weibo_status(payload)
        parsed_rows.append(_history_record(item, parsed, PLATFORM_NOTES[11]))
    return parsed_rows, errors


def _history_record(item: dict[str, Any], parsed: dict[str, Any], note: str) -> dict[str, Any]:
    return {
        "platform_type": int(item["platform_type"]),
        "platform_name": item.get("platform_name") or PLATFORM_NAMES[int(item["platform_type"])],
        "item_id": item["item_id"],
        "title": item.get("title") or "",
        "episode": item.get("episode"),
        "published_url": item.get("published_url"),
        "published_at": parse_timestamp(item.get("published_at")),
        "metrics": parsed["metrics"],
        "extra": parsed["extra"],
        "note": note,
    }


async def _launch_browser(playwright):
    return await playwright.chromium.launch(
        headless=True,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled", "--lang=zh-CN"],
    )


async def fetch_zhihu(browser, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    context = await browser.new_context(storage_state=str(_cookie_file(9)))
    page = await context.new_page()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        for index, item in enumerate(items):
            if index:
                await page.wait_for_timeout(400)
            url = item["published_url"]
            html = ""
            opened = False
            for _attempt in range(2):
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(1200)
                    html = await page.content()
                    opened = True
                    break
                except Exception as exc:
                    if _attempt == 1:
                        errors.append(f"知乎 {item['item_id']} 打开失败：{exc.__class__.__name__}")
            if not opened:
                continue
            current = page.url or ""
            if "signin" in current or "login" in current:
                errors.append("知乎登录态失效，后面的文章不再打开")
                break
            parsed = parse_zhihu_html(html)
            if not parsed["metrics"]:
                errors.append(f"知乎 {item['item_id']} 页面上没有赞同/评论字段")
                continue
            rows.append(_history_record(item, parsed, PLATFORM_NOTES[9]))
    finally:
        await context.close()
    return rows, errors


async def fetch_tieba(browser, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    context = await browser.new_context(storage_state=str(_cookie_file(10)))
    page = await context.new_page()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        for index, item in enumerate(items):
            if index:
                await page.wait_for_timeout(400)
            try:
                await page.goto(item["published_url"], wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1000)
            except Exception as exc:
                errors.append(f"贴吧 {item['item_id']} 打开失败：{exc.__class__.__name__}")
                continue
            title = await page.title()
            if "安全验证" in title or "passport.baidu.com" in (page.url or ""):
                errors.append("贴吧打开后是安全验证或登录页，停止继续抓，避免把空数写成快照")
                break
            try:
                await page.wait_for_selector(".action-number", timeout=8000)
            except Exception:
                errors.append(f"贴吧 {item['item_id']} 没有出现互动按钮，这条不入库")
                continue
            texts = await page.locator(".action-number").all_inner_texts()
            parsed = parse_tieba_action_texts(texts)
            if not parsed["metrics"]:
                errors.append(f"贴吧 {item['item_id']} 页面没有紧挨标签的数字，这条不入库")
                continue
            rows.append(_history_record(item, parsed, PLATFORM_NOTES[10]))
    finally:
        await context.close()
    return rows, errors


async def fetch_wechat(browser, topics: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    context = await browser.new_context(storage_state=str(_cookie_file(12)))
    page = await context.new_page()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        await page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3500)
        match = re.search(r"[?&]token=(\d+)", page.url or "")
        if not match:
            return [], ["公众号登录态打不开后台，没有 token"]
        token = match.group(1)
        pages = await page.evaluate(
            """async (token) => {
                const pages = [];
                let begin = 0;
                let total = 1;
                while (begin < total && pages.length < 10) {
                    const url = `/cgi-bin/appmsgpublish?sub=list&begin=${begin}&count=20&token=${token}&lang=zh_CN&f=json&ajax=1`;
                    const response = await fetch(url, {credentials: 'include'});
                    const payload = await response.json();
                    pages.push(payload);
                    let page = {};
                    try { page = JSON.parse(payload.publish_page || '{}'); } catch (err) {}
                    total = Number(page.total_count || 0);
                    const size = (page.publish_list || []).length;
                    if (!size) break;
                    begin += 20;
                }
                return pages;
            }""",
            token,
        )
        seen: set[str] = set()
        for payload in pages or []:
            if not isinstance(payload, dict):
                continue
            base = payload.get("base_resp") or {}
            if base.get("ret") not in (0, None):
                errors.append(f"公众号发表列表返回 ret={base.get('ret')}")
                continue
            for article in parse_wechat_publish_response(payload):
                if article["item_id"] in seen:
                    continue
                seen.add(article["item_id"])
                title = normalize_title(article["title"])
                topic_date = topics.get(title)
                note = PLATFORM_NOTES[12]
                episode = None
                if topic_date:
                    note = f"{note}；对上个人 IP {topic_date}"
                    episode = topic_date
                if article["deleted"]:
                    note = f"{note}；后台标记已删除"
                rows.append({
                    "platform_type": 12,
                    "platform_name": "微信公众号",
                    "item_id": article["item_id"],
                    "title": article["title"],
                    "episode": episode,
                    "published_url": article["published_url"],
                    "published_at": article["published_at"],
                    "metrics": article["metrics"],
                    "extra": article["extra"],
                    "note": note,
                })
        if not rows and not errors:
            errors.append("公众号发表列表是空的")
    finally:
        await context.close()
    return rows, errors


def _summarize(platform_name: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"{platform_name}：没有写入"
    fields = ("read_count", "like_count", "comment_count", "share_count", "collect_count")
    labels = {
        "read_count": "阅读",
        "like_count": "赞",
        "comment_count": "评论",
        "share_count": "转发",
        "collect_count": "收藏",
    }
    present = []
    missing = []
    for field in fields:
        got = sum(1 for row in rows if field in row["metrics"])
        if got:
            present.append(f"{labels[field]} {got}")
        else:
            missing.append(labels[field])
    text = f"{platform_name}：{len(rows)} 篇，有数 {('、'.join(present) or '无')}"
    if missing:
        text += f"；留空 {'、'.join(missing)}"
    return text


async def collect(platforms: list[int], dry_run: bool) -> int:
    now = datetime.now()
    topics = _load_topics()
    summaries: list[str] = []
    errors: list[str] = []
    saved = 0
    need_browser = any(item in platforms for item in (9, 10, 12))
    browser = None
    playwright = None
    try:
        if need_browser:
            playwright = await async_playwright().start()
            browser = await _launch_browser(playwright)
        for platform_type in platforms:
            name = PLATFORM_NAMES[platform_type]
            try:
                if platform_type == 11:
                    rows, platform_errors = fetch_weibo(_load_history(11))
                elif platform_type == 9:
                    rows, platform_errors = await fetch_zhihu(browser, _load_history(9))
                elif platform_type == 10:
                    rows, platform_errors = await fetch_tieba(browser, _load_history(10))
                else:
                    rows, platform_errors = await fetch_wechat(browser, topics)
            except Exception as exc:
                summaries.append(f"{name}：失败（{exc.__class__.__name__}）")
                errors.append(f"{name}：{exc}")
                continue
            errors.extend(platform_errors)
            for row in rows:
                _save(row, now, dry_run)
                saved += 1
            summaries.append(_summarize(name, rows))
    finally:
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()

    print("\n".join(summaries))
    if errors:
        print("问题：")
        for item in errors:
            print(f"- {item}")
    if not dry_run and saved:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%d_%H%M%S")
        payload = {"fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"), "summaries": summaries, "errors": errors}
        (LOG_DIR / f"{stamp}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"记录：logs/effect_stats/{stamp}.json")
    if saved:
        return 0
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="回收知乎、贴吧、微博、公众号效果数")
    parser.add_argument("--platforms", default="9,10,11,12", help="平台类型码，逗号分隔")
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印，不写数据库")
    args = parser.parse_args(argv)
    try:
        platforms = [int(item) for item in args.platforms.split(",") if item.strip()]
    except ValueError:
        print("platforms 必须是数字")
        return 2
    unknown = [item for item in platforms if item not in PLATFORM_ORDER]
    if unknown or not platforms:
        print("只接受 9 知乎、10 贴吧、11 微博、12 公众号")
        return 2
    ordered = [item for item in PLATFORM_ORDER if item in platforms]
    return asyncio.run(collect(ordered, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
