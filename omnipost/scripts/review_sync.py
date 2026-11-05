#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书多维表格人工复核闭环：待复核记录上行 + 人工结论回写。

边界（对齐跨项目 Autoloop 路线）：
1. 只把本地 platform_feedback 的 unverified 证据同步到复核台；
2. 用户在飞书选择「复核结论」后，本地才写入 review_* 字段；
3. 只有「确认异常」且复核备注明确时，才把 evidence_quality 升级为 verified，
   并补齐人工证据；「确认正常 / 忽略」永不进入违规聚合；
4. 证据版本绑定巡检快照；版本变化会重置旧结论，避免拿旧结论复核新证据；
5. 规则变更仍为 proposal_only，本脚本不修改 compliance_check 的拦截规则。

配置文件与 preflight.env 共用：~/.config/omnipost/preflight.env（chmod 600）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = OMNIPOST_DIR.parent
sys.path.insert(0, str(OMNIPOST_DIR))

from conf import BASE_DIR  # noqa: E402
import feedback_schema  # noqa: E402
import preflight_notify  # noqa: E402

DB_PATH = Path(BASE_DIR) / "db" / "database.db"
LOGS_DIR = PROJECT_DIR / "logs" / "review"
PAYLOAD_TMP_DIR = LOGS_DIR / ".tmp"
CONFIG_PATH = preflight_notify.CONFIG_PATH

BASE_TOKEN = "RZlBbdXBsaFYuVsI09rcvvfrnje"
TABLE_ID = "tblxJk4djvWzelbV"
BASE_URL = "https://my.feishu.cn/base/RZlBbdXBsaFYuVsI09rcvvfrnje"
LARK_CLI = "lark-cli"

LOOKBACK_DAYS = 30
PAGE_LIMIT = 200
MAX_SYNC_RECORDS = 2000
TEXT_LIMIT = 900

REVIEW_PENDING = "待复核"
REVIEW_OK = "确认正常"
REVIEW_ABNORMAL = "确认异常"
REVIEW_IGNORED = "忽略"
VALID_REVIEWS = frozenset({REVIEW_PENDING, REVIEW_OK, REVIEW_ABNORMAL, REVIEW_IGNORED})

MANUAL_STATUS_MAP = {
    "警告/违规": "warned",
    "删除/下架": "deleted",
    "限流/折叠": "throttled",
    "审核超时": "under_review",
}
DEFAULT_MANUAL_STATUS = "warned"

_READ_FIELDS = (
    "证据ID", "本地反馈ID", "链接", "复核结论", "人工判定状态", "复核备注", "复核人",
    "证据版本", "本地回写状态", "本地回写时间", "回写说明", "最后修改人", "最后修改时间",
)


class ReviewSyncError(RuntimeError):
    """复核同步不可信时抛出；调用方不得把结果写成成功。"""


def ensure_review_schema(db_path: Path) -> List[str]:
    """兼容旧调用名；证据与复核 schema 统一由 feedback_schema.py 维护。"""
    return feedback_schema.ensure_feedback_schema(db_path)


def ensure_readonly_schema_for_dry_run(db_path: Path) -> None:
    """dry-run 严格只读：不 ALTER、不 BACKFILL，也不创建脚本日志目录。"""
    columns = feedback_schema.feedback_columns(db_path)
    required = {"id", "history_id", "episode", "platform_type", "platform_name",
                "published_url", "check_status", "check_at", "evidence_quality"}
    missing = sorted(required - columns)
    if missing:
        raise ReviewSyncError(f"本地 platform_feedback 缺少必需列: {', '.join(missing)}")


def _clip(value: Any, limit: int = TEXT_LIMIT) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


SELECT_FIELDS = frozenset({"复核结论", "人工判定状态", "本地回写状态"})


