#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前合规闸门（Level 0：零容忍词、标题长度、正文字数、图片规格）。

设计原则：
- 只做「能否发布」的静态判定，不打开浏览器、不改数据；
- 复用 preflight.py 的共享常量（TITLE_LIMITS、COVER_REQUIRED_PLATFORMS、STATIC_BANNED_WORDS）；
- 返回结构化结果（passed / problems / warnings / matched_*）；
- 供 publish_episode.py、CLI 与定时任务复用。

证据链约束（2026-11-05 收口）：
- 静态零容忍词表只有 myUtils/preflight.py 一份来源；
- logs/violations/dynamic_banned_words.json 只提供「观察候选」，且必须落在
  preflight.PROMOTABLE_RISK_TERMS 白名单内才可能被采信，整句平台判词永不成为正文违禁词；
- 词库文件存在但损坏/格式非法 -> 抛 ComplianceRuleError，调用方必须拦截发布，禁止静默降级；
- 词库文件不存在 -> 视为尚无提案，退回静态基线。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# 复用项目共享常量
OMNIPOST_DIR = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(OMNIPOST_DIR))

from myUtils import preflight  # noqa: E402

# ============================================================ 动态违规词库加载（fail-closed、只读观察词）

_DYNAMIC_BANNED_PATH = OMNIPOST_DIR.parent / "logs" / "violations" / "dynamic_banned_words.json"


class ComplianceRuleError(RuntimeError):
    """合规规则不可信时抛出；调用方必须拦截发布，不得降级放行。"""


# 规则装载错误：模块导入时不抛（便于 CLI 输出可读提示），但在每次合规判断时 fail-closed。
_RULE_LOAD_ERROR: Optional[str] = None


