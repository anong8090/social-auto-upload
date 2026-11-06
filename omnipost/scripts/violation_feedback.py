#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""违规反馈聚合：platform_feedback → 观察词库提案 / 避坑指南。

证据链约束（2026-11-05 收口，对齐跨项目 Autoloop 路线）：
1. 只采信 evidence_quality='verified' 的记录；unverified 只做「待人工复核」计数；
2. 同一 history_id 同一天只算一次，同一判词按「不同期数」计数，避免重复巡检刷频次；
3. 平台通知判词整句绝不自动转成正文违禁词。只有落在
   preflight.PROMOTABLE_RISK_TERMS 白名单里的词法候选才进入 observed_terms 提案；
4. 输出 dynamic_banned_words.json 是「观察提案」，不是已生效规则；
   compliance_check.py 只加载白名单内候选，且规则文件损坏时 fail-closed；
5. 无有效证据时自愈回静态基线，并清理由旧脏数据生成的平台规则文件。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from conf import BASE_DIR  # noqa: E402
from myUtils import preflight  # noqa: E402
import feedback_schema  # noqa: E402

DB_PATH = Path(BASE_DIR) / "db" / "database.db"
VIOLATIONS_DIR = OMNIPOST_DIR.parent / "logs" / "violations"
VIOLATIONS_DIR.mkdir(parents=True, exist_ok=True)

DYNAMIC_BANNED_PATH = VIOLATIONS_DIR / "dynamic_banned_words.json"
AVOID_GUIDE_PATH = VIOLATIONS_DIR / "avoid_guide.md"
PLATFORM_RULES_DIR = VIOLATIONS_DIR / "platform_rules"
PLATFORM_RULES_DIR.mkdir(parents=True, exist_ok=True)

SCHEMA_VERSION = "evidence-v2"
ACTIONABLE_STATUSES = ("warned", "deleted", "throttled", "under_review")
STATIC_BANNED_WORDS = preflight.STATIC_BANNED_WORDS  # 唯一来源，不复制
PROMOTABLE = set(preflight.PROMOTABLE_RISK_TERMS)

PLATFORM_NAME_MAP = {
    5: "百家号", 7: "今日头条", 8: "搜狐号", 9: "知乎",
    11: "微博", 15: "企鹅号", 13: "网易号", 14: "大鱼号",
}


# ============================================================ 读取与去重

def _feedback_supports_evidence(db_path: Path) -> bool:
    """旧库/未建表时返回 False，调用方据此给出「证据能力缺失」而不是猜。"""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_feedback'"
            ).fetchone()
            if not table:
                return False
            return "evidence_quality" in {
                row[1] for row in conn.execute("PRAGMA table_info(platform_feedback)")
            }
    except sqlite3.Error:
        return False


def load_feedback(db_path: Path, days: int = 30) -> List[Dict[str, Any]]:
    """读取近 N 天巡检记录，按 (history_id, 日期) 保留最新一条。

    verified 与 unverified 都返回，由上层分别处理；
    旧库缺 evidence_quality 时返回空并打印明确提示，避免把历史脏数据当证据；
    review_status 为可选列，旧库动态投影为 NULL，保持只读兼容。
    """
    if not _feedback_supports_evidence(db_path):
        print("⚠️ platform_feedback 缺少 evidence_quality 列，本次不采信任何历史记录", file=sys.stderr)
        return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(platform_feedback)")}
        review_select = "review_status" if "review_status" in columns else "NULL AS review_status"
        rows = conn.execute(
            f"""
            SELECT id, history_id, episode, platform_type, platform_name,
                   published_url, check_status, evidence_quality, evidence_source,
                   evidence_text, evidence_reason, check_at, raw_snapshot,
                   {review_select}
            FROM platform_feedback
            WHERE date(check_at) >= ?
            ORDER BY check_at DESC, id DESC
            """,
            (cutoff,),
        ).fetchall()

    seen: Set[tuple[int, str]] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = (row["history_id"], str(row["check_at"])[:10])
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


