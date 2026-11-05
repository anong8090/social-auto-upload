#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布后链路存活巡检 + 平台反馈采集（第 3 件）。

职责：
1. 读 publish_history 近 N 天已发布链接，逐个回访判定状态
2. 只对可复核的证据给出确定性结论；证据不足一律 unknown / 待人工复核
3. 同一链接同一天只保留一条反馈记录（原地更新），避免重复计数
4. 异常写入 platform_feedback；publish_history 回写健康字段
5. 飞书告警默认关闭，由 run_daily_preflight.py 统一汇总发送

状态枚举：
- alive / deleted / under_review / throttled / warned / unknown

证据等级（evidence_quality）：
- verified   : HTTP 明确状态，或平台通知容器内的明确判词；可进入违规聚合
- unverified : 登录墙、反爬、选择器漂移、解析异常；只告警，不进入规则聚合
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import async_playwright

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OMNIPOST_DIR))

from conf import BASE_DIR  # noqa: E402
from myUtils import preflight  # noqa: E402

SCRIPTS_DIR = OMNIPOST_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import preflight_notify  # noqa: E402

DB_PATH = Path(BASE_DIR) / "db" / "database.db"
LOGS_DIR = OMNIPOST_DIR.parent / "logs" / "link_health"

LOOKBACK_DAYS = 30
REVIEW_TIMEOUT_HOURS = 24
CONCURRENCY = 3
PAGE_TIMEOUT_MS = 30000
EVIDENCE_TEXT_LIMIT = 200
SNAPSHOT_LIMIT = 300

# 正文容器：只用于判断「这篇文章是否真的渲染出来了」
PLATFORM_CONTENT_SELECTORS = {
    5:  ".article-content, #articleContent, .baijiahao-content",
    7:  ".article-content, .pgc-article, #article-content",
    8:  ".article-content, #content, .text",
    9:  ".Post-RichText, .RichText, .css-1v3vz1k",
    11: ".WB_detail, .WB_text, .detail_wrap",
    15: ".article-content, .content-detail",
    13: ".article-content, .post_content",
    14: ".article-content, .content",
}

# 平台通知容器：判词必须出现在这里才算 verified，不再扫整页 body.innerText
NOTICE_SELECTORS = (
    '[class*="notice"]', '[class*="Notice"]',
    '[class*="warning"]', '[class*="Warning"]',
    '[class*="audit"]', '[class*="Audit"]',
    '[class*="violation"]', '[class*="Violation"]',
    '[class*="status-tip"]', '[class*="status-tips"]',
    '[role="alert"]', '[role="status"]',
    ".article-status", ".content-status", ".audit-result",
)

# 明确判词：必须是「主语 + 原因/处置」的完整表述，禁止用裸词「违规 / 广告 / 限流」匹配
VERDICT_PATTERNS: Tuple[str, ...] = (
    r"(该|此|本篇|本条)?(内容|文章|作品|稿件|动态|帖子).{0,16}(因|由于|存在|涉及|被判定为|被识别为).{0,24}(违规|违反|营销推广|广告推广|低质|标题党|夸大)",
    r"(内容|文章|作品|稿件|动态).{0,12}(已|被).{0,8}(删除|下架|折叠|限制推荐|不予推荐|仅自己可见|设为私密)",
    r"违反.{0,12}(社区|平台|创作).{0,12}(规范|规则|公约|条例).{0,16}(已|，|,).{0,12}(下架|删除|折叠|限制|处理|驳回)",
    r"(审核|审核结果).{0,8}(未通过|不通过|驳回|拒绝)",
)

DELETE_PATTERNS: Tuple[str, ...] = (
    r"(该|此)?(内容|文章|作品|页面|稿件).{0,10}(不存在|已删除|已下架|已失效|无法查看)",
    r"(页面走丢了|内容不见了|内容已被删除)",
)

THROTTLE_PATTERNS: Tuple[str, ...] = (
    r"(限制推荐|不予推荐|减少推荐|仅自己可见|仅粉丝可见|已折叠|降权处理)",
)

REVIEW_PATTERNS: Tuple[str, ...] = (
    r"(内容|文章|作品|稿件).{0,8}(审核中|待审核|正在审核)",
    r"^(审核中|待审核|内容审核中)",
)