def _load_dynamic_banned() -> tuple[Set[str], Set[str], Dict[str, List[str]]]:
    """加载动态词库，返回 (high_risk, suspicious, platform_specific)。

    文件不存在视为「尚无提案」（允许用静态基线发布）；
    文件存在但损坏/格式不合法：记录错误，由 compliance_check() 抛 ComplianceRuleError，
    绝不静默退回到静态词表继续放行。
    """
    global _RULE_LOAD_ERROR
    _RULE_LOAD_ERROR = None
    if not _DYNAMIC_BANNED_PATH.exists():
        return set(), set(), {}
    try:
        data = json.loads(_DYNAMIC_BANNED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _RULE_LOAD_ERROR = f"动态词库不可读或 JSON 非法: {_DYNAMIC_BANNED_PATH}: {exc}"
        return set(), set(), {}
    if not isinstance(data, dict):
        _RULE_LOAD_ERROR = f"动态词库根节点必须是对象: {_DYNAMIC_BANNED_PATH}"
        return set(), set(), {}

    # 旧证据链产出的词库（无 schema_version）含平台判词整句，一律不采信；
    # 由 violation_feedback.py 重新生成 evidence-v2 观察提案后才启用。
    if data.get("schema_version") != "evidence-v2":
        return set(), set(), {}

    def _words(key: str) -> Optional[List[str]]:
        value = data.get(key, [])
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            _RULE_LOAD_ERROR = f"动态词库 {key} 必须是字符串数组: {_DYNAMIC_BANNED_PATH}"
            return None
        return value

    high_risk_raw = _words("high_risk")
    suspicious_raw = _words("suspicious")
    if high_risk_raw is None or suspicious_raw is None:
        return set(), set(), {}

    platform_specific = data.get("platform_specific", {})
    if not isinstance(platform_specific, dict) or any(
        not isinstance(k, str) or not isinstance(v, list) or any(not isinstance(x, str) for x in v)
        for k, v in platform_specific.items()
    ):
        _RULE_LOAD_ERROR = f"动态词库 platform_specific 格式非法: {_DYNAMIC_BANNED_PATH}"
        return set(), set(), {}

    # 只采信白名单内的词法候选；整句平台判词不得被当作正文违禁词。
    allowed = set(preflight.PROMOTABLE_RISK_TERMS)
    high_risk = {w for w in high_risk_raw if w in allowed}
    suspicious = {w for w in suspicious_raw if w in allowed}
    # 静态基线词本就不在 PROMOTABLE 白名单里，不算「脏词」；
    # 只提示既非静态词表、又不在白名单的可疑条目。
    baseline = set(preflight.STATIC_BANNED_WORDS)
    ignored = set(high_risk_raw) | set(suspicious_raw)
    ignored -= baseline
    ignored -= allowed
    if ignored:
        print(
            f"ℹ️ 动态词库有 {len(ignored)} 个非白名单条目未进入拦截规则（平台判词/旧脏词）",
            file=sys.stderr,
        )
    plat = {k: [w for w in v if w in allowed] for k, v in platform_specific.items()}
    return high_risk, suspicious, plat


DYNAMIC_HIGH_RISK, DYNAMIC_SUSPICIOUS, DYNAMIC_PLATFORM_SPECIFIC = _load_dynamic_banned()

# ============================================================ 规则常量（Level 0 基线）

# 唯一词表来源：myUtils/preflight.py（与 AGENTS.md 保持一致）
STATIC_BANNED_WORDS = preflight.STATIC_BANNED_WORDS

# 合并：静态零容忍 ∪ 动态白名单内已观察词
BANNED_WORDS = tuple(sorted(set(STATIC_BANNED_WORDS) | DYNAMIC_HIGH_RISK))

# 正文字数要求（AGENTS.md 第二节）
BODY_WORD_RANGES = {
    "long": (800, 1100),   # 母本 B：精炼长文
    "short": (300, 500),   # 母本 A：短图文与动态
}

# 长文平台（用母本 B）与短图文平台（用母本 A）
LONG_FORM_PLATFORMS = (5, 7, 8, 9, 12, 13, 14, 15)
SHORT_FORM_PLATFORMS = (10, 11)

# 要求正文穿插 2 张 16:9 内文图的长文平台（不含微信公众号 12，它走图文混排）
INLINE_IMAGE_PLATFORMS = (5, 7, 8, 9, 13, 14)


# ============================================================ 辅助函数

def _body_length(body: str) -> int:
    """去除所有空白字符后的字符数（中文字符按 1 计，与 AGENTS.md 字数口径一致）。"""
    return len(re.sub(r"\s", "", body or ""))


_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+)$")
_RULE_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_IMAGE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _table_cells(line: str) -> List[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return []
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return len(cells) >= 2 and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells
    )


def find_markdown_leaks(body: str) -> List[str]:
    """检查清洗后正文是否仍含会原样发布的 Markdown 源码。"""
    problems: List[str] = []
    checks = (
        (r"^\s*#{1,6}\s+", "标题标记 # / ## / ###"),
        (r"^\s*>", "引用/元数据行 >"),
        (r"!\[[^\]]*\]\(", "图片引用 ![](...)"),
        (r"\[[^\]]+\]\([^)]+\)", "链接语法 [](...)"),
        (r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "水平分隔线 ---"),
        (r"^\s*\|.*\|\s*$", "表格竖线 |"),
    )
    for pattern, label in checks:
        if re.search(pattern, body, flags=re.MULTILINE):
            problems.append(label)
    if "**" in body or "```" in body or re.search(r"(?<!\w)__(?!\w)", body):
        problems.append("加粗/代码块标记")
    return problems


# ============================================================ 核心合规检查

