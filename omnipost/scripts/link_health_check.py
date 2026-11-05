#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布后链路存活巡检 + 平台反馈采集（第 3 件 + 第 5 件预研）。

职责：
1. 读 publish_history 近 N 天已发布链接，逐个回访判定状态
2. 识别平台反馈：正常 / 已删除(404) / 审核中超时 / 疑似限流(可达但内容不可见) / 平台警告/违规标记
3. 回写 publish_history 新增字段；异常写入 platform_feedback 表
4. 同步更新飞书台账备注；异常单独推飞书告警（去重）
5. 产出违规模式摘要，供合规闸门/选题避坑复用

状态枚举：
- alive: 正常可访问，内容完整
- deleted: 404 或明确提示已删除
- under_review: 审核中超过阈值（默认 24h）
- throttled: 页面可达但内容不可见/阅读量异常为 0
- warned: 平台显式提示违规/警告/降权
- unknown: 网络/解析异常，需人工复核
"""

from __future__ import annotations

import asyncio
import hashlib
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

# preflight_notify 在 scripts 目录下，需确保 scripts 在 sys.path 中
SCRIPTS_DIR = OMNIPOST_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import preflight_notify  # noqa: E402

DB_PATH = Path(BASE_DIR) / "db" / "database.db"
LOGS_DIR = OMNIPOST_DIR.parent / "logs" / "link_health"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# 待巡检的最近天数
LOOKBACK_DAYS = 30
# 审核中超时阈值（小时）
REVIEW_TIMEOUT_HOURS = 24
# 并发数
CONCURRENCY = 3
# 单链接超时
PAGE_TIMEOUT_MS = 30000

# 平台特征选择器（用于判定内容可见性）
PLATFORM_CONTENT_SELECTORS = {
    5:  ".article-content, #articleContent, .baijiahao-content",  # 百家号
    7:  ".article-content, .pgc-article, #article-content",       # 今日头条
    8:  ".article-content, #content, .text",                       # 搜狐号
    9:  ".Post-RichText, .RichText, .css-1v3vz1k",                 # 知乎
    11: ".WB_detail, .WB_text, .detail_wrap",                      # 微博
    15: ".article-content, .content-detail",                       # 企鹅号
    13: ".article-content, .post_content",                         # 网易号
    14: ".article-content, .content",                              # 大鱼号
}

# 平台警告/违规关键词（页面文本中出现即判定为 warned）
PLATFORM_WARNING_PATTERNS = {
    5:  [r"违规", r"违反.*规范", r"被.*限流", r"低质", r"营销", r"广告嫌疑", r"标题党"],
    7:  [r"违规", r"违反.*社区", r"限流", r"低质量", r"营销推广", r"标题夸大"],
    8:  [r"违规", r"不符合.*规范", r"限制推荐", r"内容质量"],
    9:  [r"违规", r"违反.*社区", r"限流", r"折叠", r"敏感", r"广告"],
    11: [r"违规", r"违反.*规定", r"限流", r"降权", r"不予推荐", r"广告"],
    15: [r"违规", r"违反.*规范", r"限流", r"低质", r"营销"],
    13: [r"违规", r"违反.*规范", r"限流", r"质量不达标"],
    14: [r"违规", r"违反.*规范", r"限流", r"内容质量"],
}


# ============================================================ 数据库扩展

_FEEDBACK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS platform_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    history_id INTEGER NOT NULL,
    episode TEXT,
    platform_type INTEGER NOT NULL,
    platform_name TEXT NOT NULL,
    published_url TEXT NOT NULL,
    check_status TEXT NOT NULL,           -- alive/deleted/under_review/throttled/warned/unknown
    warning_text TEXT,                    -- 页面捕获的警告文本
    content_visible INTEGER,              -- 1=可见 0=不可见 -1=未检测
    http_status INTEGER,                  -- HTTP 状态码
    check_at TEXT NOT NULL,               -- 巡检时间
    raw_snapshot TEXT,                    -- 页面快照摘要(截断)
    FOREIGN KEY (history_id) REFERENCES publish_history(id)
);
CREATE INDEX IF NOT EXISTS idx_platform_feedback_history ON platform_feedback(history_id);
CREATE INDEX IF NOT EXISTS idx_platform_feedback_episode_platform ON platform_feedback(episode, platform_type);
CREATE INDEX IF NOT EXISTS idx_platform_feedback_status ON platform_feedback(check_status);
"""

# publish_history 新增列
_NEW_HEALTH_COLUMNS = (
    ("health_status", "TEXT"),           # alive/deleted/under_review/throttled/warned/unknown
    ("health_checked_at", "TEXT"),       # 最近一次巡检时间
    ("health_details", "TEXT"),          # 详情 JSON
)


