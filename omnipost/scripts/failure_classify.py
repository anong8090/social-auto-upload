#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布失败分类与记录（Level 0 基线：确定性分类码 + 去重键 + 待修堆叠）。

设计原则：
- 只做「失败归因」的静态判定，不打开浏览器、不改业务数据；
- 分类码与 publish_history.error_msg 同步落库，供后续汇总与自动补发复用；
- 同一「期数 + 平台 + 分类码」只记录一次失败，避免重复堆叠；
- PAGE_CHANGED 累计 2 次自动生成待修记录到 logs/failures/；
- 供 publish_episode.py、run_daily_preflight.py、未来 CLI 复用。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OMNIPOST_DIR))

from myUtils import preflight  # noqa: E402
from myUtils.publish_record import (  # noqa: E402
    STATUS_FAILED,
)
from myUtils import preflight  # noqa: E402

# 默认数据库路径（复用 publish_episode.py 的逻辑）
from conf import BASE_DIR  # noqa: E402
DEFAULT_DB_PATH = Path(BASE_DIR) / "db" / "database.db"

# ============================================================ 失败分类码（与 AUTOMATION_ROADMAP.md 第 4 件对齐）

FAILURE_CODES = (
    "LOGIN_EXPIRED",   # Cookie 过期 / 账号被踢 / 二次验证拦截
    "RISK_CONTROL",    # 平台风控：发布频次限制、内容审核拦截、设备指纹异常
    "PAGE_CHANGED",    # 页面改版：选择器失效、元素找不到、流程跳转异常
    "NETWORK",         # 网络抖动 / 超时 / DNS / 连接重置
    "OTHER",           # 兜底：未知异常、断言失败、第三方库报错
)

# 关键词映射表：按优先级从高到低匹配（先匹配的优先）
_FAILURE_PATTERNS: List[Tuple[str, List[str]]] = [
    ("LOGIN_EXPIRED", [
        r"cookie.{0,10}(失效|过期|expired|invalid)",
        r"登录.{0,10}(失效|过期|expired)",
        r"session.{0,10}(失效|过期|expired)",
        r"账号.{0,10}(被踢|下线|异地)",
        r"二次验证|验证码|滑块|人机验证",
        r"need.{0,5}login|login.{0,5}requir",
        r"unauthorized|401|403",
    ]),
    ("RISK_CONTROL", [
        r"风控|risk.{0,5}control|频次.{0,5}限制|发布.{0,5}过快",
        r"内容.{0,10}(违规|审核|拦截|敏感)",
        r"设备.{0,10}(异常|指纹|识别)",
        r"操作.{0,10}(频繁|受限|限制)",
        r"违反.{0,10}社区|违反.{0,10}规则",
    ]),
    ("PAGE_CHANGED", [
        r"找不到.{0,10}(元素|选择器|按钮|输入框|iframe)",
        r"selector.{0,10}(not found|timeout|invalid)",
        r"element.{0,10}(not found|not visible|not attached)",
        r"页面.{0,10}(改版|变更|跳转|异常|加载失败)",
        r"等待.{0,10}超时|timeout.{0,10}wait",
        r"no such element|element not interactable",
        r"frame.{0,10}(not found|detached)",
        r"navigation.{0,10}(failed|timeout)",
    ]),
    ("NETWORK", [
        r"network.{0,10}(error|timeout|unreachable|reset)",
        r"连接.{0,10}(超时|重置|中断|失败)",
        r"dns.{0,10}(解析|失败|错误)",
        r"socket.{0,10}(hang|closed|timeout)",
        r"read.{0,10}timeout|connect.{0,10}timeout",
        r"ssl.{0,10}(error|handshake|certificate)",
        r"http.{0,10}(500|502|503|504|408)",
        r"ERR_(CONNECTION|TIMEOUT|NAME_NOT_RESOLVED)",
    ]),
    ("OTHER", []),  # 兜底
]


# ============================================================ 核心分类逻辑

