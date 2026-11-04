# -*- coding: utf-8 -*-
"""五平台草稿正文 Markdown 统一解析器。

解决三个共性问题：
1. 以「### 正文内容」分隔符剥离前面的元数据说明行（作者、分类、标签等）；
2. 彻底清洗正文中的裸露 Markdown 标记（** 粗体、> 引用、### 标题、表格行），
   防止富文本编辑器把它们当纯文本显示、格式错乱；
3. 仅跳过 01_* 封面图，正文中的 02_* / 03_* 两张技术解析内文配图
   正常上传并插入到第一段和第二段之后。

对外接口：
    parse_body_blocks(md_path, inline_images=None, skip_cover=True)
        -> list[tuple[str, str | Path]]，元素为 ('text', 正文) 或 ('image', 图片路径)
    clean_markdown_body(md_path) -> str
"""
from __future__ import annotations

import re
from pathlib import Path

# 「### 正文内容」分隔符（也兼容无空格的写法与全角冒号）
BODY_SEPARATOR_RE = re.compile(r'^#{1,6}\s*正文内容\s*[:：]?\s*$')
HEADING_RE = re.compile(r'^#{1,6}\s*')
QUOTE_RE = re.compile(r'^>\s*')
# 列表标记：`- 内容` / `* 内容` / `1. 内容` / `1、内容`
LIST_RE = re.compile(r'^\s*(?:[-*+]\s+|\d+[.、)]\s*)')
BOLD_RE = re.compile(r'\*\*(.+?)\*\*', re.S)
IMAGE_RE = re.compile(r'!\[(.*?)\]\((.*?)\)')
TABLE_ROW_RE = re.compile(r'^\s*\|.*\|\s*$')


def _is_table_separator(line: str) -> bool:
    cells = [c.strip() for c in line.strip().strip('|').split('|')]
    return bool(cells) and all(set(c) <= {':', '-', ' '} for c in cells)


def _strip_markdown(text: str) -> str:
    """清洗裸露 Markdown 标记，返回适合富文本编辑器的纯文本。"""
    # **粗体** → 粗体内容（去掉标记，保留文字）
    text = BOLD_RE.sub(r'\1', text)
    # __粗体__
    text = re.sub(r'__(.+?)__', r'\1', text, flags=re.S)
    # *斜体* / _斜体_
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text, flags=re.S)
    # `行内代码`
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # ~~删除线~~
    text = re.sub(r'~~(.+?)~~', r'\1', text, flags=re.S)
    # 裸露的行首引用符、标题符与列表短横线
    lines = []
    for line in text.split('\n'):
        line = QUOTE_RE.sub('', line)
        line = HEADING_RE.sub('', line)
        line = LIST_RE.sub('', line)
        lines.append(line)
    return '\n'.join(lines)


def _strip_trailing_tags(text: str) -> str:
    """剥离文末的裸标签行（如「设计变更 #施工图纸 #工程人 ...」），它属于元数据而非正文。"""
    lines = text.rstrip().splitlines()
    if lines:
        last = lines[-1].strip()
        if last.count('#') >= 3 and len(last) <= 80 and last[-1] not in '。！？.!?':
            lines = lines[:-1]
    return '\n'.join(lines) + '\n'


def _split_body_and_meta(raw: str) -> str:
    """按「### 正文内容」分隔符剥离前面的元数据说明行。"""
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if BODY_SEPARATOR_RE.match(line.strip()):
            return _strip_trailing_tags('\n'.join(lines[i + 1:]))
    # 没有分隔符时，退回「首个一级标题之后」作为正文起点
    for i, line in enumerate(lines):
        if line.strip().startswith('# '):
            rest = lines[i + 1:]
            # 跳过标题后的引用块（作者/分类/标签）与分隔线
            j = 0
            while j < len(rest):
                s = rest[j].strip()
                if not s or s == '---' or s.startswith('>') or s.startswith('!['):
                    j += 1
                    continue
                break
            return _strip_trailing_tags('\n'.join(rest[j:]))
    return _strip_trailing_tags(raw)


