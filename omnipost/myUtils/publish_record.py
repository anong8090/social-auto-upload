# -*- coding: utf-8 -*-
"""发布记录的统一读写层。

解决的问题：
1. `publish_history` 原先没有发布链接、期数、平台条目 ID 字段，链接抓到即丢失；
2. `status` 取值在 '1' / 'success' / 'failed' / 'publishing' / 'preview' / 'draft'
   之间混用，前端筛选与台账统计不可靠；
3. 重复执行发布脚本缺少幂等判断，会重复发稿。

本模块是发布记录的唯一入口，其他脚本请勿直接拼 SQL 写 `publish_history`。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# ---- 统一状态枚举 ----
STATUS_PENDING = "pending"        # 已创建，尚未开始
STATUS_PREVIEW = "preview"        # 仅填表预览，未真正提交
STATUS_DRAFT = "draft"            # 已存为平台草稿
STATUS_PUBLISHING = "publishing"  # 发布中
STATUS_PUBLISHED = "published"    # 已公开发布
STATUS_FAILED = "failed"          # 发布失败

ALL_STATUSES = (
    STATUS_PENDING,
    STATUS_PREVIEW,
    STATUS_DRAFT,
    STATUS_PUBLISHING,
    STATUS_PUBLISHED,
    STATUS_FAILED,
)

TERMINAL_STATUSES = (STATUS_PREVIEW, STATUS_DRAFT, STATUS_PUBLISHED, STATUS_FAILED)

# 旧数据兼容：把历史取值归一到新枚举
_LEGACY_STATUS_MAP = {
    "1": STATUS_PUBLISHED,
    "0": STATUS_FAILED,
    "success": STATUS_PUBLISHED,
    "succeeded": STATUS_PUBLISHED,
    "ok": STATUS_PUBLISHED,
    "done": STATUS_PUBLISHED,
    "fail": STATUS_FAILED,
    "error": STATUS_FAILED,
    "publishing": STATUS_PUBLISHING,
    "preview": STATUS_PREVIEW,
    "draft": STATUS_DRAFT,
    "pending": STATUS_PENDING,
}

# 需要新增的列定义（列名, SQLite 类型）
_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("published_url", "TEXT"),
    ("platform_item_id", "TEXT"),
    ("episode", "TEXT"),
    ("topic", "TEXT"),
    ("screenshot_dir", "TEXT"),
)


def normalize_status(value: Any) -> str:
    """把任意历史/别名状态值归一为标准枚举；未知值按 failed 处理。"""
    if value is None:
        return STATUS_PENDING
    text = str(value).strip().lower()
    if text in ALL_STATUSES:
        return text
    return _LEGACY_STATUS_MAP.get(text, STATUS_FAILED)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_publish_history_schema(db_path: Path) -> List[str]:
    """幂等地补齐 publish_history 的新增列、索引，并归一历史状态值。

    返回本次实际执行过的变更描述，便于调用方打印或断言。
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return []

    applied: List[str] = []
    with _connect(db_path) as conn:
        cur = conn.cursor()

        table_exists = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='publish_history'"
        ).fetchone()
        if not table_exists:
            return []

        existing = {row[1] for row in cur.execute("PRAGMA table_info(publish_history)")}
        for column, sql_type in _NEW_COLUMNS:
            if column not in existing:
                cur.execute(f"ALTER TABLE publish_history ADD COLUMN {column} {sql_type}")
                applied.append(f"ADD COLUMN {column}")

        # 幂等键：同一期同一平台只允许一条「已发布」记录（用索引兜住查重，不改历史数据）
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_publish_history_episode_platform "
            "ON publish_history(episode, platform_type)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_publish_history_status "
            "ON publish_history(status)"
        )

        # 归一历史状态值
        rows = cur.execute("SELECT id, status FROM publish_history").fetchall()
        for row in rows:
            target = normalize_status(row["status"])
            if target != row["status"]:
                cur.execute("UPDATE publish_history SET status = ? WHERE id = ?", (target, row["id"]))
                applied.append(f"STATUS {row['id']}: {row['status']} -> {target}")

        conn.commit()
    return applied


def create_record(
    db_path: Path,
    *,
    platform_type: int,
    platform_name: str,
    account_name: str,
    content_type: str,
    title: str,
    content: str = "",
    tags: Iterable[str] | str = (),
    files: Iterable[str] | str = (),
    status: str = STATUS_PUBLISHING,
    episode: Optional[str] = None,
    topic: Optional[str] = None,
    tieba_fid: Optional[str] = None,
    tieba_kw: Optional[str] = None,
    product_id: Optional[int] = None,
) -> Optional[int]:
    """写入一条发布记录，返回 history id；失败返回 None。"""
    import json

    if isinstance(tags, (list, tuple)):
        tags_str = ",".join(str(t) for t in tags)
    else:
        tags_str = str(tags or "")
    if isinstance(files, (list, tuple)):
        files_str = json.dumps(list(files), ensure_ascii=False)
    else:
        files_str = str(files or "")

    ensure_publish_history_schema(db_path)

    try:
        with _connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO publish_history
                (platform_type, platform_name, account_name, content_type, title, content,
                 tags, files, tieba_fid, tieba_kw, product_id, episode, topic, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                """,
                (
                    platform_type,
                    platform_name,
                    str(account_name),
                    content_type,
                    title,
                    content,
                    tags_str,
                    files_str,
                    tieba_fid or None,
                    tieba_kw or None,
                    product_id,
                    _as_episode(episode),
                    topic,
                    normalize_status(status),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
    except Exception as exc:  # noqa: BLE001 - 记录失败不应拖垮发布流程
        print(f"写入发布历史记录失败: {exc}", flush=True)
        return None


def finish_record(
    db_path: Path,
    history_id: Optional[int],
    status: str,
    *,
    published_url: Optional[str] = None,
    platform_item_id: Optional[str] = None,
    error_msg: Optional[str] = None,
    screenshot_dir: Optional[str] = None,
) -> None:
    """更新一条发布记录的终态。published_at 仅在真正发布成功时写入。"""
    if not history_id:
        return

    final_status = normalize_status(status)
    sets = ["status = ?", "error_msg = ?"]
    params: list[Any] = [final_status, str(error_msg) if error_msg else None]

    if published_url:
        sets.append("published_url = ?")
        params.append(str(published_url))
    if platform_item_id:
        sets.append("platform_item_id = ?")
        params.append(str(platform_item_id))
    if screenshot_dir:
        sets.append("screenshot_dir = ?")
        params.append(str(screenshot_dir))
    if final_status == STATUS_PUBLISHED:
        sets.append("published_at = datetime('now', 'localtime')")

    params.append(history_id)
    try:
        with _connect(db_path) as conn:
            conn.execute(f"UPDATE publish_history SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"更新发布历史记录失败: {exc}", flush=True)


def find_published_record(
    db_path: Path, episode: str | int, platform_type: int
) -> Optional[Dict[str, Any]]:
    """按「期数 + 平台」查找已成功发布的记录，用于重复发布拦截。"""
    ep = _as_episode(episode)
    if not ep:
        return None
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM publish_history
                WHERE episode = ? AND platform_type = ? AND status = ?
                ORDER BY id DESC LIMIT 1
                """,
                (ep, int(platform_type), STATUS_PUBLISHED),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def _as_episode(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
