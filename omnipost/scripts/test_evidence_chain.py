#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""证据链固定回归样本：不访问网络、不改生产库。

覆盖：
1. 微博热搜/登录墙整页出现「违规/降权/广告」裸词 -> unknown，不进聚合
2. 知乎 40362 反爬 JSON -> unknown，不进聚合
3. 正常文章正文渲染 -> alive / verified
4. 通知容器内明确判词 -> warned / verified，证据为判词而非裸模板
5. 同链接同日重复巡检 -> platform_feedback 只保留一条（临时库验证）
6. 旧脏词库不采信；损坏词库 fail-closed；静态违禁词仍拦截
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
OMNIPOST_DIR = SCRIPTS_DIR.parent
PROJECT_DIR = OMNIPOST_DIR.parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

import link_health_check  # noqa: E402
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


def test_classify() -> None:
    print("\n[1] classify_page_evidence 固定样本")

    weibo_page = """登录
注册
无障碍
推荐
热门推荐
微博热搜
我的
1 王子璇说不想跟文吉儿做朋友了
微博客服 4000-960-960
页面内容包含违规降权广告...
随时随地发现新鲜事
登录/注册
查看完整热搜榜单"""
    r = link_health_check.classify_page_evidence(
        http_status=200, page_text=weibo_page, notice_text="", has_content=False,
    )
    check("微博热搜/登录墙不判 warned", r["check_status"] == "unknown", str(r))
    check("微博热搜证据不可采信", r["evidence_quality"] == "unverified", str(r))
    check("微博热搜标注成因", "登录墙/反爬" in (r["evidence_reason"] or ""), str(r))

    zhihu_page = json.dumps(
        {"error": {"message": "您当前请求存在异常，暂时限制本次访问。如有疑问，您可以通过手机摇一摇或登录后私信知乎小管家反馈。", "code": 40362}},
        ensure_ascii=False,
    )
    r = link_health_check.classify_page_evidence(
        http_status=200, page_text=zhihu_page, notice_text="", has_content=False,
    )
    check("知乎 40362 不判 throttled", r["check_status"] == "unknown", str(r))
    check("知乎 40362 归 unverified", r["evidence_quality"] == "unverified", str(r))

    r = link_health_check.classify_page_evidence(
        http_status=200, page_text="正文片段", notice_text="", has_content=True,
    )
    check("正文渲染判 alive", r["check_status"] == "alive", str(r))
    check("正文渲染证据 verified", r["evidence_quality"] == "verified", str(r))

    r = link_health_check.classify_page_evidence(
        http_status=200,
        page_text="文章正文与导航",
        notice_text="该文章因涉及营销推广被限制推荐",
        has_content=False,
    )
    check("通知容器明确判词判 warned", r["check_status"] == "warned", str(r))
    check("通知容器证据 verified", r["evidence_quality"] == "verified", str(r))
    check("证据记录具体判词", "营销推广" in (r["evidence_text"] or ""), str(r))

    r = link_health_check.classify_page_evidence(
        http_status=404, page_text="", notice_text="", has_content=False,
    )
    check("404 判 deleted", r["check_status"] == "deleted", str(r))
    check("404 证据 verified", r["evidence_quality"] == "verified", str(r))

    # 整页有判词但不在通知容器：只报线索，不做结论
    r = link_health_check.classify_page_evidence(
        http_status=200, page_text="该文章因涉及营销推广被限制推荐", notice_text="", has_content=False,
    )
    check("整页判词不作为结论", r["check_status"] == "unknown", str(r))