# ============================================================ 信号提取

HUMAN_REVIEWED_STATUSES = feedback_schema.HUMAN_REVIEWED_STATUSES
NO_AGGREGATION_REVIEW_STATUSES = frozenset({"confirmed_normal", "ignored"})


def extract_violation_signals(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """只从 verified 异常提取信号；人工确认正常/忽略不再算待复核。"""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    unverified = 0
    human_reviewed = 0
    for r in records:
        review_status = str(r.get("review_status") or "")
        if review_status in NO_AGGREGATION_REVIEW_STATUSES:
            human_reviewed += 1
            continue
        if review_status in HUMAN_REVIEWED_STATUSES:
            human_reviewed += 1
        if r.get("evidence_quality") != "verified":
            unverified += 1
            continue
        if r.get("check_status") not in ACTIONABLE_STATUSES:
            continue
        key = f"{r['platform_name']}|{r['check_status']}"
        buckets.setdefault(key, []).append(r)

    signals: Dict[str, Any] = {
        "unverified_count": unverified,
        "human_reviewed_count": human_reviewed,
        "buckets": {},
    }
    for key, items in buckets.items():
        platform, status = key.split("|", 1)
        verdict_counter: Counter[str] = Counter()
        term_counter: Counter[str] = Counter()
        episodes: Set[str] = set()
        samples: List[str] = []
        urls: List[str] = []

        for r in items:
            episodes.add(str(r.get("episode") or "?"))
            verdict = (r.get("evidence_text") or "").strip()
            if verdict:
                verdict_counter[verdict] += 1
                samples.append(verdict[:300])
                # 只有白名单内的词法候选才计入词频
                for term in PROMOTABLE:
                    if term in verdict:
                        term_counter[term] += 1
            if r.get("published_url") and len(urls) < 3:
                urls.append(r["published_url"])

        signals["buckets"][key] = {
            "platform": platform,
            "platform_type": items[0]["platform_type"],
            "status": status,
            "count": len(items),                  # 去重后的不同链接·天
            "affected_episodes": sorted(episodes),
            "distinct_episodes": len(episodes),  # 规则升级看的是不同期数，不是巡检次数
            "top_verdicts": verdict_counter.most_common(10),  # 供人工复盘，不进词表
            "promotable_terms": term_counter.most_common(),    # 白名单内观察词
            "trigger_snippets": samples[:5],
            "sample_urls": urls,
        }
    return signals


# ============================================================ 产物生成

def build_dynamic_banned_words(signals: Dict[str, Any]) -> Dict[str, Any]:
    """生成「观察提案」而非自动生效规则。

    high_risk 永远等于静态基线；平台判词绝不进入。
    observed_terms 仅记录白名单候选在不同期数中出现次数，供人工确认后再改规则。
    """
    term_episodes: Dict[str, Set[str]] = {}
    for _key, data in signals.get("buckets", {}).items():
        for term, freq in data.get("promotable_terms", []):
            term_episodes.setdefault(term, set()).update(data["affected_episodes"])
            # freq 保留在 detail 中，计数口径以不同期数为准
            _ = freq

    observed = [
        {
            "term": term,
            "distinct_episodes": len(eps),
            "episodes": sorted(eps),
            "action": "proposal_only",  # 永不自动升级为拦截词
        }
        for term, eps in sorted(term_episodes.items(), key=lambda x: (-len(x[1]), x[0]))
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "version": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "source_days": 30,
        "status": "observation_proposal",
        "note": "本文件是观察提案，不是已生效规则。规则变更须人工确认并验证效果。",
        "high_risk": sorted(STATIC_BANNED_WORDS),
        "suspicious": [],
        "platform_specific": {},
        "observed_terms": observed,
        "stats": {
            "high_risk_count": len(STATIC_BANNED_WORDS),
            "suspicious_count": 0,
            "platforms_covered": 0,
            "verified_signal_buckets": len(signals.get("buckets", {})),
            "unverified_records_ignored": signals.get("unverified_count", 0),
            "human_reviewed_count": signals.get("human_reviewed_count", 0),
            "observed_term_count": len(observed),
        },
    }


def generate_avoid_guide(signals: Dict[str, Any], dynamic_words: Dict[str, Any]) -> str:
    """生成人工可读指南；明确区分已验证信号与待复核记录。"""
    lines = [
        f"# 违规避坑指南（观察提案，自动生成 {datetime.now():%Y-%m-%d %H:%M}）",
        "",
        "> 数据来源：近 30 天 `platform_feedback` 中 `evidence_quality='verified'` 的异常记录。",
        "> 同一链接同一天只计一次；平台判词整句不会自动变成正文违禁词。",
        f"> 静态零容忍词：{dynamic_words['stats']['high_risk_count']} 个；"
        f"待人工复核记录：{dynamic_words['stats']['unverified_records_ignored']} 条；"
        f"飞书人工已复核：{dynamic_words['stats'].get('human_reviewed_count', 0)} 条。",
        "",
        "## 1. 静态零容忍词（唯一来源：myUtils/preflight.py）",
        "",
    ]
    lines.extend(f"- `{w}`" for w in dynamic_words["high_risk"])

    lines.extend(["", "## 2. 观察词候选（仅提案，不自动拦截）", ""])
    observed = dynamic_words.get("observed_terms", [])
    if observed:
        lines.append("| 候选词 | 不同期数 | 涉及期数 | 状态 |")
        lines.append("|---|---:|---|---|")
        for item in observed:
            lines.append(
                f"| `{item['term']}` | {item['distinct_episodes']} | "
                f"{', '.join(item['episodes'])} | 待人工确认，未生效 |"
            )
    else:
        lines.append("暂无可采信的观察词。")

    lines.extend(["", "## 3. 按平台汇总（已验证证据）", ""])
    buckets = signals.get("buckets", {})
    if not buckets:
        lines.append("暂无 `verified` 异常记录。")
    for _key, data in sorted(buckets.items()):
        lines.append(f"### {data['platform']} · {data['status']}")
        lines.append(
            f"- 去重后记录：{data['count']} 条；不同期数：{data['distinct_episodes']} 个"
        )
        lines.append(f"- 受影响期数：{', '.join(data['affected_episodes'])}")
        if data["top_verdicts"]:
            lines.append("- 平台判词（供人工复盘，**不作为禁词**）：")
            for verdict, freq in data["top_verdicts"][:5]:
                lines.append(f"  - `{verdict}`（{freq} 次）")
        if data["sample_urls"]:
            lines.append(f"- 样例链接：{data['sample_urls'][0]}")
        lines.append("")

    unverified = signals.get("unverified_count", 0)
    if unverified:
        lines.extend([
            "## 4. 待人工复核（不进入规则聚合）",
            "",
            f"共 {unverified} 条 `unverified` 记录，常见成因：登录墙、反爬、正文容器未命中、巡检异常。",
            f"飞书人工已复核：{signals.get('human_reviewed_count', 0)} 条，不再重复列为待办。",
            "待复核项请到对应平台创作者后台核实，并在飞书复核台明确结论。",
            "",
        ])

    lines.extend([
        "## 5. 选题与写作自查",
        "",
        "| 维度 | 自查问题 | 规避动作 |",
        "|---|---|---|",
        "| 标题 | 是否含静态零容忍词/绝对化/诱导点击？ | 改为客观陈述 + 痛点场景 |",
        "| 正文 | 是否含免费试用/私信导流/联系方式？ | 删除引导词，不写外链 |",
        "| 图片 | 是否含二维码/水印/手机号？ | 纯净图片，Logo 仅用官方透明版 |",
        "| 标签 | 是否含营销/引流词？ | 用产品功能/行业痛点/场景词 |",
        "| 声明 | AI 声明是否按平台要求勾选？ | 发布前逐平台核对 |",
        "",
        "> 本指南只读，不修改合规闸门。任何规则变更须人工确认，并在验证效果后写入受保护版本。",
    ])
    return "\n".join(lines) + "\n"


def generate_platform_rules(signals: Dict[str, Any]) -> Dict[str, Any]:
    """生成只读建议文件；不参与发布拦截。"""
    rules: Dict[str, Any] = {}
    for _key, data in signals.get("buckets", {}).items():
        platform = data["platform"]
        rules[platform] = {
            "platform_type": data["platform_type"],
            "schema_version": SCHEMA_VERSION,
            "status": "proposal_only",
            "risk_level": "high" if data["status"] in ("warned", "deleted") else "medium",
            "verified_status": data["status"],
            "distinct_episodes": data["distinct_episodes"],
            "verdicts_for_review": [v for v, _f in data["top_verdicts"][:10]],
            "observed_terms": [t for t, _f in data["promotable_terms"]],
            "sample_urls": data["sample_urls"],
            "checklist": [
                "核对平台通知原文与创作者后台状态",
                "复盘对应期数的标题/正文/封面/标签",
                "确认 AI 声明与内文图位置",
                "若需改规则，先人工确认并补回归样本",
            ],
        }
    return rules


def write_outputs(dynamic_words: Dict[str, Any], guide_md: str,
                  platform_rules: Dict[str, Any]) -> Dict[str, Any]:
    """原子写入产物；主动清理旧格式平台规则，避免残留规则被误读。"""
    DYNAMIC_BANNED_PATH.write_text(
        json.dumps(dynamic_words, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    AVOID_GUIDE_PATH.write_text(guide_md, encoding="utf-8")

    expected = {f"{name}.json" for name in platform_rules}
    for old in PLATFORM_RULES_DIR.glob("*.json"):
        if old.name not in expected:
            old.unlink()
    for name, rule in platform_rules.items():
        (PLATFORM_RULES_DIR / f"{name}.json").write_text(
            json.dumps(rule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return {
        "updated": True,
        "dynamic_words": str(DYNAMIC_BANNED_PATH),
        "avoid_guide": str(AVOID_GUIDE_PATH),
        "platform_rules_count": len(platform_rules),
        "high_risk_count": dynamic_words["stats"]["high_risk_count"],
        "suspicious_count": dynamic_words["stats"]["suspicious_count"],
        "platforms_covered": len(platform_rules),
        "observed_term_count": dynamic_words["stats"]["observed_term_count"],
        "unverified_records_ignored": dynamic_words["stats"]["unverified_records_ignored"],
        "human_reviewed_count": dynamic_words["stats"].get("human_reviewed_count", 0),
        "verified_signal_buckets": dynamic_words["stats"]["verified_signal_buckets"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="违规反馈聚合（只产出观察提案，不自动改规则）")
    parser.add_argument("--days", type=int, default=30, help="回看天数")
    args = parser.parse_args(argv)

    print(f"📥 读取 platform_feedback（最近 {args.days} 天）...")
    records = load_feedback(DB_PATH, args.days)
    print(f"   去重后记录数：{len(records)}")

    signals = extract_violation_signals(records)
    print(
        f"   verified 信号桶：{len(signals['buckets'])}；"
        f"unverified 待复核：{signals['unverified_count']}；"
        f"人工已复核：{signals.get('human_reviewed_count', 0)}"
    )

    dynamic_words = build_dynamic_banned_words(signals)
    guide_md = generate_avoid_guide(signals, dynamic_words)
    platform_rules = generate_platform_rules(signals)
    result = write_outputs(dynamic_words, guide_md, platform_rules)

    print(f"📝 观察词库：{result['dynamic_words']}（status=observation_proposal）")
    print(f"📝 避坑指南：{result['avoid_guide']}")
    print(f"📝 平台建议：{result['platform_rules_count']} 个（均不参与拦截）")
    if result["unverified_records_ignored"]:
        print(f"⚠️ {result['unverified_records_ignored']} 条待人工复核记录未进入规则聚合")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
