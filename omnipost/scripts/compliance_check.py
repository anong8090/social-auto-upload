#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前合规闸门（Level 0：零容忍词、标题长度、正文字数、图片规格）。

设计原则：
- 只做「能否发布」的静态判定，不打开浏览器、不改数据；
- 复用 preflight.py 的共享常量（TITLE_LIMITS、COVER_REQUIRED_PLATFORMS 等）；
- 返回不通过原因列表；空列表表示通过。
- 供 publish_episode.py、未来 CLI 与定时任务复用。
- 动态违规词库：优先加载 logs/violations/dynamic_banned_words.json（由 violation_feedback.py 生成），
  合并策略：动态高危 > 静态高危 > 动态疑似；平台专用黑名单在 compliance_check 入口按 platform_type 应用。
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

# ============================================================ 动态违规词库加载（优先级：动态 > 静态）

_DYNAMIC_BANNED_PATH = OMNIPOST_DIR.parent / "logs" / "violations" / "dynamic_banned_words.json"

def _load_dynamic_banned() -> tuple[Set[str], Set[str], Dict[str, List[str]]]:
    """加载动态词库，返回 (high_risk, suspicious, platform_specific)。"""
    if _DYNAMIC_BANNED_PATH.exists():
        try:
            data = json.loads(_DYNAMIC_BANNED_PATH.read_text(encoding="utf-8"))
            return (
                set(data.get("high_risk", [])),
                set(data.get("suspicious", [])),
                data.get("platform_specific", {}),
            )
        except Exception:
            pass
    return set(), set(), {}

DYNAMIC_HIGH_RISK, DYNAMIC_SUSPICIOUS, DYNAMIC_PLATFORM_SPECIFIC = _load_dynamic_banned()

# ============================================================ 规则常量（Level 0 基线）

# 静态零容忍违规词（AGENTS.md 第四节「零容忍清单」）
STATIC_BANNED_WORDS = (
    "15天免费试点", "免费试跑", "免费领", "私信领资料", "加微信", "扫码体验", "点击链接",
    "www.ricent.com", "国家级", "第一", "唯一", "100%零失误", "全网最强", "二维码",
)

# 合并：动态高危 ∪ 静态高危（动态优先，去重）
BANNED_WORDS = tuple(DYNAMIC_HIGH_RISK | set(STATIC_BANNED_WORDS))

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