def ensure_health_schema(db_path: Path) -> List[str]:
    """幂等补齐 publish_history 健康巡检列与 platform_feedback 表。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return []

    applied: List[str] = []
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()

        # publish_history 新增列
        existing = {row[1] for row in cur.execute("PRAGMA table_info(publish_history)")}
        for column, sql_type in _NEW_HEALTH_COLUMNS:
            if column not in existing:
                cur.execute(f"ALTER TABLE publish_history ADD COLUMN {column} {sql_type}")
                applied.append(f"ADD COLUMN {column}")

        # platform_feedback 表
        cur.executescript(_FEEDBACK_TABLE_SQL)
        conn.commit()
    return applied


# ============================================================ 核心巡检逻辑

async def check_one_link(
    browser,
    history_id: int,
    platform_type: int,
    platform_name: str,
    published_url: str,
    episode: str,
    published_at: Optional[str],
) -> Dict[str, Any]:
    """巡检单个链接，返回结构化结果。"""
    result = {
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
    }

    page = await browser.new_page()
    try:
        # 访问页面
        response = await page.goto(published_url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        result["http_status"] = response.status if response else 0

        if result["http_status"] in (404, 410):
            result["check_status"] = "deleted"
            return result

        # 等待内容加载
        await page.wait_for_timeout(2000)

        # 获取页面文本用于警告检测
        page_text = await page.evaluate("() => document.body.innerText")
        result["raw_snapshot"] = page_text[:2000]

        # 1) 检测平台警告/违规文案
        patterns = PLATFORM_WARNING_PATTERNS.get(platform_type, [])
        warning_hits = [p for p in patterns if re.search(p, page_text, re.IGNORECASE)]
        if warning_hits:
            result["check_status"] = "warned"
            result["warning_text"] = "; ".join(warning_hits[:5])
            return result

        # 2) 检测审核中状态
        review_keywords = ["审核中", "待审核", "审核进行", "正在审核", "under review", "pending review"]
        if any(kw in page_text for kw in review_keywords):
            # 如果发布时间超过阈值仍在审核，判定为超时
            if published_at:
                try:
                    pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    if datetime.now() - pub_dt > timedelta(hours=REVIEW_TIMEOUT_HOURS):
                        result["check_status"] = "under_review"
                        return result
                except Exception:
                    pass
            result["check_status"] = "under_review"
            return result

        # 3) 检测内容可见性
        selectors = PLATFORM_CONTENT_SELECTORS.get(platform_type, [])
        visible = False
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = await el.inner_text()
                    if text and len(text.strip()) > 50:
                        visible = True
                        break
            except Exception:
                pass
        result["content_visible"] = 1 if visible else 0

        if not visible:
            # 页面可达但内容不可见 -> 疑似限流
            result["check_status"] = "throttled"
            return result

        # 4) 正常
        result["check_status"] = "alive"
        return result

    except Exception as exc:
        result["check_status"] = "unknown"
        result["raw_snapshot"] = f"Exception: {exc}"[:2000]
        return result
    finally:
        await page.close()


async def run_health_check(
    db_path: Path,
    lookback_days: int = LOOKBACK_DAYS,
    concurrency: int = CONCURRENCY,
    send_alerts: bool = True,
) -> List[Dict[str, Any]]:
    """批量巡检近 N 天已发布链接。"""
    ensure_health_schema(db_path)

    # 读取待巡检记录
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
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

    # 并发巡检
    semaphore = asyncio.Semaphore(concurrency)
    results: List[Dict[str, Any]] = []

    async def worker(row):
        async with semaphore:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    r = await check_one_link(
                        browser,
                        row["id"],
                        row["platform_type"],
                        row["platform_name"],
                        row["published_url"],
                        row["episode"] or "",
                        row["published_at"],
                    )
                    results.append(r)
                finally:
                    await browser.close()

    await asyncio.gather(*[worker(r) for r in rows])

    # 回写数据库
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        for r in results:
            # 更新 publish_history
            cur.execute(
                """
                UPDATE publish_history
                SET health_status = ?, health_checked_at = ?, health_details = ?
                WHERE id = ?
                """,
                (r["check_status"], r["check_at"], json.dumps(r, ensure_ascii=False), r["history_id"]),
            )
            # 写入 platform_feedback
            cur.execute(
                """
                INSERT INTO platform_feedback
                (history_id, episode, platform_type, platform_name, published_url,
                 check_status, warning_text, content_visible, http_status, check_at, raw_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["history_id"], r["episode"], r["platform_type"], r["platform_name"],
                    r["published_url"], r["check_status"], r["warning_text"],
                    r["content_visible"], r["http_status"], r["check_at"], r["raw_snapshot"],
                ),
            )
        conn.commit()

    # 统计
    stats = {}
    for r in results:
        stats[r["check_status"]] = stats.get(r["check_status"], 0) + 1
    print(f"📊 巡检完成：{stats}")

    # 写入巡检日志
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"health_check_{datetime.now():%Y%m%d_%H%M%S}.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📝 巡检日志: {log_path}")

    # 发送飞书告警（可选，run_daily_preflight 中统一发送）
    if send_alerts:
        await send_health_alerts(results)

    return results