def classify_failure(error_msg: str, platform_type: int = 0) -> str:
    """根据错误文本返回失败分类码；未匹配返回 OTHER。

    Args:
        error_msg: 异常完整文本（含 traceback）
        platform_type: 平台类型，用于上下文辅助判断（预留）

    Returns:
        分类码：LOGIN_EXPIRED / RISK_CONTROL / PAGE_CHANGED / NETWORK / OTHER
    """
    text = (error_msg or "").lower()
    for code, patterns in _FAILURE_PATTERNS:
        for pat in patterns:
            if re.search(pat, text, flags=re.IGNORECASE):
                return code
    return "OTHER"


# ============================================================ 失败记录与去重

FAILURES_LOG_DIR = OMNIPOST_DIR.parent / "logs" / "failures"
FAILURES_LOG_DIR.mkdir(parents=True, exist_ok=True)

# 数据库层面的去重键：同一期同一平台同一分类码只记录一次
_DEDUPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_publish_history_episode_platform_failure
ON publish_history(episode, platform_type, failure_code)
WHERE failure_code IS NOT NULL
"""

# 新增列：failure_code
_NEW_FAILURE_COLUMNS = (
    ("failure_code", "TEXT"),
    ("failure_step", "TEXT"),     # 失败步骤：login / upload_cover / write_body / inline_image / ai_declare / publish_click / collect_link
    ("failure_raw", "TEXT"),      # 原始错误摘要（截断 500 字符）
)


def ensure_failure_schema(db_path: Path) -> List[str]:
    """幂等补齐 publish_history 的失败分类列与去重索引。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return []

    applied: List[str] = []
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        table_exists = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='publish_history'"
        ).fetchone()
        if not table_exists:
            return []

        existing = {row[1] for row in cur.execute("PRAGMA table_info(publish_history)")}
        for column, sql_type in _NEW_FAILURE_COLUMNS:
            if column not in existing:
                cur.execute(f"ALTER TABLE publish_history ADD COLUMN {column} {sql_type}")
                applied.append(f"ADD COLUMN {column}")

        cur.execute(_DEDUPE_INDEX_SQL)
        conn.commit()
    return applied


def _failure_marker_path(episode: str, platform_type: int, code: str) -> Path:
    """生成去重标记文件路径（文件系统级兜底，防数据库未提交时重复）。"""
    key = f"{episode}:{platform_type}:{code}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return FAILURES_LOG_DIR / f".fail-{digest}.json"


