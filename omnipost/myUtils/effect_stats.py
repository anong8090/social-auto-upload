# -*- coding: utf-8 -*-
"""四家效果回收的纯逻辑：解析、快照档位、落库。

不打开浏览器，不写飞书台账。缺字段保持 NULL，不把「没给」写成 0。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Optional

COUNT_FIELDS = (
    "read_count",
    "like_count",
    "comment_count",
    "share_count",
    "collect_count",
)

PLATFORM_NOTES = {
    9: "知乎公开页只有赞同/评论；阅读数页面没给就留空",
    10: "贴吧只取按钮旁边明确跟过的数字；没有的指标留空",
    11: "微博取赞/评论/转发；接口没有阅读字段就留空",
    12: "公众号取已发表列表；不进蓝枢主线台账",
}

_TIEBA_LABELS = {
    "转发": "share_count",
    "回复": "comment_count",
    "收藏": "collect_count",
}

_ZHIHU_COUNTS = {
    "voteupCount": "like_count",
    "commentCount": "comment_count",
}

_WECHAT_COUNTS = {
    "read_num": "read_count",
    "like_num": "like_count",
    "comment_num": "comment_count",
    "share_num": "share_count",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS effect_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_type INTEGER NOT NULL,
    platform_name TEXT NOT NULL,
    item_id TEXT NOT NULL,
    title TEXT,
    episode TEXT,
    published_url TEXT,
    published_at TEXT,
    read_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    collect_count INTEGER,
    extra_json TEXT,
    present_fields TEXT NOT NULL,
    snapshot_slot TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    note TEXT,
    UNIQUE(platform_type, item_id, snapshot_slot)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)


def as_int(value: Any) -> Optional[int]:
    """只接受整数值。解析不了就当这个字段没给，不能变成 0。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d+", text):
            return int(text)
    return None


def parse_weibo_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    metrics: dict[str, int] = {}
    mapping = {
        "attitudes_count": "like_count",
        "comments_count": "comment_count",
        "reposts_count": "share_count",
        "reads_count": "read_count",
    }
    for src, dest in mapping.items():
        if src not in payload:
            continue
        number = as_int(payload.get(src))
        if number is not None:
            metrics[dest] = number
    return {"metrics": metrics, "extra": {}}


def parse_zhihu_html(html: str) -> dict[str, Any]:
    metrics: dict[str, int] = {}
    for src, dest in _ZHIHU_COUNTS.items():
        match = re.search(rf'"{src}"\s*:\s*(-?\d+)', html or "")
        if match:
            metrics[dest] = int(match.group(1))
    extra: dict[str, int] = {}
    liked = re.search(r'"likedCount"\s*:\s*(-?\d+)', html or "")
    if liked:
        extra["liked_count"] = int(liked.group(1))
    return {"metrics": metrics, "extra": extra}


def parse_tieba_action_texts(texts: Iterable[str]) -> dict[str, Any]:
    """数字只归给它紧挨着的前一个标签。孤立数字不猜。"""
    items: list[str] = []
    for text in texts:
        items.extend(part for part in re.split(r"\s+", str(text or "").strip()) if part)
    metrics: dict[str, int] = {}
    index = 0
    while index < len(items):
        dest = _TIEBA_LABELS.get(items[index])
        if dest and index + 1 < len(items):
            number = as_int(items[index + 1])
            if number is not None:
                metrics[dest] = number
                index += 2
                continue
        index += 1
    return {"metrics": metrics, "extra": {}}


