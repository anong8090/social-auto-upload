#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把个人 IP 模式 B 长文拆成模式 A 候选骨架。

只做结构提取：不调用模型、不生成完整正文、不启动浏览器、不写数据库。
生成的骨架必须由人补写判断与正文，再跑 publish_ai_share.py --check-only。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

KEYWORD_RE = re.compile(r"判断|边界|代价|不是|而是|必须|应该|先|再|只|规则|证据|权限|验收")
PUNCT_RE = re.compile(r"[。！？!?；;，,]")


def parse_longform(day_dir: Path) -> Tuple[str, List[Dict[str, object]]]:
    path = day_dir / "内容.md"
    if not path.is_file():
        raise SystemExit(f"缺少内容文件：{path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    title = ""
    title_index = -1
    for index, line in enumerate(lines):
        if line.strip().startswith("# "):
            title = line.strip()[2:].strip()
            title_index = index
            break
    if not title:
        raise SystemExit(f"{path} 缺少一级标题（# 标题）")

    body = "\n".join(lines[title_index + 1 :]).strip()
    sections: List[Dict[str, object]] = []
    current: Dict[str, object] = {"heading": "开篇", "paragraphs": []}
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block:
            continue
        if block.startswith("## "):
            if current["paragraphs"]:
                sections.append(current)
            current = {"heading": block[3:].strip(), "paragraphs": []}
        else:
            current["paragraphs"].append(block)
    if current["paragraphs"]:
        sections.append(current)
    if not sections:
        raise SystemExit(f"{path} 没有可拆分的正文段落")
    return title, sections


def key_sentences(paragraphs: List[str], limit: int = 3) -> List[str]:
    result: List[str] = []
    for paragraph in paragraphs:
        for sentence in re.split(r"(?<=[。！？!?；;])", paragraph):
            sentence = sentence.strip()
            if not (16 <= len(sentence) <= 80):
                continue
            if not KEYWORD_RE.search(sentence):
                continue
            if sentence not in result:
                result.append(sentence)
            if len(result) >= limit:
                return result
    return result


def title_candidates(section: Dict[str, object], keys: List[str]) -> List[str]:
    heading = str(section["heading"])
    candidates: List[str] = []
    if heading and heading != "开篇":
        candidates.append(heading[:20])
    for sentence in keys:
        clause = re.split(r"[，,：:；;。！？!?]", sentence)[0].strip()
        if len(clause) < 4:
            clause = PUNCT_RE.sub("", sentence).strip()
        if clause:
            candidates.append(clause[:20])
    if heading and heading != "开篇":
        candidates.append((heading[:14] + "的一个判断")[:20])

    result: List[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in result:
            result.append(candidate[:20])
    return result[:3] or ["待定标题"]


def pick_sections(sections: List[Dict[str, object]], count: int) -> List[Dict[str, object]]:
    usable = [section for section in sections if section["paragraphs"]]
    if not usable:
        raise SystemExit("没有可用段落")
    if count <= 1 or len(usable) == 1:
        return [usable[0]]
    indices = []
    for i in range(count):
        index = round(i * (len(usable) - 1) / (count - 1))
        if index not in indices:
            indices.append(index)
    return [usable[index] for index in indices]


def slugify(title: str) -> str:
    slug = re.sub(r"[^\w一-鿿]+", "-", title).strip("-")
    return (slug[:40] or "longform")


def skeleton(title: str, source_title: str, angle: str, keys: List[str]) -> str:
    reusable = "\n".join(f"- {sentence}" for sentence in keys) or "- （长文未提取到合适句子，请回读原文）"
    return (
        f"# {title}\n\n"
        f"> 待补判断：这篇与长文《{source_title}》的区别是什么？"
        "写清一个具体判断，以及它的边界或代价。\n\n"
        "（待补：按 180~320 字重写，最多 3 段、最多 1 个小标题。不要直接复制长文段落。）\n\n"
        f"<!-- 建议角度：{angle}\n"
        f"可回看的长文句子：\n{reusable}\n"
        "配图：3:4 一图一概念；不要复用长文配图。 -->\n"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="把模式 B 长文拆成模式 A 候选骨架（只做结构提取）")
    parser.add_argument("--dir", required=True, type=Path, help="长文目录，需含 内容.md")
    parser.add_argument("--out", required=True, type=Path, help="输出根目录；会创建 <out>/_drafts/<slug>/01..N")
    parser.add_argument("--count", type=int, default=3, help="候选数量（默认 3）")
    parser.add_argument("--force", action="store_true", help="输出目录非空时覆盖")
    args = parser.parse_args(argv)

    day_dir = args.dir.expanduser().resolve()
    if not day_dir.is_dir():
        print(f"❌ 长文目录不存在：{day_dir}", file=sys.stderr)
        return 2
    out = args.out.expanduser().resolve()
    if out.exists() and any(out.iterdir()) and not args.force:
        print(f"❌ 输出目录非空：{out}\n加 --force 覆盖，或换一个 --out。", file=sys.stderr)
        return 2

    title, sections = parse_longform(day_dir)
    count = max(1, args.count)
    chosen = pick_sections(sections, count)
    slug = slugify(title)
    drafts_root = out / "_drafts" / slug
    if args.force and drafts_root.exists():
        shutil.rmtree(drafts_root)
    drafts_root.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# 拆分建议：{title}",
        "",
        f"> 源目录：`{day_dir}`",
        f"> 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "> 脚本只做结构提取，不生成成稿；标题与判断需人工重写。",
        "",
    ]
    for index, section in enumerate(chosen, 1):
        heading = str(section["heading"])
        paragraphs = [str(item) for item in section["paragraphs"]]
        keys = key_sentences(paragraphs, 3)
        candidates = title_candidates(section, keys)
        angle = heading if heading != "开篇" else "开篇判断"
        candidate_dir = drafts_root / f"{index:02d}"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        (candidate_dir / "内容.md").write_text(
            skeleton(candidates[0], title, angle, keys), encoding="utf-8"
        )
        reusable_lines = (
            [f"  - {sentence}" for sentence in keys]
            if keys
            else ["  - （未提取到合适句子）"]
        )
        lines.extend(
            [
                f"## 候选 {index}",
                "",
                f"- 建议角度：{angle}",
                f"- 标题候选：{' / '.join(candidates)}",
                "- 可回看的长文句子：",
                *reusable_lines,
                "- 必须新写：一个具体判断 + 边界或代价；不能把长文段落压缩后复用。",
                f"- 骨架：`{candidate_dir / '内容.md'}`",
                "",
            ]
        )
    (out / "拆分建议.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"✅ 已生成 {len(chosen)} 个候选骨架")
    print(f"   建议：{out / '拆分建议.md'}")
    print(f"   草稿：{drafts_root}")
    print("   下一步：人工补写判断与正文，再跑 publish_ai_share.py --check-only。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