def record_failure(
    db_path: Path,
    episode: str,
    platform_type: int,
    platform_name: str,
    error_msg: str,
    *,
    history_id: Optional[int] = None,
    failure_step: Optional[str] = None,
) -> Tuple[str, bool]:
    """记录一次失败，返回 (分类码, 是否为新记录)。

    - 数据库层面：若该 (episode, platform_type, failure_code) 已存在失败记录，视为重复，不再写入；
    - 文件系统层面：同理写入去重标记；
    - history_id 若提供，直接更新该条记录；否则尝试按 episode+platform_type 查找最新 publishing/failed 记录更新。
    """
    code = classify_failure(error_msg, platform_type)
    raw = (error_msg or "")[:500]

    # 文件系统去重标记
    marker = _failure_marker_path(episode, platform_type, code)
    is_new = False
    if not marker.exists():
        is_new = True
        marker.write_text(
            json.dumps(
                {
                    "episode": episode,
                    "platform_type": platform_type,
                    "platform_name": platform_name,
                    "failure_code": code,
                    "failure_step": failure_step,
                    "error_summary": raw,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # 数据库记录
    ensure_failure_schema(db_path)
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()

            # 先查是否已有同分类码记录（同期同平台同分类码）
            ep = str(episode).strip()
            existing = cur.execute(
                "SELECT 1 FROM publish_history WHERE episode = ? AND platform_type = ? AND failure_code = ? AND status = ?",
                (ep, int(platform_type), code, STATUS_FAILED),
            ).fetchone()
            if existing:
                return code, False  # 已记录过同类失败

            updated = False
            if history_id:
                cur.execute(
                    """
                    UPDATE publish_history
                    SET status = ?, error_msg = ?, failure_code = ?, failure_step = ?, failure_raw = ?
                    WHERE id = ?
                    """,
                    (STATUS_FAILED, raw, code, failure_step, raw, history_id),
                )
                updated = cur.rowcount > 0
            else:
                # 兜底：更新该期该平台最新的一条 publishing/failed 记录
                # SQLite 不支持 UPDATE ... ORDER BY LIMIT，用子查询绕过
                cur.execute(
                    """
                    UPDATE publish_history
                    SET status = ?, error_msg = ?, failure_code = ?, failure_step = ?, failure_raw = ?
                    WHERE id = (
                        SELECT id FROM publish_history
                        WHERE episode = ? AND platform_type = ? AND status IN (?, ?)
                        ORDER BY id DESC LIMIT 1
                    )
                    """,
                    (STATUS_FAILED, raw, code, failure_step, raw, ep, int(platform_type),
                     "publishing", "failed"),
                )
                updated = cur.rowcount > 0

            # 如果没有更新到任何行（如该期该平台还无 publishing/failed 记录），插入新记录
            if not updated:
                cur.execute(
                    """
                    INSERT INTO publish_history
                    (platform_type, platform_name, account_name, content_type, title, content,
                     tags, files, tieba_fid, tieba_kw, product_id, episode, topic, status,
                     error_msg, failure_code, failure_step, failure_raw, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                    """,
                    (
                        int(platform_type),
                        platform_name,
                        "默认账号",
                        "article",
                        f"失败记录: {raw[:50]}",
                        "",
                        "",
                        "",
                        None,
                        None,
                        None,
                        ep,
                        episode,
                        STATUS_FAILED,
                        raw,
                        code,
                        failure_step,
                        raw,
                    ),
                )
            conn.commit()
    except Exception:
        # 记录失败不应拖垮主流程
        pass

    return code, is_new


def get_failure_count(episode: str, platform_type: int, code: str, db_path: Path) -> int:
    """查询某期某平台某分类码的失败累计次数（含去重后的唯一记录数）。"""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()
            ep = str(episode).strip()
            row = cur.execute(
                "SELECT COUNT(*) FROM publish_history WHERE episode = ? AND platform_type = ? AND failure_code = ?",
                (ep, int(platform_type), code),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


# ============================================================ PAGE_CHANGED 待修堆叠

def _pending_fix_path(platform_type: int, platform_name: str, failure_step: str) -> Path:
    """生成待修记录路径：logs/failures/fix_<platform>_<step>.json"""
    # 使用平台 type 代替名称，避免中文被过滤
    safe_name = f"type{platform_type}"
    safe_step = re.sub(r"[^a-zA-Z0-9_-]", "_", failure_step or "unknown")
    return FAILURES_LOG_DIR / f"fix_{safe_name}_{safe_step}.json"


def maybe_create_pending_fix(
    episode: str,
    platform_type: int,
    platform_name: str,
    failure_step: Optional[str],
    error_msg: str,
    screenshot_dir: Optional[str] = None,
) -> Optional[Path]:
    """PAGE_CHANGED 同类（同平台同步骤）累计 2 次时生成待修记录；返回记录路径，未触发返回 None。"""
    code = classify_failure(error_msg, platform_type)
    if code != "PAGE_CHANGED":
        return None

    # 统计同平台同步骤的 PAGE_CHANGED 累计次数（跨期数）
    try:
        with sqlite3.connect(str(DEFAULT_DB_PATH)) as conn:
            cur = conn.cursor()
            step = failure_step or "unknown"
            row = cur.execute(
                "SELECT COUNT(*) FROM publish_history WHERE platform_type = ? AND failure_code = ? AND failure_step = ?",
                (int(platform_type), code, step),
            ).fetchone()
            count = int(row[0]) if row else 0
    except Exception:
        count = 0

    if count < 2:
        return None

    path = _pending_fix_path(platform_type, platform_name, failure_step or "unknown")
    payload = {
        "platform_type": platform_type,
        "platform_name": platform_name,
        "failure_step": failure_step,
        "first_seen_episode": episode,
        "accumulated_count": count,
        "latest_error": (error_msg or "")[:500],
        "screenshot_dir": screenshot_dir,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "pending",  # pending / fixed / dismissed
    }
    # 只在不存在或状态为 pending 时写入/更新
    if not path.exists():
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("status") == "pending":
                existing.update(
                    {
                        "accumulated_count": count,
                        "latest_error": (error_msg or "")[:500],
                        "screenshot_dir": screenshot_dir,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return path


def list_pending_fixes() -> List[Dict[str, Any]]:
    """列出所有待修记录，供人工/脚本领取。"""
    out = []
    for p in sorted(FAILURES_LOG_DIR.glob("fix_*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def mark_fix_resolved(platform_type: int, platform_name: str, failure_step: str) -> bool:
    """标记待修记录为已解决。"""
    path = _pending_fix_path(platform_type, platform_name, failure_step)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = "fixed"
        data["resolved_at"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


# ============================================================ 自动补发判定

def should_auto_retry(code: str) -> bool:
    """判定该分类码是否触发次日自动补发（仅 NETWORK）。"""
    return code == "NETWORK"


def get_yesterday_network_failures(db_path: Path) -> List[Dict[str, Any]]:
    """读取昨天的 NETWORK 类失败，用于次日补发。"""
    out = []
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # 简单按日期过滤：created_at 为昨天
            yesterday = datetime.now().strftime("%Y-%m-%d")
            # SQLite date() 需要标准格式，created_at 存的是本地时间 'YYYY-MM-DD HH:MM:SS'
            rows = cur.execute(
                """
                SELECT * FROM publish_history
                WHERE failure_code = 'NETWORK'
                  AND date(created_at) = date('now', '-1 day')
                  AND status = ?
                ORDER BY id ASC
                """,
                (STATUS_FAILED,),
            ).fetchall()
            for row in rows:
                out.append(dict(row))
    except Exception:
        pass
    return out


# ============================================================ CLI 入口

def _sample_errors() -> Dict[str, str]:
    return {
        "login": "Playwright timeout: cookie 失效，请重新登录账号",
        "risk": "发布被拦截：内容违规，疑似敏感词触发风控",
        "page": "Element not found: selector 'button.publish-btn' timeout 30000ms",
        "net": "Network error: ERR_CONNECTION_TIMEOUT at https://baijiahao.baidu.com/builder/rc/edit",
        "other": "AssertionError: unexpected response structure",
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="失败分类 CLI - 验证样本与手动分类")
    parser.add_argument("--error", help="错误文本")
    parser.add_argument("--platform", type=int, default=0, help="平台类型")
    parser.add_argument("--episode", help="期数（用于记录失败）")
    parser.add_argument("--record", action="store_true", help="记录到数据库（需 --episode --platform）")
    parser.add_argument("--list-pending", action="store_true", help="列出待修记录")
    parser.add_argument("--resolve", help="标记待修解决：platform_type:platform_name:step")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--test-samples", action="store_true", help="跑内置样本自检")
    args = parser.parse_args(argv)

    if args.test_samples:
        samples = _sample_errors()
        for name, err in samples.items():
            code = classify_failure(err, args.platform)
            print(f"{name}: {code} <- {err[:60]}")
        return 0

    if args.list_pending:
        pending = list_pending_fixes()
        if args.json:
            print(json.dumps(pending, ensure_ascii=False, indent=2))
        else:
            for p in pending:
                print(f"  {p['platform_name']}({p['platform_type']}) step={p['failure_step']} count={p['accumulated_count']}")
        return 0

    if args.resolve:
        try:
            pt, pn, step = args.resolve.split(":", 2)
            ok = mark_fix_resolved(int(pt), pn, step)
            print("✅ 已标记解决" if ok else "⚠️ 记录不存在")
        except Exception:
            print("❌ 格式错误：platform_type:platform_name:step")
        return 0

    if not args.error:
        parser.error("需提供 --error 或使用 --test-samples / --list-pending")

    code = classify_failure(args.error, args.platform)
    result = {"code": code, "should_retry": should_auto_retry(code)}

    if args.record and args.episode and args.platform:
        platform_name = preflight.PLATFORM_NAMES.get(args.platform, f"平台{args.platform}")
        code, is_new = record_failure(
            DEFAULT_DB_PATH, args.episode, args.platform, platform_name, args.error
        )
        result["recorded"] = is_new
        result["code"] = code
        # 尝试生成待修
        fix_path = maybe_create_pending_fix(args.episode, args.platform, platform_name, None, args.error)
        if fix_path:
            result["pending_fix"] = str(fix_path)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"分类码: {code}")
        print(f"自动补发: {'是' if should_auto_retry(code) else '否'}")
        if "recorded" in result:
            print(f"新记录: {'是' if result['recorded'] else '否（去重）'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())