def compliance_check(
    title: str,
    body: str,
    platform_type: int,
    *,
    has_cover: bool,
    inline_count: int = 0,
) -> Dict[str, Any]:
    """返回结构化合规结果。

    Args:
        title: 当期平台专属标题
        body: 当期平台专属正文（已清洗 Markdown）
        platform_type: 平台类型（preflight.PLATFORM_NAMES 的 key）
        has_cover: 是否已有 16:9 封面图（01_16x9_头图封面_*.png）
        inline_count: 可用的 16:9 内文图数量（02/03_16x9_内文图*.png）

    Returns:
        {
            "passed": bool,
            "problems": List[str],           # 阻断性错误（不通过即拦截发布）
            "warnings": List[str],           # 非阻断告警（疑似风险词、最佳实践建议）
            "matched_banned": List[str],     # 命中的高危违规词
            "matched_suspicious": List[str], # 命中的疑似风险词
        }
    """
    problems: List[str] = []
    warnings: List[str] = []
    matched_banned: List[str] = []
    matched_suspicious: List[str] = []

    # 0) 规则本身不可信时 fail-closed：不进入逐项检查，整批拦截发布
    if _RULE_LOAD_ERROR:
        return {
            "passed": False,
            "problems": [_RULE_LOAD_ERROR],
            "warnings": ["合规规则装载失败，本轮全部平台禁止发布；修复词库后重跑"],
            "matched_banned": [],
            "matched_suspicious": [],
        }

    # 1) 标题长度红线
    low, high = preflight.TITLE_LIMITS.get(platform_type, (1, 2000))
    title_len = len(title or "")
    if not title:
        problems.append("标题为空")
    elif title_len < low or title_len > high:
        problems.append(f"标题 {title_len} 字，超出该平台 {low}~{high} 字红线")

    # 2) 正文字数区间
    form = "long" if platform_type in LONG_FORM_PLATFORMS else "short"
    word_low, word_high = BODY_WORD_RANGES[form]
    length = _body_length(body)
    if length < word_low or length > word_high:
        label = "长文母本" if form == "long" else "短图文母本"
        problems.append(f"正文 {length} 字，超出{label} {word_low}~{word_high} 字要求")

    # 3) 零容忍违规词（通用高危 + 平台专用）
    text = f"{title}\n{body}"
    banned_hits = [w for w in BANNED_WORDS if w in text]
    if banned_hits:
        matched_banned = banned_hits
        problems.append(f"命中违规词: {', '.join(banned_hits)}")

    # 3b) 平台专用黑名单
    platform_name = preflight.PLATFORM_NAMES.get(platform_type, "")
    platform_specific = DYNAMIC_PLATFORM_SPECIFIC.get(platform_name, [])
    if platform_specific:
        plat_hits = [w for w in platform_specific if w in text]
        if plat_hits:
            matched_banned.extend(plat_hits)
            problems.append(f"命中 {platform_name} 专用违规词: {', '.join(plat_hits)}")

    # 3c) 疑似风险词（告警不拦截）
    suspicious_hits = [w for w in DYNAMIC_SUSPICIOUS if w in text]
    if suspicious_hits:
        matched_suspicious = suspicious_hits
        warnings.append(f"⚠️ 命中疑似风险词（建议人工复核上下文）: {', '.join(suspicious_hits)}")

    # 4) Markdown 残留
    leaks = find_markdown_leaks(body)
    if leaks:
        problems.append(f"正文残留 Markdown 源码: {', '.join(leaks)}")

    # 5) 封面图要求
    if platform_type in preflight.COVER_REQUIRED_PLATFORMS and not has_cover:
        problems.append("该平台要求 16:9 封面，但未找到 01_16x9 头图")

    # 6) 内文图要求
    if platform_type in INLINE_IMAGE_PLATFORMS and inline_count < 2:
        problems.append(f"该平台要求正文穿插 2 张 16:9 内文图，当前仅 {inline_count} 张")

    return {
        "passed": len(problems) == 0,
        "problems": problems,
        "warnings": warnings,
        "matched_banned": matched_banned,
        "matched_suspicious": matched_suspicious,
    }


# ============================================================ CLI 入口（手动验样用）