def _normalize_select_cells(value: Any) -> Any:
    """按 lark-cli CellValue 约定，把 select 字符串转成单元素数组。"""
    if isinstance(value, dict):
        return {key: _normalize_select_cells(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_select_cells(item) for item in value]
    return value


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_select_cells(payload)
    for records in normalized.values():
        if isinstance(records, dict):
            items = records.values()
        elif isinstance(records, list):
            items = records
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for field in SELECT_FIELDS & item.keys():
                if isinstance(item[field], str) and item[field]:
                    item[field] = [item[field]]
    return normalized


def evidence_version(row: sqlite3.Row) -> str:
    """读取机器巡检固定下来的不可变证据版本；旧库缺列时按不可变字段补算。"""
    version = _clip(row["machine_evidence_version"], 80)
    if version:
        return version
    return feedback_schema.legacy_machine_evidence_version(row)


def evidence_id(row: sqlite3.Row) -> str:
    return f"PF-{int(row['id'])}"


def _load_base_config() -> Tuple[str, str, str]:
    """配置优先级：进程环境 > 用户级 preflight.env > 脚本内置首次部署值。"""
    file_values = preflight_notify.env_file_values(CONFIG_PATH)
    base_token = (
        os.environ.get("LARK_REVIEW_BASE_TOKEN") or file_values.get("LARK_REVIEW_BASE_TOKEN") or BASE_TOKEN
    ).strip()
    table_id = (
        os.environ.get("LARK_REVIEW_TABLE_ID") or file_values.get("LARK_REVIEW_TABLE_ID") or TABLE_ID
    ).strip()
    base_url = (
        os.environ.get("LARK_REVIEW_BASE_URL") or file_values.get("LARK_REVIEW_BASE_URL") or BASE_URL
    ).strip()
    if not base_token or not table_id or not base_url:
        raise ReviewSyncError("LARK_REVIEW_BASE_TOKEN / LARK_REVIEW_TABLE_ID / LARK_REVIEW_BASE_URL 不能为空")
    return base_token, table_id, base_url


def _run_lark(args: List[str], *, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """调用 lark-cli；JSON body 写入项目内 0600 临时文件，避免命令行泄露数据。"""
    cmd = [LARK_CLI, "base", *args, "--as", "user", "--format", "json"]
    tmp: Optional[Path] = None
    if payload is not None:
        # lark-cli 的 @file 只接受当前工作目录下的相对路径；/var/folders 等绝对路径会被拒绝。
        PAYLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix="review-sync-", suffix=".json", dir=str(PAYLOAD_TMP_DIR)
        )
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(_normalize_payload(payload), handle, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            cmd.extend(["--json", f"@{tmp.relative_to(PROJECT_DIR)}"])
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=90, cwd=str(PROJECT_DIR)
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewSyncError(f"lark-cli 调用失败: {type(exc).__name__}") from exc
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    try:
        result = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ReviewSyncError(f"lark-cli 输出不是 JSON: {proc.stdout[:200]} {proc.stderr[:200]}") from exc
    if proc.returncode != 0 or not isinstance(result, dict) or not result.get("ok"):
        detail = result.get("error") if isinstance(result, dict) else None
        detail_text = "" if detail is None else str(detail)[:300]
        stderr_text = (proc.stderr or "").strip()[:300]
        stdout_text = "" if isinstance(result, dict) else (proc.stdout or "").strip()[:300]
        reason = "；".join(part for part in (detail_text, stderr_text, stdout_text) if part)
        raise ReviewSyncError(
            f"lark-cli 返回失败 (exit={proc.returncode}): {reason or '未提供错误详情'}"
        )
    return result


def _matrix_records(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 record-list/get 的矩阵返回还原成字段名 -> CellValue 映射。"""
    data = result.get("data", {})
    if not isinstance(data, dict):
        return []
    fields = data.get("fields") or []
    rows = data.get("data") or []
    record_ids = data.get("record_id_list") or []
    not_found = set(data.get("record_not_found") or [])
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        record_id = record_ids[index] if index < len(record_ids) else ""
        if record_id in not_found:
            continue
        item = {field: row[pos] if pos < len(row) else None for pos, field in enumerate(fields)}
        item["_record_id"] = record_id
        out.append(item)
    return out


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("name") or item.get("text") or ""))
            else:
                parts.append(str(item))
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        return str(value.get("name") or value.get("text") or "").strip()
    text = str(value).strip()
    # 飞书 url 字段读回常为 [url](url)；普通文本展示时还原裸 URL。
    if text.startswith("[") and text.endswith(")") and "](" in text:
        label, _, url = text[1:-1].partition("](")
        if url.strip():
            return url.strip()
        return label.strip()
    return text


def _parse_url_cell(value: Any) -> str:
    """飞书 url 文本字段读回可能是 Markdown 链接；仅保留其中的 URL。"""
    text = _cell_text(value)
    if text.startswith("[") and "](" in text and text.endswith(")"):
        inner = text[1:-1]
        label, _, url = inner.partition("](")
        return url.strip() or label.strip()
    return text


def _field_args() -> List[str]:
    args: List[str] = []
    for field in _READ_FIELDS:
        args.extend(["--field-id", field])
    return args


def _read_remote_records_with_rev(
    *, base_token: str, table_id: str
) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """串行分页读取复核台全部记录，同时返回本次快照的最大 rev。

    飞书写后短暂可能出现旧 rev：同一批 record_id 可能在不同页重复出现。
    按 record_id 去重；不同 record_id 共用同一证据ID 才视为真正的冲突。
    """
    remote: Dict[str, Dict[str, Any]] = {}
    seen_record_ids: set[str] = set()
    max_rev = 0
    offset = 0
    while offset < MAX_SYNC_RECORDS:
        args = [
            "+record-list", "--base-token", base_token, "--table-id", table_id,
            *_field_args(), "--limit", str(PAGE_LIMIT), "--offset", str(offset),
        ]
        result = _run_lark(args)
        page = result.get("data", {})
        try:
            max_rev = max(max_rev, int(page.get("rev") or 0))
        except (TypeError, ValueError):
            pass
        records = _matrix_records(result)
        for item in records:
            record_id = str(item.get("_record_id") or "")
            if record_id and record_id in seen_record_ids:
                continue
            if record_id:
                seen_record_ids.add(record_id)
            evidence_key = _cell_text(item.get("证据ID"))
            if not evidence_key:
                continue
            existing = remote.get(evidence_key)
            if existing and str(existing.get("_record_id") or "") != record_id:
                raise ReviewSyncError(f"复核台存在重复证据ID: {evidence_key}")
            item["复核结论"] = _cell_text(item.get("复核结论"))
            item["人工判定状态"] = _cell_text(item.get("人工判定状态"))
            item["复核备注"] = _cell_text(item.get("复核备注"))
            item["复核人"] = _cell_text(item.get("复核人"))
            item["证据版本"] = _cell_text(item.get("证据版本"))
            item["本地回写状态"] = _cell_text(item.get("本地回写状态"))
            item["本地回写时间"] = _cell_text(item.get("本地回写时间"))
            item["回写说明"] = _cell_text(item.get("回写说明"))
            item["最后修改人"] = _cell_text(item.get("最后修改人"))
            item["最后修改时间"] = _cell_text(item.get("最后修改时间"))
            remote[evidence_key] = item
        if not page.get("has_more"):
            return remote, max_rev
        if not records:
            raise ReviewSyncError("复核台声明 has_more=true，但本页没有记录")
        offset += len(records)
    raise ReviewSyncError(f"复核台记录超过 {MAX_SYNC_RECORDS} 条，拒绝按不完整范围回写")


def read_remote_records(*, base_token: str, table_id: str) -> Dict[str, Dict[str, Any]]:
    """兼容单次读取调用；同步主流程使用带 rev 的快照版本。"""
    return _read_remote_records_with_rev(base_token=base_token, table_id=table_id)[0]


def load_local_pending(db_path: Path, days: int = LOOKBACK_DAYS) -> List[sqlite3.Row]:
    """读取近 N 天 unverified 记录；人工复核过的旧证据不再重复上行。

    dry-run 可能面对尚未迁移的旧库，因此按真实列动态投影，绝不先写 schema。
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    columns = feedback_schema.feedback_columns(db_path)
    has_review_status = "review_status" in columns
    has_review_version = "review_evidence_version" in columns
    has_review_decision = "review_decision" in columns
    has_machine_version = "machine_evidence_version" in columns
    review_condition = (
        "AND (review_status IS NULL OR review_status = 'pending')" if has_review_status else ""
    )
    sql = f"""
        SELECT id, history_id, episode, platform_type, platform_name, published_url,
               check_status, warning_text, check_at, raw_snapshot, evidence_quality,
               evidence_source, evidence_text, evidence_reason,
               {'review_status' if has_review_status else 'NULL AS review_status'},
               {'review_evidence_version' if has_review_version else 'NULL AS review_evidence_version'},
               {'review_decision' if has_review_decision else 'NULL AS review_decision'},
               {'machine_evidence_version' if has_machine_version else 'NULL AS machine_evidence_version'}
        FROM platform_feedback
        WHERE evidence_quality = 'unverified'
          {review_condition}
          AND date(check_at) >= ?
        ORDER BY id ASC
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, (cutoff,)).fetchall()
    return list(rows)


def _record_payload(row: sqlite3.Row) -> Dict[str, Any]:
    version = evidence_version(row)
    reason = _clip(row["evidence_reason"] or "")
    verdict = _clip(row["evidence_text"] or row["warning_text"] or "")
    snapshot = _clip(row["raw_snapshot"] or "")
    evidence_summary = " | ".join(part for part in (reason, verdict, snapshot) if part)[:TEXT_LIMIT]
    return {
        "证据ID": evidence_id(row),
        "本地反馈ID": int(row["id"]),
        "期数": _clip(row["episode"] or "?", 120),
        "平台": _clip(row["platform_name"], 120),
        "平台类型": int(row["platform_type"] or 0),
        "链接": _clip(row["published_url"], 800),
        "巡检时间": _clip(row["check_at"], 60),
        "机器状态": _clip(row["check_status"], 60),
        "证据等级": _clip(row["evidence_quality"], 60),
        "成因": reason,
        "证据摘要": evidence_summary,
        "证据版本": version,
        "复核结论": REVIEW_PENDING,
        "人工判定状态": "未指定",
        "复核备注": "",
        "复核人": "",
        "本地回写状态": "待同步",
        "本地回写时间": "",
        "回写说明": "待人工在创作者后台核实",
    }


def _batch_create(base_token: str, table_id: str, payloads: List[Dict[str, Any]]) -> int:
    for start in range(0, len(payloads), PAGE_LIMIT):
        batch = payloads[start:start + PAGE_LIMIT]
        _run_lark(
            ["+record-batch-create", "--base-token", base_token, "--table-id", table_id],
            payload={"create_records": batch},
        )
    return len(payloads)


def _batch_update(base_token: str, table_id: str, updates: Dict[str, Dict[str, Any]]) -> int:
    items = list(updates.items())
    if not items:
        return 0
    for start in range(0, len(items), PAGE_LIMIT):
        batch = dict(items[start:start + PAGE_LIMIT])
        _run_lark(
            ["+record-batch-update", "--base-token", base_token, "--table-id", table_id],
            payload={"update_records": batch},
        )
    return len(items)


def _local_review_rows(db_path: Path, evidence_keys: List[str]) -> Dict[int, sqlite3.Row]:
    ids = []
    for key in evidence_keys:
        if not key.startswith("PF-"):
            continue
        try:
            ids.append(int(key[3:]))
        except ValueError:
            continue
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    columns = feedback_schema.feedback_columns(db_path)
    dynamic_columns = (
        "review_status", "review_decision", "review_evidence_version", "reviewer",
        "review_note", "review_source", "review_record_id", "review_manual_status",
        "machine_evidence_version", "review_remote_modified_at",
        "review_writeback_at", "review_writeback_note",
    )
    dynamic_select = ",\n                   ".join(
        column if column in columns else f"NULL AS {column}" for column in dynamic_columns
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT id, history_id, platform_type, platform_name, published_url, episode,
                   check_status, check_at, warning_text, evidence_quality, evidence_source,
                   evidence_text, evidence_reason, raw_snapshot,
                   {dynamic_select}
            FROM platform_feedback WHERE id IN ({placeholders})
            """,
            ids,
        ).fetchall()
    return {int(row["id"]): row for row in rows}


def _reviewer_name(remote: Dict[str, Any]) -> str:
    reviewer = _cell_text(remote.get("复核人"))
    if reviewer:
        return reviewer
    modified_by = _cell_text(remote.get("最后修改人"))
    return modified_by or "飞书复核台"


def _decision_note(decision: str, manual_status: str, note: str) -> str:
    """回写说明只由飞书人工可编辑字段构成，不依赖会被回写改写的本地证据字段。"""
    parts = [
        f"复核结论={decision}",
        f"人工判定状态={manual_status}",
    ]
    if note:
        parts.append(f"备注={note}")
    # 本地回写说明与飞书「回写说明」使用同一截断值，便于做幂等回执比对。
    return "；".join(parts)[:500]


def apply_remote_reviews(
    db_path: Path,
    remote: Dict[str, Dict[str, Any]],
    *,
    base_token: str,
    table_id: str,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """把飞书人工结论幂等回写本地；非法/过期/证据不匹配一律不升级。"""
    now = now or datetime.now()
    timestamp = now.isoformat(timespec="seconds")
    local_rows = _local_review_rows(db_path, list(remote))
    db_updates: List[Tuple[Any, ...]] = []
    remote_updates: Dict[str, Dict[str, Any]] = {}
    stats = {"reviewed": 0, "verified_upgraded": 0, "normal": 0, "ignored": 0, "conflicts": 0}

    for evidence_key, item in remote.items():
        decision = item["复核结论"]
        if decision not in VALID_REVIEWS:
            record_id = item.get("_record_id")
            if record_id:
                remote_updates[record_id] = {
                    "复核结论": REVIEW_PENDING,
                    "本地回写状态": "需人工检查",
                    "回写说明": "复核结论取值非法，已重置为待复核",
                }
            stats["conflicts"] += 1
            continue
        if decision == REVIEW_PENDING:
            continue
        feedback_id = _cell_text(item.get("本地反馈ID"))
        try:
            row_id = int(float(feedback_id or evidence_key.removeprefix("PF-")))
        except ValueError:
            stats["conflicts"] += 1
            record_id = item.get("_record_id")
            if record_id:
                remote_updates[record_id] = {
                    "本地回写状态": "需人工检查",
                    "回写说明": "本地反馈ID 无法解析，需人工检查",
                }
            continue
        row = local_rows.get(row_id)
        if row is None:
            stats["conflicts"] += 1
            record_id = item.get("_record_id")
            if record_id:
                remote_updates[record_id] = {
                    "本地回写状态": "需人工检查",
                    "回写说明": "本地不存在对应 platform_feedback 记录",
                }
            continue

        version = evidence_version(row)
        remote_version = item["证据版本"]
        if remote_version and remote_version != version:
            stats["conflicts"] += 1
            record_id = item.get("_record_id")
            if record_id:
                remote_updates[record_id] = {
                    "复核结论": REVIEW_PENDING,
                    "人工判定状态": "未指定",
                    "本地回写状态": "需人工检查",
                    "回写说明": "巡检证据版本已变化，旧复核结论失效，请重新复核",
                }
            continue
        if _parse_url_cell(item.get("链接")) and _parse_url_cell(item.get("链接")) != str(row["published_url"]):
            stats["conflicts"] += 1
            record_id = item.get("_record_id")
            if record_id:
                remote_updates[record_id] = {
                    "本地回写状态": "需人工检查",
                    "回写说明": "链接与本地证据不一致，拒绝回写",
                }
            continue

        # 用「本地回写时间」作为远端回执；结论、备注、人工判定状态、复核人、
        # 证据版本只要有一项变化就要重放，避免同一结论掩盖后续人工修正。
        note = item["复核备注"]
        reviewer = _reviewer_name(item)
        manual_label = item["人工判定状态"] or "未指定"
        manual_status = MANUAL_STATUS_MAP.get(manual_label, DEFAULT_MANUAL_STATUS)
        reason = _clip(row["evidence_reason"] or "")
        write_note = _decision_note(decision, manual_label, note)
        record_id = item.get("_record_id") or ""
        stored_manual_status = "" if decision in (REVIEW_OK, REVIEW_IGNORED) else manual_label

        if (
            item["本地回写状态"] == "已回写"
            and item["本地回写时间"]
            and item["本地回写时间"] == str(row["review_writeback_at"] or "")
            and str(row["review_writeback_note"] or "") == write_note
            and str(row["review_decision"] or "") == decision
            and str(row["review_manual_status"] or "") == stored_manual_status
            and str(row["reviewer"] or "") == reviewer
            and str(row["review_evidence_version"] or "") == version
        ):
            continue

        if decision == REVIEW_ABNORMAL:
            # 人工明确异常：必须带备注，且限定人工状态枚举，避免裸词或旧脏数据自动升级。
            if len(note.strip()) < 4 or manual_label not in MANUAL_STATUS_MAP:
                stats["conflicts"] += 1
                if record_id:
                    remote_updates[record_id] = {
                        "本地回写状态": "需人工检查",
                        "回写说明": "确认异常需选择人工判定状态，并填写不少于 4 个字的核实备注",
                    }
                continue
            review_status = "confirmed_abnormal"
            new_quality = "verified"
            new_check_status = manual_status
            new_evidence_source = "human_lark_base"
            new_evidence_text = _clip(note, 200)
            new_evidence_reason = "人工复核确认异常；" + (reason or "证据版本已绑定")
            new_snapshot = _clip(
                f"decision={decision}; manual_status={manual_label}; note={note}; "
                f"evidence_version={version}; reviewer={reviewer}",
                300,
            )
            stats["verified_upgraded"] += 1
        elif decision == REVIEW_OK:
            review_status = "confirmed_normal"
            # 人工确认正常不是「已验证异常证据」；从异常改判正常时也要降级。
            new_quality = "unverified"
            new_check_status = "alive"
            new_evidence_source = "human_lark_base_normal"
            new_evidence_text = _clip(note, 200)
            new_evidence_reason = "人工复核确认正常"
            new_snapshot = _clip(row["raw_snapshot"] or "", 300)
            stats["normal"] += 1
        else:
            review_status = "ignored"
            new_quality = str(row["evidence_quality"])
            new_check_status = str(row["check_status"])
            new_evidence_source = str(row["evidence_source"] or "")
            new_evidence_text = str(row["evidence_text"] or "")
            new_evidence_reason = str(row["evidence_reason"] or "")
            new_snapshot = str(row["raw_snapshot"] or "")
            stats["ignored"] += 1

        db_updates.append((
            review_status, reviewer, timestamp, note, "lark_base", record_id, version,
            decision, stored_manual_status, timestamp, write_note,
            new_quality, new_check_status, new_evidence_source, new_evidence_text,
            new_evidence_reason, new_snapshot, item["最后修改时间"], row_id,
        ))
        if record_id:
            remote_updates[record_id] = {
                "本地回写状态": "已回写",
                "本地回写时间": timestamp,
                "回写说明": _clip(write_note, 500),
            }
        stats["reviewed"] += 1

    if db_updates:
        with sqlite3.connect(str(db_path)) as conn:
            conn.executemany(
                """
                UPDATE platform_feedback SET
                    review_status=?, reviewer=?, reviewed_at=?, review_note=?,
                    review_source=?, review_record_id=?, review_evidence_version=?,
                    review_decision=?, review_manual_status=?, review_writeback_at=?,
                    review_writeback_note=?,
                    evidence_quality=?, check_status=?, evidence_source=?,
                    evidence_text=?, evidence_reason=?, raw_snapshot=?,
                    review_remote_modified_at=?
                WHERE id=?
                """,
                db_updates,
            )
            conn.commit()
    stats["remote_writeback"] = _batch_update(base_token, table_id, remote_updates)
    return stats


def sync_reviews(
    db_path: Path,
    *,
    days: int = LOOKBACK_DAYS,
    dry_run: bool = False,
    revision_settle_seconds: float = 1.2,
) -> Dict[str, Any]:
    """按「读取最新 rev 快照 → 回写结论 → 上行待复核 → 读回确认」顺序同步。

    飞书人工修改后 record-list 可能短暂返回旧 rev；等待后重读，只采用 rev
    更高的快照，然后统一回写一次，避免重复落库。
    """
    base_token, table_id, base_url = _load_base_config()
    if dry_run:
        ensure_readonly_schema_for_dry_run(db_path)
    else:
        ensure_review_schema(db_path)
    remote, remote_rev = _read_remote_records_with_rev(base_token=base_token, table_id=table_id)
    if not dry_run and revision_settle_seconds > 0:
        time.sleep(revision_settle_seconds)
        refreshed, refreshed_rev = _read_remote_records_with_rev(
            base_token=base_token, table_id=table_id
        )
        if refreshed_rev > remote_rev:
            remote, remote_rev = refreshed, refreshed_rev
    review_stats = apply_remote_reviews(
        db_path, remote, base_token=base_token, table_id=table_id
    ) if not dry_run else {
        "reviewed": 0, "verified_upgraded": 0, "normal": 0, "ignored": 0,
        "conflicts": 0, "remote_writeback": 0,
    }

    pending_rows = load_local_pending(db_path, days)
    new_payloads: List[Dict[str, Any]] = []
    version_resets: Dict[str, Dict[str, Any]] = {}
    for row in pending_rows:
        key = evidence_id(row)
        version = evidence_version(row)
        item = remote.get(key)
        if not item:
            new_payloads.append(_record_payload(row))
            continue
        # 证据版本变化：保留同一证据ID，重置人工结论，避免旧结论套到新证据。
        if item["证据版本"] and item["证据版本"] != version:
            version_resets[item["_record_id"]] = {
                **_record_payload(row),
                "本地回写状态": "待同步",
                "本地回写时间": "",
                "回写说明": "巡检证据版本已变化，已重置复核结论",
            }

    if not dry_run:
        created = _batch_create(base_token, table_id, new_payloads)
        reset = _batch_update(base_token, table_id, version_resets)
    else:
        created = len(new_payloads)
        reset = len(version_resets)

    # 飞书 record-list 可能短暂返回旧 rev，给写入留出收敛时间，再按证据ID读回确认。
    if not dry_run and (created or reset or review_stats["remote_writeback"]):
        time.sleep(1.0)
    verified_remote = read_remote_records(base_token=base_token, table_id=table_id) if not dry_run else remote
    expected = {evidence_id(row) for row in pending_rows}
    # dry-run 不写表；用「将创建数 + 已有数」表达预演结果，不把未写入当失败。
    missing = [] if dry_run else sorted(key for key in expected if key not in verified_remote)
    # 未复核项包括本地待复核记录 + 远程残留的非待复核但尚未回写项。
    pending_in_base = sum(
        1 for key, item in verified_remote.items()
        if key in expected and item["复核结论"] == REVIEW_PENDING
    ) + (created if dry_run else 0)
    unprocessed_reviews = sum(
        1 for key, item in verified_remote.items()
        if key in expected
        and item["复核结论"] != REVIEW_PENDING
        and item["本地回写状态"] != "已回写"
    )
    result = {
        "ok": not missing,
        "base_url": base_url,
        "base_token": base_token,
        "table_id": table_id,
        "dry_run": dry_run,
        "remote_records": len(verified_remote),
        "local_pending": len(pending_rows),
        "created": created,
        "version_resets": reset,
        "pending_in_base": pending_in_base,
        "unprocessed_reviews": unprocessed_reviews,
        "reviewed": review_stats["reviewed"],
        "verified_upgraded": review_stats["verified_upgraded"],
        "confirmed_normal": review_stats["normal"],
        "ignored": review_stats["ignored"],
        "conflicts": review_stats["conflicts"],
        "remote_writeback": review_stats["remote_writeback"],
        "missing_evidence_ids": missing[:10],
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not dry_run:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / f"review_sync_{datetime.now():%Y%m%d_%H%M%S}.json"
        log_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["log"] = str(log_path)
    if missing:
        raise ReviewSyncError(f"复核台读回缺少 {len(missing)} 条证据ID: {', '.join(missing[:5])}")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="飞书多维表格人工复核同步")
    parser.add_argument("--dry-run", action="store_true", help="只读取并计算，不写飞书或本地数据库")
    parser.add_argument("--days", type=int, default=LOOKBACK_DAYS, help="待复核记录回看天数")
    parser.add_argument("--db", default=str(DB_PATH), help="本地 SQLite 路径")
    args = parser.parse_args(argv)

    try:
        result = sync_reviews(Path(args.db), days=args.days, dry_run=args.dry_run)
    except ReviewSyncError as exc:
        print(f"❌ 复核同步失败: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
