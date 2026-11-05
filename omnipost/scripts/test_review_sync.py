#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书人工复核闭环固定回归样本：不访问飞书、不改生产库。

覆盖：
1. 确认异常（备注 + 人工判定状态）升级为 verified，且进入违规聚合；
2. 确认正常 / 忽略永不进入违规聚合，也不再算待人工复核；
3. 同一结论与回执重复同步时幂等，修改备注后重新回写；
4. 证据版本变化、链接不一致、非法结论、缺备注均拒绝升级。
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

SCRIPTS_DIR = Path(__file__).resolve().parent
OMNIPOST_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

import feedback_schema  # noqa: E402
import review_sync  # noqa: E402
import violation_feedback  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label} {detail}")


class FakeLark:
    """记录并回放到内存远端表，模拟飞书写回后下一轮 read 的结果。"""

    def __init__(self, remote: Dict[str, Dict[str, Any]]) -> None:
        self.remote = remote
        self.updates: list[Dict[str, Any]] = []

    def __call__(self, base_token: str, table_id: str, updates: Dict[str, Dict[str, Any]]) -> int:
        _ = base_token, table_id
        self.updates.append(updates)
        for record_id, fields in updates.items():
            for item in self.remote.values():
                if item.get("_record_id") == record_id:
                    item.update(fields)
        return len(updates)


def _create_db(path: Path) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "CREATE TABLE publish_history ("
            "id INTEGER PRIMARY KEY, episode TEXT, platform_type INTEGER, "
            "platform_name TEXT, published_url TEXT, status TEXT, created_at TEXT)"
        )
        conn.commit()
    feedback_schema.ensure_feedback_schema(path)
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        (1, "15", 11, "微博", "https://example.com/abnormal", "throttled", "反爬"),
        (2, "15", 9, "知乎", "https://example.com/normal", "unknown", "登录墙"),
        (3, "15", 8, "搜狐号", "https://example.com/ignored", "unknown", "容器未命中"),
        (4, "15", 5, "百家号", "https://example.com/replay", "unknown", "反爬"),
        (5, "15", 15, "企鹅号", "https://example.com/stale", "unknown", "反爬"),
        (6, "15", 7, "今日头条", "https://example.com/bad-url", "unknown", "反爬"),
        (7, "15", 13, "网易号", "https://example.com/invalid", "unknown", "反爬"),
        (8, "15", 14, "大鱼号", "https://example.com/short-note", "unknown", "反爬"),
    ]
    with sqlite3.connect(str(path)) as conn:
        for row in rows:
            conn.execute(
                "INSERT INTO publish_history (id, episode, platform_type, platform_name, "
                "published_url, status, created_at) VALUES (?, ?, ?, ?, ?, 'published', ?)",
                (row[0], row[1], row[2], row[3], row[4], now[:10]),
            )
            conn.execute(
                "INSERT INTO platform_feedback (history_id, episode, platform_type, platform_name, "
                "published_url, check_status, check_at, raw_snapshot, evidence_quality, "
                "evidence_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unverified', ?)",
                (row[0], row[1], row[2], row[3], row[4], row[5], now, "机器快照", row[6]),
            )
        conn.commit()
    # 固定机器证据版本；本测试只验证复核回写，不模拟巡检重算。
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM platform_feedback").fetchall():
            version = feedback_schema.legacy_machine_evidence_version(row)
            conn.execute(
                "UPDATE platform_feedback SET machine_evidence_version=? WHERE id=?",
                (version, row["id"]),
            )
        conn.commit()


def _versions(path: Path) -> Dict[int, str]:
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        return {
            int(row["id"]): review_sync.evidence_version(row)
            for row in conn.execute("SELECT * FROM platform_feedback")
        }