# 登录墙 / 反爬 / 反机器人特征：命中且正文未渲染 -> unknown，不得判为限流或违规
UNVERIFIABLE_MARKERS: Tuple[str, ...] = (
    "登录/注册", "登录后查看", "请登录", "扫码登录", "暂无查看权限",
    "您当前请求存在异常", "暂时限制本次访问", "手机摇一摇",
    "40362", "请求存在异常", "访问频繁", "请稍后再试",
    "人机验证", "完成安全验证", "captcha", "verify your identity",
    "随时随地发现新鲜事", "查看完整热搜榜单", "微博热搜",
)


# ============================================================ 数据库扩展

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
_NEW_FEEDBACK_COLUMNS = (
    ("evidence_quality", "TEXT DEFAULT 'unverified'"),  # verified / unverified
    ("evidence_source", "TEXT"),                        # http_status / notice / page_marker / content_selector
    ("evidence_text", "TEXT"),                          # 命中的具体判词（截断）
    ("evidence_reason", "TEXT"),                        # unknown 的成因
)


def ensure_health_schema(db_path: Path) -> List[str]:
    """幂等补齐 publish_history 健康列与 platform_feedback 证据列。"""
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
        for column, sql_type in _NEW_FEEDBACK_COLUMNS:
            if column not in existing_feedback:
                cur.execute(f"ALTER TABLE platform_feedback ADD COLUMN {column} {sql_type}")
                applied.append(f"ADD COLUMN platform_feedback.{column}")

        conn.commit()
    return applied


# ============================================================ 纯判定逻辑（可脱离浏览器测试）

def _first_match(patterns: Tuple[str, ...], text: str) -> Optional[str]:
    for pattern in patterns:
        hit = re.search(pattern, text, re.IGNORECASE)
        if hit:
            return re.sub(r"\s+", " ", hit.group(0)).strip()[:EVIDENCE_TEXT_LIMIT]
    return None


def _hit_marker(text: str) -> Optional[str]:
    lowered = text.lower()
    for marker in UNVERIFIABLE_MARKERS:
        if marker.lower() in lowered:
            return marker
    return None