# ============================================================ 违规模式摘要（供合规/选题复用）

def extract_violation_patterns(db_path: Path, days: int = 30) -> Dict[str, Any]:
    """从 platform_feedback 提取高频违规模式，生成避坑指南。"""
    ensure_health_schema(db_path)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT platform_type, platform_name, warning_text, check_status, published_url, episode
            FROM platform_feedback
            WHERE check_status IN ('warned', 'deleted', 'throttled')
              AND date(check_at) >= ?
            """,
            (cutoff,),
        ).fetchall()

    patterns = {}
    for row in rows:
        key = f"{row['platform_name']}|{row['check_status']}"
        if key not in patterns:
            patterns[key] = {
                "platform": row["platform_name"],
                "status": row["check_status"],
                "count": 0,
                "warning_keywords": {},
                "episodes": set(),
            }
        p = patterns[key]
        p["count"] += 1
        p["episodes"].add(row["episode"])
        if row["warning_text"]:
            for kw in row["warning_text"].split("; "):
                p["warning_keywords"][kw] = p["warning_keywords"].get(kw, 0) + 1

    # 转换为可序列化格式
    out = {}
    for k, v in patterns.items():
        out[k] = {
            "platform": v["platform"],
            "status": v["status"],
            "count": v["count"],
            "top_keywords": sorted(v["warning_keywords"].items(), key=lambda x: -x[1])[:10],
            "affected_episodes": sorted(v["episodes"]),
        }
    return out


def suggest_compliance_updates(patterns: Dict[str, Any]) -> List[str]:
    """基于违规模式建议合规词库/规则更新。"""
    suggestions = []
    for key, data in patterns.items():
        if data["status"] == "warned" and data["top_keywords"]:
            top_kw = data["top_keywords"][0][0]
            suggestions.append(
                f"[{data['platform']}] 高频警告关键词: {top_kw} (出现 {data['top_keywords'][0][1]} 次) "
                f"-> 建议加入 BANNED_WORDS 或该平台专用黑名单"
            )
        if data["status"] == "throttled" and data["count"] >= 2:
            suggestions.append(
                f"[{data['platform']}] 疑似限流 {data['count']} 次 -> 建议检查标题/封面/标签是否触发平台质量模型"
            )
        if data["status"] == "deleted" and data["count"] >= 2:
            suggestions.append(
                f"[{data['platform']}] 被删除 {data['count']} 次 -> 建议人工复盘对应期数内容与平台规则"
            )
    return suggestions


# ============================================================ 飞书告警（去重）

async def send_health_alerts(results: List[Dict[str, Any]]) -> None:
    """异常结果推送飞书（去重：同期同平台同状态只提醒一次）。"""
    webhook, secret = preflight_notify.resolve_lark_settings(None, preflight_notify.CONFIG_PATH)
    if not webhook:
        print("⚠️ 未配置飞书 Webhook，跳过告警")
        return

    webhook = preflight_notify.validate_webhook(webhook)
    STATE_DIR = OMNIPOST_DIR.parent / "logs" / "link_health" / ".notify"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    for r in results:
        if r["check_status"] in ("alive",):
            continue
        key = f"health:{r['episode']}:{r['platform_type']}:{r['check_status']}"
        text = (
            f"🔗 链路巡检异常\n"
            f"- 期数: {r['episode']}\n"
            f"- 平台: {r['platform_name']}\n"
            f"- 状态: {r['check_status']}\n"
            f"- 链接: {r['published_url']}\n"
        )
        if r["warning_text"]:
            text += f"- 警告: {r['warning_text']}\n"
        status, msg = preflight_notify.notify_once(STATE_DIR, key, webhook, secret, text)
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
    parser.add_argument("--patterns", action="store_true", help="仅输出违规模式摘要")
    parser.add_argument("--suggest", action="store_true", help="输出合规规则更新建议")
    parser.add_argument("--no-alert", action="store_true", help="不发送飞书告警")
    args = parser.parse_args(argv)

    if args.patterns or args.suggest:
        patterns = extract_violation_patterns(DB_PATH, args.days)
        if args.suggest:
            suggestions = suggest_compliance_updates(patterns)
            for s in suggestions:
                print(s)
        else:
            print(json.dumps(patterns, ensure_ascii=False, indent=2))
        return 0

    results = asyncio.run(run_health_check(DB_PATH, args.days, args.concurrency))

    if not args.no_alert:
        asyncio.run(send_health_alerts(results))

    # 写入巡检日志
    log_path = LOGS_DIR / f"health_check_{datetime.now():%Y%m%d_%H%M%S}.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📝 巡检日志: {log_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())