def _remote_item(
    row_id: int,
    url: str,
    version: str,
    decision: str,
    *,
    manual: str = "未指定",
    note: str = "",
    reviewer: str = "测试复核人",
    writeback_state: str = "待同步",
    writeback_at: str = "",
    writeback_note: str = "",
) -> Dict[str, Any]:
    return {
        "_record_id": f"rec-{row_id}",
        "证据ID": f"PF-{row_id}",
        "本地反馈ID": str(row_id),
        "链接": url,
        "复核结论": decision,
        "人工判定状态": manual,
        "复核备注": note,
        "复核人": reviewer,
        "证据版本": version,
        "本地回写状态": writeback_state,
        "本地回写时间": writeback_at,
        "回写说明": writeback_note,
        "最后修改人": reviewer,
        "最后修改时间": "2026-11-05 19:00:00",
    }


def test_revision_snapshot_selection() -> None:
    """同步主流程必须采用 rev 更高的复核台快照，再统一回写一次。"""
    from unittest.mock import patch

    pending = {"PF-1": {"_record_id": "rec-1", "复核结论": review_sync.REVIEW_PENDING}}
    confirmed = {"PF-1": {"_record_id": "rec-1", "复核结论": review_sync.REVIEW_OK}}
    snapshots = [(pending, 10), (confirmed, 11)]
    captured: Dict[str, Any] = {}
    zero_stats = {
        "reviewed": 0, "verified_upgraded": 0, "normal": 0,
        "ignored": 0, "conflicts": 0, "remote_writeback": 0,
    }

    def fake_read_with_rev(*, base_token: str, table_id: str):
        _ = base_token, table_id
        return snapshots.pop(0) if snapshots else (confirmed, 11)

    def fake_apply(db_path, remote, *, base_token, table_id, now=None):
        _ = db_path, base_token, table_id, now
        captured["decision"] = remote["PF-1"]["复核结论"]
        return dict(zero_stats)

    with tempfile.TemporaryDirectory(prefix="review-rev-") as td:
        with patch.object(review_sync, "_load_base_config", return_value=("fake", "fake", "fake-url")), \
             patch.object(review_sync, "ensure_review_schema", return_value=[]), \
             patch.object(review_sync, "_read_remote_records_with_rev", side_effect=fake_read_with_rev), \
             patch.object(review_sync, "apply_remote_reviews", side_effect=fake_apply), \
             patch.object(review_sync, "load_local_pending", return_value=[]), \
             patch.object(review_sync, "_batch_create", return_value=0), \
             patch.object(review_sync, "_batch_update", return_value=0), \
             patch.object(review_sync, "read_remote_records", return_value=confirmed), \
             patch.object(review_sync.time, "sleep", return_value=None), \
             patch.object(review_sync, "LOGS_DIR", Path(td)):
            result = review_sync.sync_reviews(
                Path(td) / "rev.db", days=30, dry_run=False, revision_settle_seconds=0.01
            )
        check("同步采用更高 rev 的人工结论", captured.get("decision") == review_sync.REVIEW_OK, str(captured))
        check("rev 快照选择后同步仍成功", result["ok"] is True, str(result))
        check("两次快照读取后只回写一次", len(snapshots) == 0, str(snapshots))


def test_stale_revision_dedupe() -> None:
    """飞书写后短暂返回同一批 record_id 时，合并而非误判重复证据ID。"""
    original = review_sync._run_lark
    rows = [
        ["PF-1", 1, ["待复核"], ["待同步"]],
        ["PF-2", 2, ["待复核"], ["待同步"]],
    ]
    fields = ["证据ID", "本地反馈ID", "复核结论", "本地回写状态"]
    calls = {"n": 0}

    def fake_run_lark(args: list[str], *, payload: Any = None) -> Dict[str, Any]:
        _ = payload
        calls["n"] += 1
        return {
            "ok": True,
            "data": {
                "fields": fields,
                "data": rows,
                "record_id_list": ["rec-1", "rec-2"],
                "has_more": calls["n"] == 1,
            },
        }

    review_sync._run_lark = fake_run_lark
    try:
        remote = review_sync.read_remote_records(base_token="fake", table_id="fake")
        check("旧 rev 重复页按 record_id 去重", len(remote) == 2, str(remote))
        check("旧 rev 重复页读两页后返回", calls["n"] == 2, str(calls))
    finally:
        review_sync._run_lark = original


def _row(path: Path, row_id: int) -> Dict[str, Any]:
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(
            "SELECT * FROM platform_feedback WHERE id=?", (row_id,)
        ).fetchone())


