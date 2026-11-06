#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""platform_feedback / publish_history 证据与人工复核字段的唯一 schema 来源。

- 所有迁移都是幂等 ADD COLUMN / CREATE INDEX，不删除或改写历史列；
- machine_evidence_version 在巡检写入时固定，人工复核不会改变它；
- 旧记录缺 machine_evidence_version 时，用只读的不可变字段补算 legacy 版本，
  避免复核结论绑定到会被回写的证据文本。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Set

HUMAN_REVIEWED_STATUSES = frozenset({
    "confirmed_normal", "confirmed_abnormal", "ignored",
})

_FEEDBACK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS platform_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id INTEGER NOT NULL,
    episode TEXT,
    platform_type INTEGER NOT NULL,
    platform_name TEXT NOT NULL,
    published_url TEXT NOT NULL,
    check_status TEXT NOT NULL,
    warning_text TEXT,
    content_visible INTEGER,
    http_status INTEGER,
    check_at TEXT NOT NULL,
    raw_snapshot TEXT,
    FOREIGN KEY (history_id) REFERENCES publish_history(id)
);
CREATE INDEX IF NOT EXISTS idx_platform_feedback_history ON platform_feedback(history_id);
CREATE INDEX IF NOT EXISTS idx_platform_feedback_episode_platform ON platform_feedback(episode, platform_type);
CREATE INDEX IF NOT EXISTS idx_platform_feedback_status ON platform_feedback(check_status);
CREATE INDEX IF NOT EXISTS idx_platform_feedback_history_time ON platform_feedback(history_id, check_at);
"""

_NEW_HEALTH_COLUMNS = (
    ("health_status", "TEXT"),
    ("health_checked_at", "TEXT"),
    ("health_details", "TEXT"),
)

# 证据链字段：旧记录默认为 unverified，避免历史脏数据被当作规则依据
_NEW_EVIDENCE_COLUMNS = (
    ("evidence_quality", "TEXT DEFAULT 'unverified'"),  # verified / unverified
    ("evidence_source", "TEXT"),                        # http_status / notice / page_marker / content_selector / human_lark_base
    ("evidence_text", "TEXT"),                          # 命中的具体判词或人工核实备注（截断）
    ("evidence_reason", "TEXT"),                        # unknown 的成因 / 人工复核说明
    ("machine_evidence_version", "TEXT"),               # 巡检写入时固定，人工复核不可变
)

# 人工复核字段：只有飞书复核台结论回流后才写
REVIEW_COLUMNS = (
    ("review_status", "TEXT"),                 # pending / confirmed_normal / confirmed_abnormal / ignored
    ("reviewer", "TEXT"),
    ("reviewed_at", "TEXT"),
    ("review_note", "TEXT"),
    ("review_source", "TEXT DEFAULT 'lark_base'"),
    ("review_record_id", "TEXT"),
    ("review_evidence_version", "TEXT"),
    ("review_decision", "TEXT"),               # 飞书原始单选结论
    ("review_manual_status", "TEXT"),          # 人工确认后的 check_status 标签
    ("review_writeback_at", "TEXT"),
    ("review_writeback_note", "TEXT"),
    ("review_remote_modified_at", "TEXT"),   # 绑定飞书行最后修改时间，支持结论变更重放
)

_MACHINE_VERSION_FIELDS = (
    "history_id", "episode", "platform_type", "platform_name", "published_url",
    "check_status", "warning_text", "content_visible", "http_status", "check_at",
    "raw_snapshot", "evidence_quality", "evidence_source", "evidence_text",
    "evidence_reason",
)

_LEGACY_VERSION_FIELDS = (
    "id", "history_id", "episode", "platform_type", "platform_name",
    "published_url", "check_at", "warning_text", "content_visible", "http_status",
)


def _value(record: Mapping[str, Any] | sqlite3.Row, key: str) -> Any:
    try:
        return record[key]
    except (KeyError, IndexError):
        return None


def _digest(prefix: str, record: Mapping[str, Any] | sqlite3.Row, fields: Iterable[str]) -> str:
    raw = {key: _value(record, key) for key in fields}
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    return prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def machine_evidence_version(result: Mapping[str, Any] | sqlite3.Row) -> str:
    """由一次机器巡检的完整原始结果计算证据版本；人工复核字段不参与。"""
    return _digest("mev2-", result, _MACHINE_VERSION_FIELDS)


def legacy_machine_evidence_version(row: Mapping[str, Any] | sqlite3.Row) -> str:
    """旧记录缺少机器版本时，只用不会被人工复核改写的字段补算。"""
    return _digest("mev2-legacy-", row, _LEGACY_VERSION_FIELDS)


def feedback_columns(db_path: Path) -> Set[str]:
    db_path = Path(db_path)
    if not db_path.exists():
        return set()
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(platform_feedback)")}


def ensure_feedback_schema(db_path: Path) -> List[str]:
    """幂等补齐健康列、证据列、人工复核列，并回填旧记录的机器证据版本。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return []

    applied: List[str] = []
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()

        existing_history = {row[1] for row in cur.execute("PRAGMA table_info(publish_history)")}
        for column, sql_type in _NEW_HEALTH_COLUMNS:
            if column not in existing_history:
                cur.execute(f"ALTER TABLE publish_history ADD COLUMN {column} {sql_type}")
                applied.append(f"ADD COLUMN publish_history.{column}")

        cur.executescript(_FEEDBACK_TABLE_SQL)
        existing_feedback = {row[1] for row in cur.execute("PRAGMA table_info(platform_feedback)")}
        for column, sql_type in _NEW_EVIDENCE_COLUMNS + REVIEW_COLUMNS:
            if column not in existing_feedback:
                cur.execute(f"ALTER TABLE platform_feedback ADD COLUMN {column} {sql_type}")
                applied.append(f"ADD COLUMN platform_feedback.{column}")

        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_platform_feedback_review_status
            ON platform_feedback(review_status)
            """
        )

        # 旧 platform_feedback 记录补一个稳定版本；只用不可变字段，避免复核回写后版本漂移。
        cur.execute(
            """
            SELECT id, history_id, episode, platform_type, platform_name,
                   published_url, check_at, warning_text, content_visible, http_status
            FROM platform_feedback
            WHERE machine_evidence_version IS NULL OR machine_evidence_version = ''
            """
        )
        legacy_rows = cur.fetchall()
        legacy_columns = (
            "id", "history_id", "episode", "platform_type", "platform_name",
            "published_url", "check_at", "warning_text", "content_visible", "http_status",
        )
        for values in legacy_rows:
            record: Dict[str, Any] = dict(zip(legacy_columns, values))
            cur.execute(
                "UPDATE platform_feedback SET machine_evidence_version = ? WHERE id = ?",
                (legacy_machine_evidence_version(record), record["id"]),
            )
        if legacy_rows:
            applied.append(f"BACKFILL platform_feedback.machine_evidence_version ({len(legacy_rows)} rows)")

        conn.commit()
    return applied