def _parse_published_at(published_at: Optional[str]) -> Optional[datetime]:
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def classify_page_evidence(
    *,
    http_status: Optional[int],
    page_text: str,
    notice_text: str,
    has_content: bool,
    published_at: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """根据页面证据判状态；证据不足一律 unknown / unverified。

    判定顺序刻意把「正文已渲染」放在登录墙之前：真实文章页的评论区也常出现
    「请登录」，不能因此把正常文章判成 unknown。
    """
    now = now or datetime.now()
    page_text = page_text or ""
    notice_text = notice_text or ""
    base: Dict[str, Any] = {
        "check_status": "unknown",
        "evidence_quality": "unverified",
        "evidence_source": None,
        "evidence_text": None,
        "evidence_reason": None,
        "content_visible": 1 if has_content else 0,
        "warning_text": None,
    }

    def finish(status: str, quality: str, source: str, text: Optional[str] = None,
               reason: Optional[str] = None) -> Dict[str, Any]:
        base.update(
            check_status=status,
            evidence_quality=quality,
            evidence_source=source,
            evidence_text=text,
            evidence_reason=reason,
            warning_text=text if status in ("warned", "deleted", "throttled") else None,
        )
        return base

    # 1) HTTP 层确定性证据
    if http_status in (404, 410):
        return finish("deleted", "verified", "http_status", f"HTTP {http_status}")

    # 2) 通知容器内的明确判词（唯一可 verified 的 warned 来源）
    verdict = _first_match(VERDICT_PATTERNS, notice_text)
    if verdict:
        return finish("warned", "verified", "notice", verdict)
    deleted = _first_match(DELETE_PATTERNS, notice_text)
    if deleted:
        return finish("deleted", "verified", "notice", deleted)
    throttled = _first_match(THROTTLE_PATTERNS, notice_text)
    if throttled:
        return finish("throttled", "verified", "notice", throttled)

    # 3) 审核中：只有能确认超过阈值才算 verified
    review = _first_match(REVIEW_PATTERNS, notice_text) or _first_match(REVIEW_PATTERNS, page_text)
    if review:
        pub_dt = _parse_published_at(published_at)
        if pub_dt is None:
            return finish("unknown", "unverified", "notice",
                          reason=f"检测到审核状态，但发布时间不可用，无法确认是否超时：{review}")
        if now - pub_dt > timedelta(hours=REVIEW_TIMEOUT_HOURS):
            return finish("under_review", "verified", "notice", review)
        return finish("under_review", "verified", "notice", review,
                      reason="审核中但未超过超时阈值，仅记录")

    # 4) 正文正常渲染 -> alive
    if has_content:
        return finish("alive", "verified", "content_selector")

    # 5) 登录墙 / 反爬：明确区分，不再误判成限流或违规
    marker = _hit_marker(page_text)
    if marker:
        return finish("unknown", "unverified", "page_marker",
                      reason=f"命中登录墙/反爬特征「{marker}」，需人工复核")

    # 6) 整页出现判词但不在通知容器内：只报线索，不做结论
    page_only = _first_match(VERDICT_PATTERNS, page_text)
    if page_only:
        return finish("unknown", "unverified", "page_text",
                      reason=f"整页出现疑似判词但不在通知容器内，不采信：{page_only}")

    if http_status and http_status >= 400:
        return finish("unknown", "unverified", "http_status",
                      reason=f"HTTP {http_status}，无法确认内容状态")

    return finish("unknown", "unverified", "content_selector",
                  reason="正文容器未命中（可能是页面改版、登录态差异或内容未加载），需人工复核")


# ============================================================ 单链接巡检

async def check_one_link(
    browser,
    history_id: int,
    platform_type: int,
    platform_name: str,
    published_url: str,
    episode: str,
    published_at: Optional[str],
) -> Dict[str, Any]:
    """巡检单个链接，返回结构化结果（含证据等级）。"""
    result: Dict[str, Any] = {
        "history_id": history_id,
        "episode": episode,
        "platform_type": platform_type,
        "platform_name": platform_name,
        "published_url": published_url,
        "check_status": "unknown",
        "warning_text": None,
        "content_visible": -1,
        "http_status": None,
        "check_at": datetime.now().isoformat(timespec="seconds"),
        "raw_snapshot": "",
        "evidence_quality": "unverified",
        "evidence_source": None,
        "evidence_text": None,
        "evidence_reason": None,
    }

    page = await browser.new_page()
    try:
        response = await page.goto(published_url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        result["http_status"] = response.status if response else 0

        if result["http_status"] in (404, 410):
            verdict = classify_page_evidence(
                http_status=result["http_status"], page_text="", notice_text="",
                has_content=False, published_at=published_at,
            )
            result.update(verdict)
            result["raw_snapshot"] = f"HTTP {result['http_status']}"
            return result

        await page.wait_for_timeout(2000)

        try:
            page_text = await page.evaluate("() => document.body.innerText") or ""
        except Exception:  # noqa: BLE001 - 页面上下文丢失也要留证据
            page_text = ""

        # 通知容器文本：只在小范围内匹配判词
        notice_parts: List[str] = []
        for selector in NOTICE_SELECTORS:
            try:
                elements = await page.query_selector_all(selector)
                for el in elements[:5]:
                    text = await el.inner_text()
                    if text and len(text.strip()) <= 500:
                        notice_parts.append(text.strip())
            except Exception:  # noqa: BLE001
                continue
        notice_text = "\n".join(notice_parts)[:2000]

        # 正文可见性
        has_content = False
        for sel in PLATFORM_CONTENT_SELECTORS.get(platform_type, "").split(","):
            sel = sel.strip()
            if not sel:
                continue
            try:
                el = await page.query_selector(sel)
                if el:
                    text = await el.inner_text()
                    if text and len(text.strip()) > 50:
                        has_content = True
                        break
            except Exception:  # noqa: BLE001
                pass

        verdict = classify_page_evidence(
            http_status=result["http_status"],
            page_text=page_text,
            notice_text=notice_text,
            has_content=has_content,
            published_at=published_at,
        )
        result.update(verdict)

        # 快照只保留可复核证据，避免把热搜榜/客服电话写进避坑指南
        if result["evidence_quality"] == "verified":
            excerpt = result.get("evidence_text") or page_text[:SNAPSHOT_LIMIT]
            result["raw_snapshot"] = re.sub(r"\s+", " ", excerpt)[:SNAPSHOT_LIMIT]
        else:
            result["raw_snapshot"] = f"evidence_reason={result.get('evidence_reason')}"[:SNAPSHOT_LIMIT]
        return result

    except Exception as exc:  # noqa: BLE001 - 异常本身也是证据，但不能当结论
        result.update(
            check_status="unknown",
            evidence_quality="unverified",
            evidence_source="exception",
            evidence_reason=f"巡检异常，需人工复核: {type(exc).__name__}",
            raw_snapshot=f"Exception: {type(exc).__name__}"[:SNAPSHOT_LIMIT],
        )
        return result
    finally:
        await page.close()


# ============================================================ 批量巡检

async def run_health_check(
    db_path: Path,
    lookback_days: int = LOOKBACK_DAYS,
    concurrency: int = CONCURRENCY,
    send_alerts: bool = False,
) -> List[Dict[str, Any]]:
    """批量巡检近 N 天已发布链接；同链接同日只保留一条反馈记录。"""
    ensure_health_schema(db_path)

    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, episode, platform_type, platform_name, published_url, published_at
            FROM publish_history
            WHERE status = 'published'
              AND published_url IS NOT NULL
              AND published_url != ''
              AND date(created_at) >= ?
            ORDER BY id DESC
            """,
            (cutoff,),
        ).fetchall()

    if not rows:
        print("✅ 无待巡检记录")
        return []

    print(f"🔍 开始巡检 {len(rows)} 条已发布链接（最近 {lookback_days} 天）")

    semaphore = asyncio.Semaphore(concurrency)
    results: List[Dict[str, Any]] = []

    async def worker(row):
        async with semaphore:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    results.append(await check_one_link(
                        browser,
                        row["id"], row["platform_type"], row["platform_name"],
                        row["published_url"], row["episode"] or "", row["published_at"],
                    ))
                finally:
                    await browser.close()

    await asyncio.gather(*[worker(r) for r in rows])

    # 回写：同一 (history_id, 当日) 只 UPDATE 不重复 INSERT
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        for r in results:
            cur.execute(
                """
                UPDATE publish_history
                SET health_status = ?, health_checked_at = ?, health_details = ?
                WHERE id = ?
                """,
                (r["check_status"], r["check_at"], json.dumps(r, ensure_ascii=False), r["history_id"]),
            )
            day = str(r["check_at"])[:10]
            existing = cur.execute(
                """
                SELECT id FROM platform_feedback
                WHERE history_id = ? AND substr(check_at, 1, 10) = ?
                ORDER BY id DESC LIMIT 1
                """,
                (r["history_id"], day),
            ).fetchone()

            values = (
                r["episode"], r["platform_type"], r["platform_name"], r["published_url"],
                r["check_status"], r["warning_text"], r["content_visible"], r["http_status"],
                r["check_at"], r["raw_snapshot"], r["evidence_quality"],
                r["evidence_source"], r["evidence_text"], r["evidence_reason"],
            )
            if existing:
                cur.execute(
                    """
                    UPDATE platform_feedback SET
                        episode=?, platform_type=?, platform_name=?, published_url=?,
                        check_status=?, warning_text=?, content_visible=?, http_status=?,
                        check_at=?, raw_snapshot=?, evidence_quality=?, evidence_source=?,
                        evidence_text=?, evidence_reason=?
                    WHERE id=?
                    """,
                    (*values, existing[0]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO platform_feedback
                    (episode, platform_type, platform_name, published_url,
                     check_status, warning_text, content_visible, http_status,
                     check_at, raw_snapshot, evidence_quality, evidence_source,
                     evidence_text, evidence_reason, history_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values, r["history_id"]),
                )
        conn.commit()

    stats: Dict[str, int] = {}
    for r in results:
        stats[r["check_status"]] = stats.get(r["check_status"], 0) + 1
    print(f"📊 巡检完成：{stats}")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"health_check_{datetime.now():%Y%m%d_%H%M%S}.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📝 巡检日志: {log_path}")

    if send_alerts:
        await send_health_alerts(results)
    return results


# ============================================================ 违规模式摘要（只读、去重、只采信 verified）

def _feedback_supports_evidence(db_path: Path) -> bool:
    with sqlite3.connect(str(db_path)) as conn:
        return "evidence_quality" in {
            row[1] for row in conn.execute("PRAGMA table_info(platform_feedback)")
        }


def extract_violation_patterns(db_path: Path, days: int = 30) -> Dict[str, Any]:
    """从 platform_feedback 提取高频违规模式；旧表无证据列时返回空，不猜。"""
    ensure_health_schema(db_path)
    if not _feedback_supports_evidence(db_path):
        return {}
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT platform_type, platform_name, check_status, evidence_text,
                   published_url, episode, check_at, history_id
            FROM platform_feedback
            WHERE evidence_quality = 'verified'
              AND check_status IN ('warned', 'deleted', 'throttled', 'under_review')
              AND date(check_at) >= ?
            ORDER BY check_at DESC
            """,
            (cutoff,),
        ).fetchall()

    seen = set()
    patterns: Dict[str, Any] = {}
    for row in rows:
        key_day = (row["history_id"], str(row["check_at"])[:10])
        if key_day in seen:
            continue
        seen.add(key_day)
        key = f"{row['platform_name']}|{row['check_status']}"
        p = patterns.setdefault(key, {
            "platform": row["platform_name"],
            "status": row["check_status"],
            "count": 0,
            "warning_keywords": {},
            "episodes": set(),
        })
        p["count"] += 1
        p["episodes"].add(row["episode"])
        ev = (row["evidence_text"] or "").strip()
        if ev:
            p["warning_keywords"][ev] = p["warning_keywords"].get(ev, 0) + 1

    return {
        k: {
            "platform": v["platform"],
            "status": v["status"],
            "count": v["count"],
            "top_keywords": sorted(v["warning_keywords"].items(), key=lambda x: -x[1])[:10],
            "affected_episodes": sorted(str(e) for e in v["episodes"]),
        }
        for k, v in patterns.items()
    }


def suggest_compliance_updates(patterns: Dict[str, Any]) -> List[str]:
    """基于 verified 证据给出建议；平台判词不自动转成正文违禁词。"""
    suggestions: List[str] = []
    for _key, data in patterns.items():
        if data["status"] == "warned" and data["top_keywords"]:
            top, freq = data["top_keywords"][0]
            suggestions.append(
                f"[{data['platform']}] verified 警告证据（{freq} 个不同期数）: {top} "
                f"-> 建议人工复盘对应正文，确认真正的触发词后再提规则变更"
            )
        if data["status"] == "throttled" and data["count"] >= 2:
            suggestions.append(
                f"[{data['platform']}] 明确限流提示 {data['count']} 次 -> 检查标题/封面/标签是否触发质量模型"
            )
        if data["status"] == "deleted" and data["count"] >= 2:
            suggestions.append(
                f"[{data['platform']}] 已删除 {data['count']} 次 -> 人工复盘对应期数内容与平台规则"
            )
    if not suggestions:
        suggestions.append("暂无 verified 证据，不产出规则变更建议；请先人工复核 unverified 记录。")
    return suggestions


# ============================================================ 飞书告警（去重，unknown 不逐条打扰）

async def send_health_alerts(results: List[Dict[str, Any]]) -> None:
    """仅对 verified 异常推送飞书；unknown 交由每日汇总里的「待人工复核」呈现。"""
    webhook, secret = preflight_notify.resolve_lark_settings(None, preflight_notify.CONFIG_PATH)
    if not webhook:
        print("⚠️ 未配置飞书 Webhook，跳过告警")
        return

    webhook = preflight_notify.validate_webhook(webhook)
    state_dir = LOGS_DIR / ".notify"
    state_dir.mkdir(parents=True, exist_ok=True)

    actionable = {"deleted", "warned", "throttled", "under_review"}
    for r in results:
        if r["check_status"] not in actionable or r.get("evidence_quality") != "verified":
            continue
        key = f"health:{r['episode']}:{r['platform_type']}:{r['check_status']}"
        text = (
            f"🔗 链路巡检异常（已验证证据）\n"
            f"- 期数: {r['episode']}\n"
            f"- 平台: {r['platform_name']}\n"
            f"- 状态: {r['check_status']}\n"
            f"- 证据: {r.get('evidence_text') or 'HTTP 状态'}\n"
            f"- 链接: {r['published_url']}\n"
        )
        status, msg = preflight_notify.notify_once(state_dir, key, webhook, secret, text)
        if status == "sent":
            print(f"  📣 已推送飞书告警: {r['platform_name']} {r['check_status']}")
        elif status == "unknown":
            print(f"  ⚠️ 飞书告警结果未知: {msg}")


# ============================================================ CLI 入口

def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="发布后链路存活巡检 + 平台反馈采集")
    parser.add_argument("--days", type=int, default=LOOKBACK_DAYS, help="回看天数")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY, help="并发数")
    parser.add_argument("--patterns", action="store_true", help="仅输出 verified 违规模式摘要")
    parser.add_argument("--suggest", action="store_true", help="输出合规规则更新建议")
    parser.add_argument("--no-alert", action="store_true", help="不发送飞书告警")
    args = parser.parse_args(argv)

    if args.patterns or args.suggest:
        patterns = extract_violation_patterns(DB_PATH, args.days)
        if args.suggest:
            for s in suggest_compliance_updates(patterns):
                print(s)
        else:
            print(json.dumps(patterns, ensure_ascii=False, indent=2))
        return 0

    # 告警只在这里统一决定一次，避免函数内 + CLI 双重发送
    results = asyncio.run(run_health_check(
        DB_PATH, args.days, args.concurrency, send_alerts=not args.no_alert,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