def clean_markdown_body(md_path: Path | str) -> str:
    """返回清洗后的正文纯文本（不含图片标记与元数据说明行）。"""
    raw = Path(md_path).read_text(encoding='utf-8')
    body = _split_body_and_meta(raw)
    out: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            out.append('')
            continue
        if s == '---':
            continue
        if IMAGE_RE.search(s):
            continue
        if TABLE_ROW_RE.match(s):
            if _is_table_separator(s):
                continue
            cells = [c.strip() for c in s.strip('|').split('|')]
            if len(cells) >= 2:
                out.append('• ' + '：'.join(c for c in cells if c))
            continue
        out.append(_strip_markdown(s))
    text = '\n'.join(out)
    # 折叠多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_body_blocks(
    md_path: Path | str,
    inline_images: list[Path | str] | None = None,
    skip_cover: bool = True,
) -> list[tuple[str, str | Path]]:
    """把母本 Markdown 解析成 ('text', 正文块) / ('image', 图片路径) 序列。

    - 以「### 正文内容」分隔符剥离元数据说明行；
    - 仅跳过 01_* 封面图，02_*/03_* 内文图按「第一段后、第二段后」插入；
    - 彻底清洗裸露 Markdown 标记。
    """
    md_path = Path(md_path)
    inline_images = [Path(p) for p in (inline_images or [])]
    raw = md_path.read_text(encoding='utf-8')
    body = _split_body_and_meta(raw)

    elements: list[tuple[str, str | Path]] = []
    current: list[str] = []
    pending_heading: str | None = None
    last_is_list = False
    md_inline_seen = 0  # 仅统计「非封面」的内文图，避免封面占用 inline_images 索引

    def flush_text():
        nonlocal current, last_is_list
        if current:
            text = '\n\n'.join(current).strip()
            if text:
                elements.append(('text', text))
            current = []
        last_is_list = False

    for line in body.splitlines():
        s = line.strip()
        if not s:
            flush_text()
            continue
        if s == '---':
            flush_text()
            continue

        img_match = IMAGE_RE.search(s)
        if img_match:
            flush_text()
            target = img_match.group(2)
            name = Path(target).name
            # 仅跳过封面图（01_*），其余内文图照常处理
            if skip_cover and name.startswith('01_'):
                continue
            md_inline_seen += 1
            # inline_images 已按序提供时优先用它，避免与 md 内的图重复插入
            if inline_images:
                continue
            abs_path = (md_path.parent / target).resolve()
            if abs_path.exists():
                elements.append(('image', abs_path))
            continue

        if TABLE_ROW_RE.match(s):
            if _is_table_separator(s):
                continue
            cells = [c.strip() for c in s.strip('|').split('|')]
            if len(cells) >= 2:
                if pending_heading:
                    current.append(pending_heading)
                    pending_heading = None
                current.append('• ' + '：'.join(c for c in cells if c))
            continue

        # 列表项：去掉短横线/序号，同一列表的多项用单换行紧凑拼接（避免编辑器里出现大段空行）
        if LIST_RE.match(s):
            if pending_heading:
                current.append(pending_heading)
                pending_heading = None
            item = _strip_markdown(s)
            if last_is_list and current:
                current[-1] = current[-1] + '\n' + item
            else:
                current.append(item)
            last_is_list = True
            continue

        # 标题单独成块会打断「段落」语义，标题与其后正文合并为一个段落块
        if HEADING_RE.match(s):
            flush_text()
            pending_heading = _strip_markdown(s)
            continue

        if pending_heading:
            current.append(pending_heading)
            pending_heading = None
        current.append(_strip_markdown(s))
        last_is_list = False
    flush_text()

    # 插入内文配图：第一张放在第一个文本块后，第二张放在第二个文本块后
    if inline_images:
        text_positions = [i for i, e in enumerate(elements) if e[0] == 'text']
        insertions: list[tuple[int, Path]] = []
        for n, img_path in enumerate(inline_images[:2]):
            if n < len(text_positions):
                # 文本块位置需按已插入的图片数偏移
                pos = text_positions[n] + 1 + n
                insertions.append((pos, img_path))
        for pos, img_path in reversed(insertions):
            elements.insert(pos, ('image', img_path))

    return elements