def parse_wechat_publish_response(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_page = payload.get("publish_page")
    if isinstance(raw_page, str):
        try:
            page = json.loads(raw_page)
        except json.JSONDecodeError:
            return []
    elif isinstance(raw_page, dict):
        page = raw_page
    else:
        return []
    rows: list[dict[str, Any]] = []
    for item in page.get("publish_list") or []:
        if not isinstance(item, dict):
            continue
        info = item.get("publish_info")
        if isinstance(info, str):
            try:
                info = json.loads(info)
            except json.JSONDecodeError:
                continue
        if not isinstance(info, dict):
            continue
        sent = info.get("sent_info") if isinstance(info.get("sent_info"), dict) else {}
        for article in info.get("appmsg_info") or []:
            if not isinstance(article, dict) or article.get("appmsgid") in (None, ""):
                continue
            metrics: dict[str, int] = {}
            for src, dest in _WECHAT_COUNTS.items():
                if src not in article:
                    continue
                number = as_int(article.get(src))
                if number is not None:
                    metrics[dest] = number
            extra: dict[str, int] = {}
            if "old_like_num" in article:
                zaikan = as_int(article.get("old_like_num"))
                if zaikan is not None:
                    extra["zaikan"] = zaikan
            rows.append({
                "item_id": str(article.get("appmsgid")),
                "title": str(article.get("title") or "").strip(),
                "published_url": article.get("content_url") or None,
                "published_at": parse_timestamp(sent.get("time")),
                "metrics": metrics,
                "extra": extra,
                "deleted": bool(article.get("is_deleted")),
            })
    return rows


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp /= 1000
        try:
            return datetime.fromtimestamp(stamp)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def snapshot_slots(published_at: Optional[datetime], now: datetime) -> list[str]:
    """latest 每次更新。h24 / d7 只在窗口内补一次，过了窗口不回填。"""
    slots = ["latest"]
    if published_at is None:
        return slots
    age = now - published_at
    if timedelta(hours=24) <= age < timedelta(hours=48):
        slots.append("h24")
    elif timedelta(days=7) <= age < timedelta(days=8):
        slots.append("d7")
    return slots


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").replace("\xa0", " ")).strip()


def topics_from_markdown(text: str) -> dict[str, str]:
    """个人 IP 登记表：标题 -> 日期。同标题保留先出现的一行。"""
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        title = normalize_title(cells[1])
        if title and title not in found:
            found[title] = cells[0]
    return found


def present_fields(metrics: Mapping[str, Any], extra: Mapping[str, Any]) -> list[str]:
    fields = [name for name in COUNT_FIELDS if name in metrics]
    fields.extend(sorted(extra))
    return fields


def apply_snapshots(
    conn: sqlite3.Connection,
    *,
    platform_type: int,
    platform_name: str,
    item_id: str,
    title: str,
    episode: Optional[str],
    published_url: Optional[str],
    published_at: Optional[datetime],
    metrics: Mapping[str, Any],
    extra: Mapping[str, Any],
    note: str,
    now: datetime,
) -> list[str]:
    """写入本次快照。返回实际新写或更新的档位。h24/d7 已存在则跳过。"""
    ensure_schema(conn)
    slots = snapshot_slots(published_at, now)
    fetched_at = now.strftime("%Y-%m-%d %H:%M:%S")
    published_text = published_at.strftime("%Y-%m-%d %H:%M:%S") if published_at else None
    extra_json = json.dumps(extra, ensure_ascii=False) if extra else None
    fields_json = json.dumps(present_fields(metrics, extra), ensure_ascii=False)
    counts = [metrics.get(name) if name in metrics else None for name in COUNT_FIELDS]
    written: list[str] = []
    for slot in slots:
        if slot == "latest":
            updated = conn.execute(
                """
                UPDATE effect_snapshots
                   SET title=?, episode=?, published_url=?, published_at=?,
                       read_count=?, like_count=?, comment_count=?, share_count=?, collect_count=?,
                       extra_json=?, present_fields=?, fetched_at=?, note=?
                 WHERE platform_type=? AND item_id=? AND snapshot_slot='latest'
                """,
                (
                    title, episode, published_url, published_text, *counts,
                    extra_json, fields_json, fetched_at, note,
                    platform_type, item_id,
                ),
            )
            if updated.rowcount:
                written.append(slot)
                continue
        else:
            exists = conn.execute(
                """
                SELECT 1 FROM effect_snapshots
                 WHERE platform_type=? AND item_id=? AND snapshot_slot=?
                """,
                (platform_type, item_id, slot),
            ).fetchone()
            if exists:
                continue
        conn.execute(
            """
            INSERT INTO effect_snapshots (
                platform_type, platform_name, item_id, title, episode, published_url,
                published_at, read_count, like_count, comment_count, share_count,
                collect_count, extra_json, present_fields, snapshot_slot, fetched_at, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                platform_type, platform_name, item_id, title, episode, published_url,
                published_text, *counts, extra_json, fields_json, slot, fetched_at, note,
            ),
        )
        written.append(slot)
    return written
