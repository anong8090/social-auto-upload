#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""违规反馈闭环：聚合 platform_feedback → 更新合规词库 / 生成避坑指南。

职责（第 5 件核心）：
1. 从 platform_feedback 读取近 N 天 warned/deleted/throttled 记录
2. 按平台+状态分桶，提取高频警告关键词、触发片段、受影响期数
3. 生成/更新：
   - logs/violations/dynamic_banned_words.json  — 动态违规词库（供 compliance_check.py 优先加载）
   - logs/violations/avoid_guide.md              — 选题/写作阶段人工可读的避坑指南
   - logs/violations/platform_rules_<type>.json  — 各平台专用规则建议（标题/封面/标签/声明）
4. 合规闸门集成：compliance_check.py 增加动态词库加载逻辑（优先级：动态 > 静态）
5. 选题共创集成：避坑指南可被选题 Agent 读取做规避提示
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OMNIPOST_DIR))

from conf import BASE_DIR  # noqa: E402

DB_PATH = Path(BASE_DIR) / "db" / "database.db"
VIOLATIONS_DIR = OMNIPOST_DIR.parent / "logs" / "violations"
VIOLATIONS_DIR.mkdir(parents=True, exist_ok=True)

DYNAMIC_BANNED_PATH = VIOLATIONS_DIR / "dynamic_banned_words.json"
AVOID_GUIDE_PATH = VIOLATIONS_DIR / "avoid_guide.md"
PLATFORM_RULES_DIR = VIOLATIONS_DIR / "platform_rules"
PLATFORM_RULES_DIR.mkdir(parents=True, exist_ok=True)

# 静态兜底（来自 AGENTS.md & compliance_check.py）
STATIC_BANNED_WORDS = [
    "15天免费试点", "免费试跑", "免费领", "私信领资料", "加微信",
    "扫码体验", "点击链接", "www.ricent.com", "国家级", "第一",
    "唯一", "100%零失误", "全网最强",
]
# 需要上下文判断的模糊词（高频出现在 warned 时才加入）
FUZZY_CANDIDATES = [
    "免费", "试用", "领取", "私信", "微信", "二维码", "联系方式",
    "绝对", "零失误", "最强", "第一", "唯一", "全网", "全国",
    "营销", "推广", "广告", "引流", "获客", "转化",
]

PLATFORM_NAME_MAP = {
    5: "百家号", 7: "今日头条", 8: "搜狐号", 9: "知乎",
    11: "微博", 15: "企鹅号", 13: "网易号", 14: "大鱼号",
}