def _load_sample_case(case_name: str) -> Dict[str, Any]:
    """加载内置必拦样本。"""
    samples = {
        "title_too_long": {
            "title": "这是一个超级超级超级超级超级超级超级超级超级超级超级超级长的标题",
            "body": "正文内容符合要求，没有违规词，字数也在范围内。" * 20,
            "platform_type": 5,
            "has_cover": True,
            "inline_count": 2,
            "expect_fail": ["标题"],
        },
        "banned_word": {
            "title": "正常标题",
            "body": "正文里含有免费试跑这个违规词，应该被拦截。" * 10,
            "platform_type": 7,
            "has_cover": True,
            "inline_count": 2,
            "expect_fail": ["违规词"],
        },
        "missing_cover": {
            "title": "正常标题",
            "body": "正文内容完全合规，字数达标，但缺少封面图。" * 15,
            "platform_type": 8,
            "has_cover": False,
            "inline_count": 2,
            "expect_fail": ["封面"],
        },
        "inline_short": {
            "title": "正常标题",
            "body": "正文内容完全合规，字数达标，有封面但只有 1 张内文图。" * 15,
            "platform_type": 9,
            "has_cover": True,
            "inline_count": 1,
            "expect_fail": ["内文图"],
        },
        "all_pass": {
            "title": "河狸云蓝枢：工程现场看图神器实测",
            "body": "工程现场看图痛点真不少，图纸版本乱、批注找不到、离线看不了，三大根因让现场人员天天翻车。蓝枢从版本管控、批注同步、离线缓存三个维度破局，配合微信看图小程序随时随地打开。现场避坑建议：统一图纸入口、强制批注归档、提前缓存离线包。" * 8,
            "platform_type": 5,
            "has_cover": True,
            "inline_count": 2,
            "expect_fail": [],
        },
    }
    return samples[case_name]


def _sample_names() -> List[str]:
    """返回所有内置样本名称。"""
    return ["title_too_long", "banned_word", "missing_cover", "inline_short", "all_pass"]


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="合规闸门 CLI - 验证样本与手动检查")
    parser.add_argument("--case", choices=_sample_names(),
                        help="运行内置必拦样本（自带平台与参数）")
    parser.add_argument("--title", help="自定义标题")
    parser.add_argument("--body", help="自定义正文")
    parser.add_argument("--platform", type=int, help="平台类型，如 5=百家号（--case 时可省略）")
    parser.add_argument("--has-cover", action="store_true", help="有 16:9 封面")
    parser.add_argument("--inline-count", type=int, default=0, help="可用内文图数量")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    if args.case:
        sample = _load_sample_case(args.case)
        title = sample["title"]
        body = sample["body"]
        platform = sample["platform_type"]
        has_cover = sample["has_cover"]
        inline_count = sample["inline_count"]
        expect = sample["expect_fail"]
    else:
        if args.platform is None or not args.title or not args.body:
            parser.error("自定义模式需提供 --platform --title --body")
        title = args.title
        body = args.body
        platform = args.platform
        has_cover = args.has_cover
        inline_count = args.inline_count
        expect = None

    result = compliance_check(title, body, platform, has_cover=has_cover, inline_count=inline_count)
    problems = result["problems"]
    warnings = result["warnings"]
    matched_banned = result["matched_banned"]
    matched_suspicious = result["matched_suspicious"]
    passed = result["passed"]

    if args.json:
        import json
        print(json.dumps({
            "passed": passed,
            "problems": problems,
            "warnings": warnings,
            "matched_banned": matched_banned,
            "matched_suspicious": matched_suspicious,
        }, ensure_ascii=False))
        return 0 if passed else 1

    if problems:
        print("❌ 合规拦截：")
        for p in problems:
            print(f"  - {p}")
    else:
        print("✅ 合规通过")

    if warnings:
        print("\n⚠️ 告警（不拦截，建议人工复核）：")
        for w in warnings:
            print(f"  - {w}")

    if expect is not None:
        # 校验必拦样本是否按预期拦截
        if len(expect) == 0:
            # 预期全通过
            if passed:
                print(f"✅ 样本 {args.case} 按预期全通过")
            else:
                print(f"⚠️ 样本 {args.case} 预期全通过但实际被拦截: {problems}")
                return 2
        else:
            expected_hits = any(e in " ".join(problems) for e in expect)
            if expected_hits:
                print(f"✅ 样本 {args.case} 按预期被拦截")
            else:
                print(f"⚠️ 样本 {args.case} 预期拦截 {expect} 但实际通过了")
                return 2

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())