def main() -> int:
    remote: Dict[str, Dict[str, Any]] = {}
    fake = FakeLark(remote)
    original = review_sync._batch_update
    review_sync._batch_update = fake
    try:
        with tempfile.TemporaryDirectory(prefix="review-sync-") as td:
            db = Path(td) / "test.db"
            _create_db(db)
            versions = _versions(db)
            fixed_now = datetime(2026, 11, 5, 19, 0, 0)
            remote.update({
                "PF-1": _remote_item(
                    1, "https://example.com/abnormal", versions[1],
                    review_sync.REVIEW_ABNORMAL, manual="限流/折叠",
                    note="创作者后台确认限流，内容已折叠",
                ),
                "PF-2": _remote_item(
                    2, "https://example.com/normal", versions[2],
                    review_sync.REVIEW_OK, note="创作者后台可见且正常",
                ),
                "PF-3": _remote_item(
                    3, "https://example.com/ignored", versions[3],
                    review_sync.REVIEW_IGNORED, note="测试链接无需复核",
                ),
                "PF-4": _remote_item(
                    4, "https://example.com/replay", versions[4],
                    review_sync.REVIEW_OK, note="创作者后台确认正文完整",
                ),
                "PF-5": _remote_item(
                    5, "https://example.com/stale", "mev2-stale-version",
                    review_sync.REVIEW_ABNORMAL, manual="警告/违规",
                    note="创作者后台确认存在违规警告",
                ),
                "PF-6": _remote_item(
                    6, "https://example.com/tampered", versions[6],
                    review_sync.REVIEW_ABNORMAL, manual="警告/违规",
                    note="创作者后台确认存在违规警告",
                ),
                "PF-7": _remote_item(
                    7, "https://example.com/invalid", versions[7], "随便写的结论",
                ),
                "PF-8": _remote_item(
                    8, "https://example.com/short-note", versions[8],
                    review_sync.REVIEW_ABNORMAL, manual="警告/违规", note="嗯",
                ),
            })

            print("\n[1] 人工结论首次回写")
            stats = review_sync.apply_remote_reviews(
                db, remote, base_token="fake", table_id="fake", now=fixed_now
            )
            check("确认异常回写 1 条", stats["verified_upgraded"] == 1, str(stats))
            check("确认正常回写 2 条", stats["normal"] == 2, str(stats))
            check("忽略回写 1 条", stats["ignored"] == 1, str(stats))
            check("过期版本/错链接/非法结论/短备注共 4 个冲突", stats["conflicts"] == 4, str(stats))
            abnormal = _row(db, 1)
            check("确认异常升级为 verified", abnormal["evidence_quality"] == "verified", str(abnormal))
            check("人工判定状态映射为 throttled", abnormal["check_status"] == "throttled", str(abnormal))
            check("人工证据来源已记录", abnormal["evidence_source"] == "human_lark_base", str(abnormal))
            check("确认正常保持 unverified", _row(db, 2)["evidence_quality"] == "unverified")
            check("确认正常状态改为 alive", _row(db, 2)["check_status"] == "alive")
            check("忽略保留机器成因", _row(db, 3)["evidence_reason"] == "容器未命中")
            check("确认正常的机器版本不变", _row(db, 2)["machine_evidence_version"] == versions[2])
            check("过期版本未升级", _row(db, 5)["evidence_quality"] == "unverified")
            check("链接不一致未升级", _row(db, 6)["evidence_quality"] == "unverified")
            check("非法结论未升级", _row(db, 7)["evidence_quality"] == "unverified")
            check("缺备注未升级", _row(db, 8)["evidence_quality"] == "unverified")

            records = violation_feedback.load_feedback(db, days=30)
            signals = violation_feedback.extract_violation_signals(records)
            check("只有人工确认异常进入信号桶", len(signals["buckets"]) == 1, str(signals))
            check("人工已复核 4 条", signals["human_reviewed_count"] == 4, str(signals))
            check("待人工复核仅剩 4 条", signals["unverified_count"] == 4, str(signals))
            words = violation_feedback.build_dynamic_banned_words(signals)
            check("聚合结果仍为观察提案", words["status"] == "observation_proposal")
            check("人工已复核计数写入产物", words["stats"]["human_reviewed_count"] == 4)
            check("high_risk 仍等于静态基线", words["high_risk"] == sorted(violation_feedback.STATIC_BANNED_WORDS))

            print("\n[2] 同一结论与回执幂等重放")
            before_updates = len(fake.updates)
            stats2 = review_sync.apply_remote_reviews(
                db, remote, base_token="fake", table_id="fake", now=fixed_now
            )
            check("同一回执不重复写本地", stats2["reviewed"] == 0, str(stats2))
            check("仅残留冲突项写 1 批回执", len(fake.updates) == before_updates + 1, str(fake.updates[-1:]))

            print("\n[3] 用户只修改复核备注时重新回写")
            remote["PF-4"]["复核备注"] = "创作者后台复查正文完整且推荐流可见"
            stats3 = review_sync.apply_remote_reviews(
                db, remote, base_token="fake", table_id="fake",
                now=fixed_now + timedelta(minutes=1),
            )
            check("备注变化触发重新回写", stats3["reviewed"] == 1, str(stats3))
            check("最新备注已落库", "推荐流可见" in str(_row(db, 4)["review_note"]), str(_row(db, 4)))

            print("\n[4] 确认异常改判确认正常时降级")
            remote["PF-1"]["复核结论"] = review_sync.REVIEW_OK
            remote["PF-1"]["人工判定状态"] = "未指定"
            remote["PF-1"]["复核备注"] = "创作者后台复查已恢复正常"
            stats4 = review_sync.apply_remote_reviews(
                db, remote, base_token="fake", table_id="fake",
                now=fixed_now + timedelta(minutes=2),
            )
            row_after = _row(db, 1)
            check("改判触发重新回写", stats4["normal"] == 1, str(stats4))
            check("改判后证据降级为 unverified", row_after["evidence_quality"] == "unverified", str(row_after))
            check("改判后状态为 alive", row_after["check_status"] == "alive", str(row_after))
            signals_after = violation_feedback.extract_violation_signals(
                violation_feedback.load_feedback(db, days=30)
            )
            check("改判后异常信号桶清空", len(signals_after["buckets"]) == 0, str(signals_after))

            print("\n[5] 旧库缺复核列时只读兼容")
            legacy_db = Path(td) / "legacy.db"
            with sqlite3.connect(str(legacy_db)) as conn:
                conn.execute(
                    "CREATE TABLE publish_history (id INTEGER PRIMARY KEY, episode TEXT, "
                    "platform_type INTEGER, platform_name TEXT, published_url TEXT, status TEXT)"
                )
                conn.execute(
                    "CREATE TABLE platform_feedback (id INTEGER PRIMARY KEY, history_id INTEGER, "
                    "episode TEXT, platform_type INTEGER, platform_name TEXT, published_url TEXT, "
                    "check_status TEXT, warning_text TEXT, content_visible INTEGER, http_status INTEGER, "
                    "check_at TEXT, raw_snapshot TEXT, evidence_quality TEXT, evidence_source TEXT, "
                    "evidence_text TEXT, evidence_reason TEXT)"
                )
                conn.execute(
                    "INSERT INTO platform_feedback (id, history_id, episode, platform_type, "
                    "platform_name, published_url, check_status, check_at, raw_snapshot, "
                    "evidence_quality, evidence_reason) VALUES "
                    "(1, 1, '15', 9, '知乎', 'https://example.com/legacy', 'unknown', ?, "
                    "'快照', 'unverified', '反爬')",
                    (datetime.now().isoformat(timespec="seconds"),),
                )
                conn.commit()
            legacy_records = violation_feedback.load_feedback(legacy_db, days=30)
            check("旧库仍可读取巡检记录", len(legacy_records) == 1, str(legacy_records))
            check("旧库 review_status 投影为空", legacy_records[0]["review_status"] is None, str(legacy_records))

            print("\n[6] 飞书旧 rev 快照收敛")
            test_revision_snapshot_selection()
            test_stale_revision_dedupe()
    finally:
        review_sync._batch_update = original

    print(f"\n结果：PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