def load_feedback(db_path: Path, days: int = 30) -> List[Dict[str, Any]]:
    """读取近 N 天 platform_feedback 异常记录。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT id, history_id, episode, platform_type, platform_name,
                   published_url, check_status, warning_text, content_visible,
                   http_status, check_at, raw_snapshot
            FROM platform_feedback
            WHERE check_status IN ('warned', 'deleted', 'throttled', 'under_review')
              AND date(check_at) >= ?
            ORDER BY check_at DESC
            """,
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def extract_violation_signals(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从反馈记录中提取违规信号。"""
    # 按平台+状态分桶
    buckets: Dict[str, List[Dict]] = {}
    for r in records:
        key = f"{r['platform_name']}|{r['check_status']}"
        buckets.setdefault(key, []).append(r)

    signals = {}
    for key, items in buckets.items():
        platform, status = key.split("|", 1)
        # 收集警告文本
        warning_texts = [r["warning_text"] for r in items if r["warning_text"]]
        # 关键词频次
        kw_counter = Counter()
        for wt in warning_texts:
            for kw in wt.split("; "):
                kw_counter[kw.strip()] += 1

        # 从 raw_snapshot 提取疑似触发片段（启发式）
        trigger_snippets = []
        for r in items:
            snap = r.get("raw_snapshot", "")
            if snap:
                # 取前 500 字符作为片段
                trigger_snippets.append(snap[:500])

        signals[key] = {
            "platform": platform,
            "platform_type": items[0]["platform_type"],
            "status": status,
            "count": len(items),
            "affected_episodes": sorted(set(r["episode"] for r in items)),
            "top_keywords": kw_counter.most_common(20),
            "warning_texts": warning_texts[:10],
            "trigger_snippets": trigger_snippets[:5],
            "sample_urls": [r["published_url"] for r in items[:3]],
        }
    return signals


def build_dynamic_banned_words(signals: Dict[str, Any]) -> Dict[str, Any]:
    """构建动态违规词库（分层：高危/疑似/平台专用）。"""
    high_risk: Set[str] = set()
    suspicious: Set[str] = set()
    platform_specific: Dict[str, List[str]] = {}

    for key, data in signals.items():
        platform = data["platform"]
        status = data["status"]

        for kw, freq in data["top_keywords"]:
            kw_lower = kw.lower()
            # 直接出现在警告文本中的关键词 -> 高危
            if status in ("warned", "deleted") and freq >= 2:
                high_risk.add(kw)
            # 频次较低或 throttled/under_review -> 疑似
            elif freq >= 1:
                suspicious.add(kw)

        # 平台专用黑名单
        if data["count"] >= 2:
            plat_kws = [kw for kw, _ in data["top_keywords"][:10]]
            platform_specific[platform] = plat_kws

    # 合并静态兜底
    all_high = set(STATIC_BANNED_WORDS) | high_risk

    return {
        "version": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "source_days": 30,
        "high_risk": sorted(all_high),
        "suspicious": sorted(suspicious - all_high),
        "platform_specific": platform_specific,
        "stats": {
            "high_risk_count": len(all_high),
            "suspicious_count": len(suspicious - all_high),
            "platforms_covered": len(platform_specific),
        },
    }


def generate_avoid_guide(signals: Dict[str, Any], dynamic_words: Dict[str, Any]) -> str:
    """生成 Markdown 避坑指南（供选题/写作阶段人工/ Agent 读取）。"""
    lines = []
    lines.append(f"# 🚫 违规避坑指南（自动生成 {datetime.now():%Y-%m-%d %H:%M}）")
    lines.append("")
    lines.append(f"> 数据来源：近 30 天 `platform_feedback` 表中 `warned/deleted/throttled/under_review` 记录")
    lines.append(f"> 高危词库条目：{dynamic_words['stats']['high_risk_count']} 个 | 疑似词：{dynamic_words['stats']['suspicious_count']} 个")
    lines.append("")

    # 1. 通用高危词汇
    lines.append("## 1️⃣ 通用高危词汇（全平台严禁）")
    lines.append("")
    lines.append("以下词汇在近期巡检中**直接触发平台违规/删除/限流**，写作时**严禁出现**：")
    lines.append("")
    for w in dynamic_words["high_risk"]:
        lines.append(f"- `{w}`")
    lines.append("")

    # 2. 疑似风险词（需上下文判断）
    if dynamic_words["suspicious"]:
        lines.append("## 2️⃣ 疑似风险词（视上下文谨慎使用）")
        lines.append("")
        lines.append("以下词汇高频出现在警告/限流场景，**非必要不要用**，若必须用请确保语境合规：")
        lines.append("")
        for w in dynamic_words["suspicious"]:
            lines.append(f"- `{w}`")
        lines.append("")

    # 3. 平台专用避坑规则
    lines.append("## 3️⃣ 平台专用避坑规则")
    lines.append("")

    for key, data in signals.items():
        platform = data["platform"]
        status = data["status"]
        count = data["count"]
        if count < 2:
            continue

        lines.append(f"### {platform}（{PLATFORM_NAME_MAP.get(data['platform_type'], '')}）")
        lines.append(f"- **异常状态**：{status} × {count} 次")
        lines.append(f"- **受影响期数**：{', '.join(data['affected_episodes'])}")
        lines.append("")

        if data["top_keywords"]:
            lines.append("**高频警告关键词**：")
            for kw, freq in data["top_keywords"][:8]:
                lines.append(f"- `{kw}`（{freq} 次）")
            lines.append("")

        if data["trigger_snippets"]:
            lines.append("**典型触发片段（供人工复盘）**：")
            for i, snip in enumerate(data["trigger_snippets"][:2], 1):
                lines.append(f"> {snip[:300]}...")
                lines.append("")

        # 平台级建议
        if status == "warned":
            lines.append("💡 **写作建议**：标题/正文/标签中避开上述关键词；检查 AI 声明是否按要求勾选；封面图无水印/二维码。")
        elif status == "throttled":
            lines.append("💡 **写作建议**：疑似触发质量模型/限流。优化标题吸引力（非标题党）、提升正文原创度、避免纯营销话术、确保图文匹配度。")
        elif status == "deleted":
            lines.append("💡 **写作建议**：内容被平台删除。人工复盘对应期数全文，核对平台社区规范与违规申诉渠道。")
        elif status == "under_review":
            lines.append("💡 **写作建议**：审核超时。检查是否含敏感词/图片，或账号权重不足导致长审。")

        lines.append("---")
        lines.append("")

    # 4. 选题阶段规避清单
    lines.append("## 4️⃣ 选题阶段规避清单（共创时自查）")
    lines.append("")
    lines.append("| 维度 | 自查问题 | 规避动作 |")
    lines.append("|------|----------|----------|")
    lines.append("| 标题 | 是否含高危词/绝对化/诱导点击？ | 替换为客观陈述+痛点场景 |")
    lines.append("| 正文 | 是否含免费试用/私信导流/联系方式？ | 删去引导词，改为「文末获取方案」类软植入 |")
    lines.append("| 图片 | 封面/内文图是否含二维码/水印/手机号？ | 纯净图片，Logo 仅用官方透明版 |")
    lines.append("| 标签 | 话题标签是否含营销/广告/引流词？ | 用产品功能/行业痛点/场景词 |")
    lines.append("| 声明 | AI 声明是否按平台要求勾选？ | 发布前逐平台核对声明项 |")
    lines.append("")

    lines.append("> 🤖 **Agent 使用提示**：选题共创/写作前请 `Read` 本文件，对生成的标题/大纲/正文做关键词扫描，命中即标红提示用户修改。")

    return "\n".join(lines)


def generate_platform_rules(signals: Dict[str, Any]) -> Dict[str, Any]:
    """生成各平台专用规则建议（JSON，供自动化发布脚本参考）。"""
    rules = {}
    for key, data in signals.items():
        platform = data["platform"]
        ptype = data["platform_type"]
        status = data["status"]
        if data["count"] < 2:
            continue

        rules[platform] = {
            "platform_type": ptype,
            "risk_level": "high" if status in ("warned", "deleted") else "medium",
            "trigger_keywords": [kw for kw, _ in data["top_keywords"][:15]],
            "avoid_in_title": [kw for kw, _ in data["top_keywords"][:10] if len(kw) <= 20],
            "avoid_in_body": [kw for kw, _ in data["top_keywords"][:15]],
            "avoid_in_tags": [kw for kw, _ in data["top_keywords"][:10] if "营销" in kw or "广告" in kw or "引流" in kw],
            "checklist": [
                f"标题长度符合 {platform} 红线",
                "正文无高危词/疑似词",
                "封面图纯净（无二维码/水印/联系方式）",
                f"AI 声明按 {platform} 要求勾选",
                "内文图位置/数量符合规范",
            ],
            "sample_violations": data["trigger_snippets"][:3],
        }
    return rules


def update_compliance_check_integration(dynamic_words: Dict[str, Any]) -> str:
    """生成 compliance_check.py 的动态加载补丁代码（供人工合并或自动应用）。"""
    patch = f'''# ===== 动态违规词库加载（自动生成 {datetime.now():%Y-%m-%d}）=====
# 将此段合并到 compliance_check.py 顶部 import 之后、BANNED_WORDS 定义之前

import json
from pathlib import Path

_DYNAMIC_BANNED_PATH = Path(__file__).parent.parent / "logs" / "violations" / "dynamic_banned_words.json"

def _load_dynamic_banned() -> tuple[list[str], list[str], dict[str, list[str]]]:
    """加载动态词库，返回 (high_risk, suspicious, platform_specific)。"""
    if _DYNAMIC_BANNED_PATH.exists():
        try:
            data = json.loads(_DYNAMIC_BANNED_PATH.read_text(encoding="utf-8"))
            return (
                data.get("high_risk", []),
                data.get("suspicious", []),
                data.get("platform_specific", {{}}),
            )
        except Exception:
            pass
    return [], [], {{}}

DYNAMIC_HIGH_RISK, DYNAMIC_SUSPICIOUS, DYNAMIC_PLATFORM_SPECIFIC = _load_dynamic_banned()

# 合并策略：动态高危 > 静态高危 > 动态疑似
# 使用时：BANNED_WORDS = list(set(STATIC_BANNED_WORDS) | set(DYNAMIC_HIGH_RISK))
# 平台专用：PLATFORM_BANNED[platform] = DYNAMIC_PLATFORM_SPECIFIC.get(platform, [])
'''
    return patch


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="违规反馈闭环：聚合分析 → 更新词库/生成避坑指南")
    parser.add_argument("--days", type=int, default=30, help="回看天数")
    parser.add_argument("--apply-patch", action="store_true", help="输出 compliance_check.py 动态加载补丁")
    args = parser.parse_args(argv)

    print(f"📥 读取 platform_feedback（最近 {args.days} 天）...")
    records = load_feedback(DB_PATH, args.days)
    print(f"   异常记录数：{len(records)}")

    if not records:
        print("✅ 无异常记录，词库/指南保持现状")
        return 0

    signals = extract_violation_signals(records)
    print(f"   聚类信号数：{len(signals)}")

    # 1) 动态词库
    dynamic_words = build_dynamic_banned_words(signals)
    DYNAMIC_BANNED_PATH.write_text(json.dumps(dynamic_words, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📝 动态词库已写入：{DYNAMIC_BANNED_PATH}")
    print(f"   高危：{dynamic_words['stats']['high_risk_count']} 个 | 疑似：{dynamic_words['stats']['suspicious_count']} 个 | 平台专用：{dynamic_words['stats']['platforms_covered']} 个")

    # 2) 避坑指南
    guide_md = generate_avoid_guide(signals, dynamic_words)
    AVOID_GUIDE_PATH.write_text(guide_md, encoding="utf-8")
    print(f"📝 避坑指南已写入：{AVOID_GUIDE_PATH}")

    # 3) 平台专用规则
    platform_rules = generate_platform_rules(signals)
    for platform, rule in platform_rules.items():
        rule_path = PLATFORM_RULES_DIR / f"{platform}.json"
        rule_path.write_text(json.dumps(rule, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📝 平台专用规则已写入：{PLATFORM_RULES_DIR}（{len(platform_rules)} 个平台）")

    # 4) 合规闸门补丁
    if args.apply_patch:
        patch = update_compliance_check_integration(dynamic_words)
        patch_path = VIOLATIONS_DIR / "compliance_check_patch.py"
        patch_path.write_text(patch, encoding="utf-8")
        print(f"📝 合规闸门补丁已写入：{patch_path}")
        print("   请人工合并到 omnipost/scripts/compliance_check.py")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())