def test_dedupe_and_aggregation(tmp_db: Path) -> dict:
    print("\n[2] 同链接同日去重 + 只采信 verified")
    now = datetime.now().isoformat(timespec="seconds")
    with sqlite3.connect(str(tmp_db)) as conn:
        # 临时库只需最小表结构；ensure_health_schema 会补健康列与反馈表
        conn.execute(
            "CREATE TABLE publish_history ("
            "id INTEGER PRIMARY KEY, episode TEXT, platform_type INTEGER, "
            "platform_name TEXT, published_url TEXT, status TEXT, created_at TEXT)"
        )
        conn.commit()
    link_health_check.ensure_health_schema(tmp_db)
    with sqlite3.connect(str(tmp_db)) as conn:
        conn.execute(
            "INSERT INTO publish_history (id, episode, platform_type, platform_name, published_url, status, created_at) "
            "VALUES (1, '15', 11, '微博', 'https://example.com/a', 'published', ?)",
            (now[:10],),
        )
        # 同一条链接、同一天，人为塞两条旧式重复记录
        for i in range(2):
            conn.execute(
                "INSERT INTO platform_feedback (history_id, episode, platform_type, platform_name, published_url, "
                "check_status, warning_text, check_at, raw_snapshot, evidence_quality, evidence_text) "
                "VALUES (1, '15', 11, '微博', 'https://example.com/a', 'warned', '违规', ?, '旧脏快照', 'verified', '违规')",
                ((datetime.now() - timedelta(minutes=i)).isoformat(timespec="seconds"),),
            )
        # unverified 反爬记录，不应进入聚合
        conn.execute(
            "INSERT INTO platform_feedback (history_id, episode, platform_type, platform_name, published_url, "
            "check_status, check_at, raw_snapshot, evidence_quality, evidence_reason) "
            "VALUES (2, '15', 9, '知乎', 'https://example.com/b', 'throttled', ?, '40362', 'unverified', '反爬')",
            (now,),
        )
        conn.commit()

    records = violation_feedback.load_feedback(tmp_db, days=30)
    check("同链接同日只保留一条", len(records) == 2, f"实际 {len(records)}: {records}")
    signals = violation_feedback.extract_violation_signals(records)
    check("unverified 被忽略", signals["unverified_count"] == 1, str(signals))
    check("verified 信号桶仅 1 个", len(signals["buckets"]) == 1, str(signals))
    words = violation_feedback.build_dynamic_banned_words(signals)
    check("high_risk 等于静态基线", words["high_risk"] == sorted(violation_feedback.STATIC_BANNED_WORDS), str(words["high_risk"]))
    check("裸词「违规」不进入 observed_terms", all(x["term"] != "违规" for x in words["observed_terms"]), str(words["observed_terms"]))
    check("状态标注为观察提案", words["status"] == "observation_proposal", str(words))
    return words


def test_compliance_gate(tmp_rule: Path) -> None:
    print("\n[3] 合规闸门 fail-closed 与旧词库隔离")
    import compliance_check

    # reload 会重新绑定模块路径，因此每个场景都在 reload 后立刻覆盖到临时词库
    original = compliance_check._DYNAMIC_BANNED_PATH
    try:
        title = "工程现场图纸版本统一与批注归档实操"
        body = "工程现场图纸版本混乱，需要统一入口与批注归档，避免交接时反复找图。" * 28

        tmp_rule.write_text('{"high_risk": ["违规", "低质量"], "suspicious": [], "platform_specific": {}}', encoding="utf-8")
        m = importlib.reload(compliance_check)
        m._DYNAMIC_BANNED_PATH = tmp_rule
        m._load_dynamic_banned()
        r = m.compliance_check(title, body, 5, has_cover=True, inline_count=2)
        check("旧格式词库不采信裸词", r["passed"], str(r["problems"]))

        tmp_rule.write_text("{not json", encoding="utf-8")
        m = importlib.reload(compliance_check)
        m._DYNAMIC_BANNED_PATH = tmp_rule
        m._load_dynamic_banned()
        r = m.compliance_check(title, body, 5, has_cover=True, inline_count=2)
        check("词库损坏时 fail-closed", not r["passed"], str(r))
        check("fail-closed 原因明确", any("词库" in p for p in r["problems"]), str(r["problems"]))

        tmp_rule.write_text(json.dumps({
            "schema_version": "evidence-v2", "status": "observation_proposal",
            "high_risk": sorted(violation_feedback.STATIC_BANNED_WORDS),
            "suspicious": [], "platform_specific": {}, "observed_terms": [],
        }), encoding="utf-8")
        m = importlib.reload(compliance_check)
        m._DYNAMIC_BANNED_PATH = tmp_rule
        m._load_dynamic_banned()
        r = m.compliance_check(title, body, 5, has_cover=True, inline_count=2)
        check("正常词库下正常样本通过", r["passed"], str(r["problems"]))
        r = m.compliance_check(title, "免费试跑" + body, 5, has_cover=True, inline_count=2)
        check("静态零容忍词仍拦截", not r["passed"], str(r))
        check("拦截原因命中具体词", any("免费试跑" in p for p in r["problems"]), str(r["problems"]))
    finally:
        compliance_check._DYNAMIC_BANNED_PATH = original
        importlib.reload(compliance_check)


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="evidence-chain-") as td:
        tmp = Path(td)
        test_classify()
        test_dedupe_and_aggregation(tmp / "test.db")
        test_compliance_gate(tmp / "dynamic_banned_words.json")

    print(f"\n结果：PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
