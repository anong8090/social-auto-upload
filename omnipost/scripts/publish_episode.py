#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一稿五投统一发布入口。

替代根目录下 20+ 个 `_publish_*_epNN.py` 一次性脚本。能力：
- 按素材目录自动发现母本 A/B 与 6 张配图；
- 发布前预检（封禁平台、账号、cookie 文件），从可用池随机抽平台，并固定追加 2 个贴吧；
- 每平台独立执行，单个失败不影响其余；
- 成功/失败都写入 publish_history（含期数、选题、公开链接、贴吧吧名）；
- 按「期数 + 平台」幂等；贴吧按「期数 + 吧名」幂等，已成功发布过的默认跳过；
- 今日头条不进自动选台。显式指定时默认只代填，不自动存草稿、不点发布。

用法示例：
    python scripts/publish_episode.py --episode 15 --seed 20261105
    python scripts/publish_episode.py --episode 15 --platforms 5,7,9,11 --dry-run
    python scripts/publish_episode.py --episode 15 --dir "/path/to/15_xxx_一稿五投" --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from conf import BASE_DIR  # noqa: E402
from myUtils import preflight, publish_record  # noqa: E402
from myUtils.link_collector import collect_sync, _item_id_from_url  # noqa: E402
import preflight_notify  # noqa: E402
import failure_classify  # noqa: E402

DEFAULT_MATERIAL_ROOT = OMNIPOST_DIR.parent / "河狸云蓝枢_自媒体物料包"
DB_PATH = Path(BASE_DIR) / "db" / "database.db"
RUN_LOG_DIR = OMNIPOST_DIR.parent / "logs"
COMPLIANCE_NOTIFY_DIR = RUN_LOG_DIR / "compliance_notify"

# 长文平台用母本 B，动态平台用母本 A
LONG_FORM_PLATFORMS = (5, 7, 8, 9, 12, 13, 14, 15)
SHORT_FORM_PLATFORMS = (10, 11)

# 禁止自动化操作浏览器的平台：只产出「待发内容包」，由人工到后台发布。
# 头条不进这个名单：它仍会打开可见编辑页，但默认只代填，不自动存草稿。
MANUAL_PACKAGE_PLATFORMS = ()

# auto 选台只从「发布链路已实测打通、且能采集公开链接」的平台池中抽取。
# 12 微信公众号、13 网易号、14 大鱼号的登录/发布链路尚未完成 E2E 验证。
# 7 今日头条：2026-11-09 起 save=0 被账号级 7050 拒绝，退出自动选台。
#   显式 --platforms 7 默认走代填（可见窗口填好，不发保存请求、不点发布）。
#   只有 --toutiao-save-draft 才恢复 save=0，且不能与 --publish 同用。
# 8 搜狐号于 2026-11-10 被平台警告，随机选台暂停使用。
# 10 贴吧不占随机名额：每天固定追加 2 个吧，见 FIXED_TIEBA_BARS。
AUTO_PUBLISHABLE_PLATFORMS = (5, 9, 11, 15)
TOUTIAO_PLATFORM = 7
TIEBA_PLATFORM = 10
FIXED_TIEBA_BARS = ("建筑", "土木工程")
TIEBA_FALLBACK_BAR = "市政工程"

# 微信公众号只承接个人 IP 独立线（publish_ai_share.py），2026-11-12 用户明确
# 「不要发蓝枢的内容」。主线显式 --platforms 12 也必须硬拦截，不能靠选台遗漏规避。
MAINLINE_FORBIDDEN_PLATFORMS = {
    12: "微信公众号仅用于个人 IP 独立线，禁止发布蓝枢主线内容",
}

# 2026-11-18 第23期今日头条重复发布复盘：
# 头条没有可靠的服务端草稿回执，自动保存失败/草稿箱未命中时，内容仍可能已由
# 可见窗口里的人工操作发布；而 --force 又会跳过唯一的「已发布」幂等检查。
# 因此头条不能用普通 --force 重投，必须按最保守口径拦截任意尝试记录。
REPUBLISH_FORBIDDEN_PLATFORMS = (7,)


# ---------------------------------------------------------------- 素材发现


def find_episode_dir(material_root: Path, episode: str) -> Path:
    """在文案合集下按期数前缀定位目录，如 `14_xxx_一稿五投`。"""
    body_dir = material_root / "文案合集"
    matches = sorted(body_dir.glob(f"{episode}_*"))
    if not matches:
        raise FileNotFoundError(f"未找到第 {episode} 期文案目录: {body_dir}/{episode}_*")
    return matches[0]


def find_image_dir(material_root: Path, episode: str, topic: str = "") -> Optional[Path]:
    img_root = material_root / "图片素材"
    candidates = sorted(img_root.glob(f"{episode}_*"))
    if candidates:
        return candidates[0]
    if topic:
        candidates = sorted(img_root.glob(f"*{topic}*"))
        if candidates:
            return candidates[0]
    return None


def _read_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+)$")
_RULE_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_IMAGE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _strip_inline_markdown(text: str) -> str:
    """把行内 Markdown 标记转成适合富文本 editor 的纯文本。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)\*([^*\n]+?)\*(?!\w)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.replace("**", "").replace("__", "").strip()


def _table_cells(line: str) -> List[str]:
    """解析 Markdown 表格行；非表格行返回空列表。"""
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


def _extract_clean_body(text: str) -> str:
    """把母本 Markdown 转成可直接粘贴到平台富文本 editor 的纯正文。

    平台 editor 不会渲染 Markdown；若保留 `#`、`**`、`>`、图片引用或表格竖线，
    这些源码会原样发布出去。这里统一删除母本标题/元数据，并把二级标题、
    加粗、表格转成自然语言。
    """
    lines = text.replace("\r\n", "\n").splitlines()
    out: List[str] = []
    i = 0
    seen_first_heading = False

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        if not stripped:
            if out and out[-1]:
                out.append("")
            i += 1
            continue
        if _FENCE_RE.match(stripped):
            fence = stripped[:3]
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                i += 1
            i += 1
            continue
        if stripped.startswith(">") or _IMAGE_RE.match(stripped) or _RULE_RE.match(stripped):
            i += 1
            continue
        if _is_table_separator(stripped):
            i += 1
            continue

        heading = _HEADING_RE.match(raw)
        if heading:
            # 第一行 H1 是文章标题，由发布器单独填入，不再重复进正文。
            if not seen_first_heading:
                seen_first_heading = True
                i += 1
                continue
            heading_text = _strip_inline_markdown(heading.group(2))
            if heading_text in {"正文内容", "正文", "内容"}:
                i += 1
                continue
            if out and out[-1]:
                out.append("")
            out.append(heading_text)
            out.append("")
            i += 1
            continue

        cells = _table_cells(raw)
        if len(cells) >= 2 and i + 1 < len(lines) and _is_table_separator(lines[i + 1]):
            headers = [_strip_inline_markdown(cell) for cell in cells]
            i += 2
            while i < len(lines):
                row = _table_cells(lines[i])
                if len(row) < 2 or not any(row):
                    break
                out.append("；".join(
                    f"{headers[idx] if idx < len(headers) else f'第{idx + 1}项'}：{_strip_inline_markdown(cell)}"
                    for idx, cell in enumerate(row)
                ))
                i += 1
            continue

        out.append(_strip_inline_markdown(raw.rstrip()))
        i += 1

    cleaned: List[str] = []
    for line in out:
        if line or (cleaned and cleaned[-1]):
            cleaned.append(line)
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned)


def _body_with_inline_markers(raw_text: str, image_count: int) -> str:
    """把母本图片引用恢复成可见插图位置标记，其余仍走统一清洗规则。

    标记只用于上传器定位插图；上传器会在发布前把它们替换为真实图片块，
    并校验正文中不再残留标记，避免把 `[[插图N]]` 发布到线上。
    """
    lines = raw_text.replace("\r\n", "\n").splitlines()
    expanded: List[str] = []
    image_index = 0
    for line in lines:
        if _IMAGE_RE.match(line.strip()) and image_index < image_count:
            image_index += 1
            expanded.append(f"[[插图{image_index}]]")
        else:
            expanded.append(line)
    return _extract_clean_body("\n".join(expanded))


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


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def load_assets(episode: str, material_root: Path, explicit_dir: Optional[Path]) -> Dict[str, Any]:
    """汇总标题、母本 A/B、封面与配图路径。"""
    episode_dir = explicit_dir or find_episode_dir(material_root, episode)
    md_files = sorted(episode_dir.glob("*.md"))
    long_md = next((p for p in md_files if "母本B" in p.name or "长文" in p.name), None)
    short_md = next((p for p in md_files if "母本A" in p.name or "短图文" in p.name), None)
    if not long_md:
        long_md = next((p for p in md_files if p != short_md), None)
    if not short_md:
        short_md = long_md

    long_raw = _read_markdown(long_md) if long_md else ""
    short_raw = _read_markdown(short_md) if short_md else long_raw
    # 平台专属长文：`母本B_<平台type>_<平台名>.md`，用于保证 5 篇内容互不重复
    title_by_platform: Dict[int, str] = {}
    platform_raw: Dict[int, str] = {}
    for path in md_files:
        match = re.match(r"母本B_(\d+)_", path.name)
        if not match:
            continue
        ptype = int(match.group(1))
        raw = _read_markdown(path)
        platform_raw[ptype] = raw
        heading = _first_heading(raw)
        if heading:
            title_by_platform[ptype] = heading

    topic = title_by_platform.get(5) or _first_heading(long_raw) or episode_dir.name

    image_dir = find_image_dir(material_root, episode, topic)
    cover = inline = None
    gallery: List[Path] = []
    if image_dir:
        pngs = sorted(image_dir.glob("*.png"))
        cover = next((p for p in pngs if "01_" in p.name and "16x9" in p.name), None)
        if not cover:
            cover = next((p for p in pngs if "16x9" in p.name), pngs[0] if pngs else None)
        inline_files = [p for p in pngs if "16x9" in p.name and p != cover][:2]
        inline = inline_files
        gallery = [p for p in pngs if "16x9" in p.name][:3]
        if not gallery and cover:
            gallery = [cover]

    inline_count = len(inline or [])
    # 知乎、企鹅号、大鱼号上传器已支持按标记把图片精确插到母本指定位置；
    # 其余平台仍沿用原正文（图片追加到末尾），避免把位置标记发到线上。
    inline_marker_platforms = (9, 14, 15)
    # 专属母本只对部分平台存在；支持精确插图的平台即使回退到通用母本，
    # 也必须保留 [[插图N]]，否则上传器会静默写出 0 张内文图的草稿。
    long_body_with_markers = _body_with_inline_markers(long_raw, inline_count)
    long_body_by_platform: Dict[int, str] = {
        ptype: (
            _body_with_inline_markers(raw, inline_count)
            if ptype in inline_marker_platforms
            else _extract_clean_body(raw)
        )
        for ptype, raw in platform_raw.items()
    }
    long_body = _extract_clean_body(long_raw)
    # 让调用方按平台取正文时，回退值也带正确插图位置，避免误用无标记通用母本。
    for ptype in inline_marker_platforms:
        long_body_by_platform.setdefault(ptype, long_body_with_markers)
    short_body = _extract_clean_body(short_raw)

    return {
        "episode_dir": str(episode_dir),
        "topic": topic,
        "long_body": long_body,
        "long_body_by_platform": long_body_by_platform,
        "title_by_platform": title_by_platform,
        "short_body": short_body,
        "short_title": _first_heading(short_raw) or topic,
        "cover": str(cover) if cover else "",
        "inline": [str(p) for p in (inline or [])],
        "gallery": [str(p) for p in gallery],
        "image_dir": str(image_dir) if image_dir else "",
    }


# ---------------------------------------------------------------- 合规闸门（复用独立模块）


from scripts.compliance_check import compliance_check, ComplianceRuleError

# ---------------------------------------------------------------- 发布分发


def _dispatch(platform_type: int, assets: Dict[str, Any], title: str, body: str,
              account_list: List[str], *, dry_run: bool, is_draft: bool, tieba_kw: str = "",
              patch_existing_dayu_draft: bool = False, toutiao_prepare_only: bool = False) -> None:
    """调用对应平台的 post_article_*。导入放在函数内，避免 CLI 启动即加载全部浏览器依赖。"""
    from myUtils.postVideo import (
        post_article_baijiahao,
        post_article_dayu,
        post_article_qiehao,
        post_article_sohu,
        post_article_tieba,
        post_article_toutiao,
        post_article_wangyi,
        post_article_wechat_mp,
        post_article_weibo,
        post_article_zhihu,
    )

    tags: List[str] = []
    cover = assets.get("cover") or ""
    inline = assets.get("inline") or []
    gallery = assets.get("gallery") or []
    common = dict(
        enableTimer=False,
        videos_per_day=1,
        daily_times=["10:00"],
        start_days=0,
        dry_run=dry_run,
        cover_path=cover,
    )

    if platform_type == 5:
        return post_article_baijiahao(
            title, body, tags, account_list, ai_generated=True,
            inline_image_paths=inline, is_draft=is_draft, **common,
        )
    elif platform_type == 7:
        # 默认 prepare_only=True：可见窗口代填，不发 save=0、不点发布。
        # 只有调用方显式关掉 prepare_only 且 is_draft=True，才会走 save=0。
        return post_article_toutiao(
            title, body, tags, account_list,
            work_statements=["引用AI"],
            inline_image_paths=inline,
            is_draft=is_draft,
            prepare_only=toutiao_prepare_only,
            **common,
        )
    elif platform_type == 8:
        # 搜狐号发文页只有一个「必选声明」单选组，选项真实文案是「含有AI生成内容」。
        # 传旧文案「包含AI创作内容」选不中，AI 声明会静默缺失。
        return post_article_sohu(
            title, body, tags, account_list,
            cover_paths=[cover] if cover else [],
            inline_image_paths=inline,
            info_source="含有AI生成内容",
            is_draft=is_draft,
            **common,
        )
    elif platform_type == 9:
        return post_article_zhihu(
            title, body, tags, account_list,
            creation_statement="包含 AI 辅助创作",
            inline_image_paths=inline, is_draft=is_draft, **common,
        )
    elif platform_type == 10:
        return post_article_tieba(
            title, body, tags, account_list,
            kw=tieba_kw or None, is_draft=is_draft,
            **{k: v for k, v in common.items() if k != "cover_path"},
            cover_path=cover,
        )
    elif platform_type == 11:
        return post_article_weibo(
            title, body, tags, account_list,
            is_draft=is_draft, file_list=gallery,
            **common,
        )
    elif platform_type == 12:
        return post_article_wechat_mp(
            title, body, tags, account_list,
            is_draft=is_draft, file_list=inline, **common,
        )
    elif platform_type == 13:
        return post_article_wangyi(title, body, tags, account_list, is_draft=is_draft, file_list=inline, **common)
    elif platform_type == 14:
        existing_draft_url = ""
        if patch_existing_dayu_draft:
            from uploader.dayu_uploader.main import find_draft_edit_url
            existing_draft_url = find_draft_edit_url(
                account_list, title, headless=True
            )
            if not existing_draft_url:
                raise RuntimeError(f"大鱼号草稿箱未找到本期标题，无法修补既有草稿: {title}")
        return post_article_dayu(
            title, body, tags, account_list, is_draft=is_draft, file_list=inline,
            existing_draft_url=existing_draft_url,
            patch_existing_draft=patch_existing_dayu_draft, **common,
        )
    elif platform_type == 15:
        return post_article_qiehao(title, body, tags, account_list, is_draft=is_draft, file_list=inline, **common)
    else:
        raise ValueError(f"暂不支持的平台类型: {platform_type}")


# ---------------------------------------------------------------- 本地发布记录


def sync_published_draft(
    episode: str,
    ptype: int,
    platform_name: str,
    platform_title: str,
    body: str,
    files: List[str],
    account_name: str,
    url: str,
    status: str = "published",
    note: str = "",
) -> None:
    """把成功发布（或已存草稿）的一条记录同步到 publish_drafts，供 OmniPost 前端核对。

    按「期数备注 + 平台 + 标题」幂等；已有记录只更新状态、链接与文件，
    不重复插入。该表是前端发布记录视图，publish_history 才是审计主表。
    """
    account = str(account_name or "默认账号")
    default_note = f"第{episode}期 {platform_name} 已真实发布：{url}"
    completion_note = note or default_note
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id FROM publish_drafts
               WHERE platform_type = ? AND title = ? AND completion_note LIKE ?
               ORDER BY id DESC LIMIT 1""",
            (ptype, platform_title, f"第{episode}期 {platform_name}%"),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE publish_drafts
                   SET platform_name = ?, body = ?, status = ?,
                       ai_generated = 1, dry_run = 0, completion_note = ?,
                       synced_platforms = ?, updated_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (
                    platform_name,
                    body,
                    status,
                    completion_note,
                    json.dumps([platform_name], ensure_ascii=False),
                    row[0],
                ),
            )
        else:
            cur.execute(
                """INSERT INTO publish_drafts
                   (title, platform_type, platform_name, content_type, body, tags,
                    files_json, account_ids_json, ai_generated, dry_run,
                    status, completion_note, synced_platforms, created_at, updated_at)
                   VALUES (?, ?, ?, 'article', ?, '', ?, ?, 1, 0,
                           ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))""",
                (
                    platform_title,
                    ptype,
                    platform_name,
                    body,
                    json.dumps(files, ensure_ascii=False),
                    json.dumps([account], ensure_ascii=False),
                    status,
                    completion_note,
                    json.dumps([platform_name], ensure_ascii=False),
                ),
            )
        conn.commit()


# ---------------------------------------------------------------- 待发内容包


def _safe_name(text: str, limit: int = 60) -> str:
    """把标题转成跨平台安全的目录名（含全角标点，避免在 Windows 上失败）。"""
    cleaned = re.sub(r'[\\/:*?"<>|？：＊＂＜＞｜／\r\n\t]', "_", (text or "").strip())
    return cleaned[:limit].strip(" .") or "未命名"


def export_manual_package(
    material_root: Path,
    episode: str,
    topic: str,
    platform_name: str,
    title: str,
    body: str,
    cover: str,
    inline: List[str],
) -> Path:
    """为禁止自动发布的平台产出一份可直接照抄的待发内容包，绝不启动浏览器。

    目录结构：`<物料包>/待发布/<期数>_<选题>/<平台>/`
      - `标题.txt`：平台标题（已按平台红线校验过）
      - `正文.md`：可直接粘贴的纯正文（母本 Markdown 语法已清洗）
      - `01_封面.*` / `02_内文图1.*` …：按插入顺序编号的配图副本
      - `发布说明.md`：上传顺序、AI 声明勾选、发布后回填要求
    """
    out_dir = material_root / "待发布" / f"{episode}_{_safe_name(topic)}" / _safe_name(platform_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "标题.txt").write_text(title + "\n", encoding="utf-8")
    (out_dir / "正文.md").write_text(body.strip() + "\n", encoding="utf-8")

    image_lines: List[str] = []
    if cover and Path(cover).is_file():
        target = out_dir / f"01_封面{Path(cover).suffix}"
        shutil.copy2(cover, target)
        image_lines.append(f"1. `{target.name}` → 文章封面 / 头图（16:9）")
    for idx, path in enumerate(inline, 1):
        src = Path(path)
        if not src.is_file():
            continue
        target = out_dir / f"0{idx + 1}_内文图{idx}{src.suffix}"
        shutil.copy2(src, target)
        image_lines.append(
            f"{idx + 1}. `{target.name}` → 正文第 {idx} 张插图（插在第 {idx} 个二级小标题后的段落末）"
        )

    body_chars = len(re.sub(r"\s", "", body or ""))
    note = "\n".join([
        f"# {platform_name} 待发内容包（第 {episode} 期）",
        "",
        f"- 选题：{topic}",
        f"- 标题（{len(title)} 字）：{title}",
        f"- 正文字数：{body_chars} 字",
        "",
        "## 为什么是内容包而不是自动发布",
        "",
        "该平台已列入「禁止自动化发布」名单。自动化写入后台存在误发、误判营销号的风险，",
        "因此本工具只把内容整理成可直接照抄的文件，发布动作由人工完成。",
        "",
        "## 发布步骤",
        "",
        "1. 打开平台创作后台的图文发布页；",
        "2. 标题直接粘贴 `标题.txt`；",
        "3. 正文粘贴 `正文.md`（已是纯文本，无 Markdown 标记）；",
        "4. 按下面的顺序上传配图；",
        "5. 勾选平台要求的 AI 声明；",
        "6. 人工确认无误后点击发布。",
        "",
        "## 配图上传顺序",
        "",
        *(image_lines or ["（本期无可用配图）"]),
        "",
        "## 发布后回填",
        "",
        "- 把公开链接回填到飞书每日动作台账对应列；",
        "- 在本机 OmniPost 发布记录里把该条 `draft` 改成已发布并补链接。",
        "",
    ])
    (out_dir / "发布说明.md").write_text(note, encoding="utf-8")

    return out_dir


# ---------------------------------------------------------------- 主流程


def parse_tieba_bars(explicit: str) -> List[str]:
    """把 `--tieba-kw` 拆成吧名。支持英文逗号和中文逗号，去重并保持顺序。"""
    seen: List[str] = []
    for part in re.split(r"[,，]", explicit or ""):
        name = part.strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def default_tieba_bars(unavailable: Optional[Iterable[str]] = None) -> List[str]:
    """每天两个吧：优先建筑、土木工程，已被本期发布过的用市政工程补位。"""
    blocked = {str(name).strip() for name in (unavailable or ()) if str(name).strip()}
    chosen: List[str] = []
    for name in (*FIXED_TIEBA_BARS, TIEBA_FALLBACK_BAR):
        if name in blocked:
            continue
        chosen.append(name)
        if len(chosen) == 2:
            break
    return chosen


def resolve_jobs(args, episode: str) -> List[Dict[str, Any]]:
    """决定本轮要跑的发布任务。

    每个任务是 `{platform_type, tieba_kw}`。贴吧同一平台类型会拆成多条，
    每条一个吧，避免「期数 + 平台」幂等把第二个吧误判成重复发布。

    auto：随机池不含头条和贴吧，再追加两个贴吧。
    显式平台：按传入顺序；包含贴吧且没写 `--tieba-kw` 时，同样用每天的两个吧。
    """
    explicit_bars = parse_tieba_bars(getattr(args, "tieba_kw", "") or "")
    if args.platforms != "auto":
        requested = [int(x) for x in str(args.platforms).split(",") if x.strip()]
        forbidden = [p for p in requested if p in MAINLINE_FORBIDDEN_PLATFORMS]
        if forbidden:
            reasons = "；".join(MAINLINE_FORBIDDEN_PLATFORMS[p] for p in forbidden)
            raise SystemExit(f"❌ 主线平台硬拦截：{reasons}；请改用 publish_ai_share.py")
        requested = [p for p in requested if p not in preflight.BANNED_PLATFORMS]
        bars = explicit_bars or default_tieba_bars()
        jobs: List[Dict[str, Any]] = []
        for ptype in requested:
            if ptype == TIEBA_PLATFORM:
                for bar in bars:
                    jobs.append({"platform_type": ptype, "tieba_kw": bar})
            else:
                jobs.append({"platform_type": ptype, "tieba_kw": ""})
        return jobs

    named, unscoped = publish_record.tieba_published_state(DB_PATH, episode)
    picked = preflight.pick_random_platforms(
        DB_PATH, Path(BASE_DIR),
        count=args.count,
        pool=AUTO_PUBLISHABLE_PLATFORMS,
        exclude=MANUAL_PACKAGE_PLATFORMS,
        seed=args.seed,
    )
    # 随机池本身只有 4 个平台，默认 count=5 不应当成「账号没绑够」。
    # 只在池内账号未就绪、实际抽不满时提醒。
    expected = min(args.count, len(AUTO_PUBLISHABLE_PLATFORMS))
    if len(picked) < expected:
        print(
            f"⚠️ 随机池可用平台只有 {len(picked)} 个"
            f"（池内应有 {expected} 个，不含固定的 2 个贴吧）"
        )
    jobs = [{"platform_type": ptype, "tieba_kw": ""} for ptype in picked]
    if explicit_bars:
        bars = explicit_bars
    elif unscoped:
        # 仍放进任务列表，由主循环按「未记录吧名」跳过并写进结果，不静默丢掉。
        bars = list(FIXED_TIEBA_BARS)
        print("⚠️ 该期已有未记录吧名的贴吧成功发布，本轮贴吧任务会跳过，避免重复发帖")
    else:
        bars = default_tieba_bars(named)
        if len(bars) < 2:
            print(
                f"⚠️ 该期还能发的贴吧只剩 {len(bars)} 个"
                "（建筑 / 土木工程 / 市政工程中已发布的不再发）"
            )
    for bar in bars:
        jobs.append({"platform_type": TIEBA_PLATFORM, "tieba_kw": bar})
    return jobs


def _job_label(platform_name: str, ptype: int, tieba_kw: str) -> str:
    if ptype == TIEBA_PLATFORM and tieba_kw:
        return f"{platform_name}·{tieba_kw}"
    return platform_name


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="一稿五投统一发布入口")
    parser.add_argument("--episode", help="期数，如 15（--rule-check 时可省略）")
    parser.add_argument("--dir", help="直接指定该期文案目录（默认按 --episode 自动发现）")
    parser.add_argument("--base", default=str(DEFAULT_MATERIAL_ROOT), help="物料包根目录")
    parser.add_argument("--platforms", default="auto", help="auto 或逗号分隔的平台类型，如 5,7,9,11")
    parser.add_argument("--count", type=int, default=5, help="auto 模式下随机抽取几个平台（不含固定的 2 个贴吧）")
    parser.add_argument("--seed", type=int, help="随机种子，便于复现")
    parser.add_argument("--title", help="覆盖自动解析出的标题")
    parser.add_argument("--dry-run", action="store_true", help="只填表不提交（会打开平台编辑页，慎用）")
    parser.add_argument(
        "--check-only", action="store_true",
        help="完全离线预检：只做素材发现与合规检查，不启动浏览器、不写数据库、不发布",
    )
    parser.add_argument(
        "--publish",
        dest="draft",
        action="store_false",
        help="真实发布（默认只存草稿，不点「发布」）",
    )
    parser.add_argument(
        "--draft",
        dest="draft",
        action="store_true",
        help="只存草稿（默认行为，保留此参数仅为兼容旧调用）",
    )
    parser.set_defaults(draft=True)
    parser.add_argument("--verify-cookie", action="store_true", help="发布前真正开浏览器校验 cookie（慢）")
    parser.add_argument("--force", action="store_true", help="忽略已发布幂等检查；对今日头条仍按任意尝试记录拦截")
    parser.add_argument(
        "--force-retry-failed", action="store_true",
        help="仅今日头条：显式重试已失败且确认未发布的同篇内容；已发布记录仍硬拦截",
    )
    parser.add_argument(
        "--tieba-kw", default="",
        help="贴吧吧名。省略时发「建筑」和「土木工程」；多个吧用逗号分隔，写了就只发这些吧",
    )
    parser.add_argument(
        "--patch-existing-dayu-draft", action="store_true",
        help="修补大鱼号本篇既有草稿：打开草稿编辑页补齐内文图/封面并保存，不新建重复草稿",
    )
    parser.add_argument(
        "--toutiao-prepare-only", action="store_true",
        help="今日头条代填（默认已是这个行为）：可见浏览器填好后由人工发布，不发保存请求、不点发布",
    )
    parser.add_argument(
        "--toutiao-save-draft", action="store_true",
        help="显式恢复头条 save=0 自动存草稿。账号仍可能被平台拒绝；不能与 --publish 或 --toutiao-prepare-only 同用",
    )
    parser.add_argument("--rule-check", action="store_true", help="仅校验合规规则是否可信，不加载素材/不发布")
    args = parser.parse_args(argv)

    if args.toutiao_save_draft and args.toutiao_prepare_only:
        print("❌ --toutiao-save-draft 与 --toutiao-prepare-only 不能同时使用", file=sys.stderr)
        return 2

    if args.toutiao_save_draft and not args.draft:
        print("❌ --toutiao-save-draft 只恢复 save=0 存草稿，不能与 --publish 同时使用", file=sys.stderr)
        return 2

    if args.toutiao_prepare_only and not args.draft:
        print("❌ --toutiao-prepare-only 不能与 --publish 同时使用（代填模式绝不点发布）", file=sys.stderr)
        return 2

    if args.force_retry_failed and args.force:
        print("❌ --force-retry-failed 不能与 --force 同时使用", file=sys.stderr)
        return 2

    if args.rule_check:
        import compliance_check as rule_module
        if rule_module._RULE_LOAD_ERROR:
            print(f"❌ 合规规则不可信: {rule_module._RULE_LOAD_ERROR}", file=sys.stderr)
            return 2
        print("✅ 合规规则装载正常（静态词表 + 动态白名单候选）")
        return 0

    if not args.episode:
        print("❌ 缺少 --episode（仅 --rule-check 可省略）")
        return 2

    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}")
        return 2

    try:
        assets = load_assets(args.episode, Path(args.base), Path(args.dir) if args.dir else None)
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        return 2

    title = args.title or assets["topic"]
    if not title:
        print("❌ 未能解析出标题，请用 --title 显式指定")
        return 2

    jobs = resolve_jobs(args, args.episode)
    platforms: List[int] = []
    for job in jobs:
        if job["platform_type"] not in platforms:
            platforms.append(job["platform_type"])
    if not jobs:
        print("❌ 没有可用平台")
        return 2

    print(f"📦 第 {args.episode} 期 | 标题: {title}")
    print(f"🖼  封面: {assets['cover'] or '无'} | 内文图: {len(assets['inline'])} 张")
    print("🎯 本轮: " + "、".join(
        _job_label(
            str(preflight.PLATFORM_NAMES.get(job["platform_type"], job["platform_type"])),
            job["platform_type"],
            job["tieba_kw"],
        )
        for job in jobs
    ))

    # 离线预检：只做合规判定，不启动浏览器、不写数据库、不碰平台编辑页。
    # 「只是做测试」请用这个模式，不要用 --dry-run（后者会真的打开平台编辑页并填表）。
    if args.check_only:
        blocked = 0
        for job in jobs:
            ptype = job["platform_type"]
            pname = _job_label(
                str(preflight.PLATFORM_NAMES.get(ptype, ptype)),
                ptype,
                job["tieba_kw"],
            )
            body = (
                assets["long_body_by_platform"].get(ptype, assets["long_body"])
                if ptype in LONG_FORM_PLATFORMS
                else assets["short_body"]
            )
            platform_title = (
                assets["short_title"]
                if ptype in SHORT_FORM_PLATFORMS
                else assets["title_by_platform"].get(ptype, title)
            )
            try:
                result = compliance_check(
                    platform_title, body, ptype,
                    has_cover=bool(assets["cover"]),
                    inline_count=len(assets["inline"]),
                )
            except ComplianceRuleError as exc:
                print(f"❌ 合规规则不可信，终止离线预检: {exc}", file=sys.stderr)
                return 2
            if result["passed"]:
                print(f"  ✅ {pname} 合规通过（标题 {len(platform_title)} 字）")
            else:
                blocked += 1
                print(f"  ⛔ {pname} 合规拦截: {'; '.join(result['problems'])}")
            for warning in result["warnings"]:
                print(f"  ⚠️ {pname} 合规告警: {warning}")
        print("\n🧪 离线预检完成：未启动浏览器、未写数据库、未发布")
        return 0 if blocked == 0 else 1

    # 只有真正要写发布记录时才补 schema；离线预检不碰数据库。
    publish_record.ensure_publish_history_schema(DB_PATH)

    # 预检（含封禁、账号、cookie 文件）
    checks = asyncio_run_preflight(platforms, args.verify_cookie)
    for c in checks:
        icon = "✅" if c["ok"] else "❌"
        print(f"  {icon} {c['platform_name']}: {c['reason']}")

    results: List[Dict[str, Any]] = []
    check_by_type = {c["platform_type"]: c for c in checks}
    for job in jobs:
        ptype = job["platform_type"]
        tieba_kw = str(job.get("tieba_kw") or "")
        check = check_by_type.get(ptype)
        if check is None:
            results.append({
                "platform": str(ptype), "type": ptype, "status": "skipped",
                "tieba_kw": tieba_kw, "reason": "预检没有返回该平台",
            })
            continue
        pname = _job_label(check["platform_name"], ptype, tieba_kw)

        # 待发内容包平台不需要 cookie（不碰浏览器），预检失败也不影响出包
        if not check["ok"] and ptype not in MANUAL_PACKAGE_PLATFORMS:
            results.append({
                "platform": pname, "type": ptype, "status": "skipped",
                "tieba_kw": tieba_kw, "reason": check["reason"],
            })
            continue

        existing = None
        if ptype in REPUBLISH_FORBIDDEN_PLATFORMS:
            # 头条这类平台即使上次记 failed/draft，也可能已由人工在窗口里发布；
            # 普通 --force 不再放行，必须显式 --force-retry-failed 才能重试失败。
            existing = publish_record.find_any_attempt_record(DB_PATH, args.episode, ptype)
            if existing:
                status = str(existing.get("status") or "").strip()
                if status == publish_record.STATUS_PUBLISHED:
                    results.append({
                        "platform": pname, "type": ptype, "status": "already_published",
                        "url": existing.get("published_url"),
                        "reason": "该期该平台已成功发布，禁止重复发布",
                    })
                    continue
                if not args.force_retry_failed:
                    results.append({
                        "platform": pname, "type": ptype, "status": "already_attempted",
                        "url": existing.get("published_url"),
                        "reason": (
                            f"该期该平台已有{status}记录（id={existing.get('id')}），"
                            "可能已由人工发布；禁止重复。确需重试失败请用 --force-retry-failed"
                        ),
                    })
                    continue
        elif ptype == TIEBA_PLATFORM and not args.force:
            # 贴吧按吧名幂等，不能按平台类型挡：同一期要发两个不同的吧。
            if not tieba_kw:
                results.append({
                    "platform": pname, "type": ptype, "status": "blocked",
                    "reason": "贴吧任务缺少吧名",
                })
                continue
            named_bars, unscoped_tieba = publish_record.tieba_published_state(DB_PATH, args.episode)
            if unscoped_tieba:
                results.append({
                    "platform": pname, "type": ptype, "status": "already_published",
                    "tieba_kw": tieba_kw,
                    "reason": "该期已有未记录吧名的贴吧成功发布，无法确认是哪个吧，禁止再发",
                })
                continue
            if tieba_kw in named_bars:
                results.append({
                    "platform": pname, "type": ptype, "status": "already_published",
                    "tieba_kw": tieba_kw,
                    "reason": f"该期贴吧「{tieba_kw}」已发布，禁止重复发帖",
                })
                continue
        elif not args.force:
            existing = publish_record.find_published_record(DB_PATH, args.episode, ptype)
            if existing:
                results.append({
                    "platform": pname, "type": ptype, "status": "already_published",
                    "url": existing.get("published_url"), "reason": "该期该平台已成功发布，跳过（--force 可覆盖）",
                })
                continue

        body = (
            assets["long_body_by_platform"].get(ptype, assets["long_body"])
            if ptype in LONG_FORM_PLATFORMS
            else assets["short_body"]
        )
        # 审计主表与 publish_drafts 只保存最终可读正文，不保存上传器内部插图标记。
        audit_body = re.sub(r"\[\[插图\d+\]\]", "", body)
        platform_title = (
            assets["short_title"]
            if ptype in SHORT_FORM_PLATFORMS
            else assets["title_by_platform"].get(ptype, title)
        )
        try:
            compliance_result = compliance_check(
                platform_title, body, ptype,
                has_cover=bool(assets["cover"]),
                inline_count=len(assets["inline"]),
            )
        except ComplianceRuleError as exc:
            # 规则本身不可信：整批终止，不逐平台尝试发布
            print(f"❌ 合规规则不可信，终止本轮发布: {exc}", file=sys.stderr)
            return 2
        problems = compliance_result["problems"]
        warnings = compliance_result["warnings"]
        if problems:
            reason = "; ".join(problems)
            results.append({"platform": pname, "type": ptype, "status": "blocked", "reason": reason})
            print(f"  ⛔ {pname} 合规拦截: {reason}")
            # 飞书告警（去重：同一期+平台+问题摘要只提醒一次）
            try:
                webhook, secret = preflight_notify.resolve_lark_settings(None, preflight_notify.CONFIG_PATH)
                if webhook:
                    webhook = preflight_notify.validate_webhook(webhook)
                    key = f"compliance:{args.episode}:{ptype}:{hashlib.md5(reason.encode()).hexdigest()[:8]}"
                    status, msg = preflight_notify.notify_once(
                        COMPLIANCE_NOTIFY_DIR, key, webhook, secret,
                        f"⛔ 第 {args.episode} 期 {pname} 合规拦截\n{reason}\n标题: {platform_title[:50]}",
                    )
                    if status == "sent":
                        print(f"  📣 已推送飞书告警")
                    elif status == "unknown":
                        print(f"  ⚠️ 飞书告警结果未知: {msg}")
            except Exception as exc:
                print(f"  ⚠️ 飞书告警发送异常: {preflight_notify.redact_error(exc, None, None)}")
            continue

        # 合规告警（不拦截，仅提示）
        if warnings:
            for w in warnings:
                print(f"  ⚠️ {pname} 合规告警: {w}")

        # 草稿优先：默认只存草稿、绝不点发布。
        # 例外平台走真实发布：
        #   知乎(9)——专栏编辑器是纯前端自动存草稿，草稿只存在本地浏览器，
        #            换浏览器/换 profile 就看不到，等于没存，只能直接发布；
        #   微博(11)、百度贴吧(10)——平台本身没有真草稿入口，
        #            草稿模式只能做到「填好但不发布」，无实质意义。
        # 头条(7) 不在自动选台里。显式指定时默认代填：不发 save=0、不点发布。
        # 只有 --toutiao-save-draft 才恢复自动存草稿。
        DIRECT_PUBLISH_PLATFORMS = (9, 11, 10)
        draft_only = bool(args.draft) or ptype in MANUAL_PACKAGE_PLATFORMS
        if args.draft and ptype in DIRECT_PUBLISH_PLATFORMS:
            draft_only = False
            print(
                f"  ℹ️ {pname} 不支持平台侧草稿（草稿仅存本地浏览器/无草稿入口），"
                f"本次按真实发布执行"
            )
        use_prepare = ptype == TOUTIAO_PLATFORM and not args.toutiao_save_draft
        if use_prepare and not args.draft:
            print(f"  ℹ️ {pname} 不随 --publish 自动投稿，改为代填（不发保存请求、不点发布）")
        if use_prepare or args.dry_run:
            opening_status = publish_record.STATUS_PREVIEW
        elif draft_only:
            opening_status = publish_record.STATUS_DRAFT
        else:
            opening_status = publish_record.STATUS_PUBLISHING

        history_id = publish_record.create_record(
            DB_PATH,
            platform_type=ptype,
            platform_name=pname,
            account_name=str(check.get("account") or "默认账号"),
            content_type="article",
            title=platform_title,
            content=audit_body,
            files=assets["gallery"],
            status=opening_status,
            episode=args.episode,
            topic=assets["topic"],
            tieba_kw=tieba_kw or None,
        )

        # 禁止自动发布的平台：只产出待发内容包，全程不启动浏览器
        if ptype in MANUAL_PACKAGE_PLATFORMS:
            if args.dry_run:
                publish_record.finish_record(DB_PATH, history_id, publish_record.STATUS_PREVIEW)
                results.append({"platform": pname, "type": ptype, "status": "preview"})
                print(f"  ✅ {pname} 预览模式：只校验合规，不出包")
                continue
            try:
                package_dir = export_manual_package(
                    Path(args.base), args.episode, assets["topic"], pname,
                    platform_title, body, assets["cover"], assets["inline"],
                )
            except Exception as exc:  # noqa: BLE001
                error_msg = f"待发内容包生成失败: {exc}"
                publish_record.finish_record(
                    DB_PATH, history_id, publish_record.STATUS_FAILED, error_msg=error_msg,
                )
                results.append({"platform": pname, "type": ptype, "status": "failed", "reason": error_msg})
                print(f"  ❌ {pname} 待发内容包生成失败: {exc}")
                continue
            note = f"禁止自动发布，已生成待发内容包，请人工到{pname}后台发布: {package_dir}"
            publish_record.finish_record(
                DB_PATH, history_id, publish_record.STATUS_DRAFT, error_msg=note,
            )
            results.append({
                "platform": pname, "type": ptype, "status": "manual_package",
                "package": str(package_dir),
            })
            print(f"  📁 {pname} 已生成待发内容包（不自动发布）: {package_dir}")
            continue

        cookie = Path(str(check["cookie_path"]))
        account_file = [cookie.name]

        direct_url = ""
        try:
            direct_url = str(
                _dispatch(
                    ptype, assets, platform_title, body, account_file,
                    dry_run=args.dry_run,
                    is_draft=True if use_prepare else draft_only,
                    tieba_kw=tieba_kw,
                    patch_existing_dayu_draft=args.patch_existing_dayu_draft,
                    toutiao_prepare_only=use_prepare,
                ) or ""
            ).strip()
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            # 失败分类与记录
            code, _ = failure_classify.record_failure(
                DB_PATH, args.episode, ptype, pname, error_msg,
                history_id=history_id,
                failure_step="dispatch",
            )
            # PAGE_CHANGED 累计 2 次生成待修记录
            fix_path = failure_classify.maybe_create_pending_fix(
                args.episode, ptype, pname, "dispatch", error_msg
            )
            if fix_path:
                print(f"  📝 已生成待修记录: {fix_path.name}")
            publish_record.finish_record(
                DB_PATH, history_id, publish_record.STATUS_FAILED,
                error_msg=error_msg,
            )
            results.append({"platform": pname, "type": ptype, "status": "failed", "reason": error_msg, "failure_code": code})
            print(f"  ❌ {pname} 发布失败 [{code}]: {exc}")
            continue

        if use_prepare:
            note = "今日头条代填结束：未发送保存请求、未点击发布。请人工在编辑页发布后再补链接。"
            publish_record.finish_record(
                DB_PATH, history_id, publish_record.STATUS_PREVIEW, error_msg=note,
            )
            results.append({
                "platform": pname, "type": ptype, "status": "prepared", "reason": note,
            })
            print(f"  ⏸️ {pname} 代填结束（未存草稿、未发布）")
            continue

        if args.dry_run or draft_only:
            final_status = publish_record.STATUS_PREVIEW if args.dry_run else publish_record.STATUS_DRAFT
            publish_record.finish_record(DB_PATH, history_id, final_status)
            results.append({"platform": pname, "type": ptype, "status": final_status})
            hint = "预览" if args.dry_run else "存草稿"
            print(f"  ✅ {pname} 已{hint}")
            if draft_only and not args.dry_run:
                # 草稿同样入前端记录视图：状态记 draft，备注写明「待人工发布」
                try:
                    draft_files = (
                        [str(p) for p in sorted(Path(assets["image_dir"]).glob("*.png"))]
                        if assets.get("image_dir") and Path(assets["image_dir"]).is_dir()
                        else assets["gallery"]
                    )
                    sync_published_draft(
                        args.episode, ptype, pname, platform_title, audit_body,
                        draft_files, str(check.get("account") or ""), "",
                        status="draft",
                        note=(
                            f"第{args.episode}期 {pname} 已保存为平台草稿"
                            "（未点击发表），待人工到平台后台确认发布后再补链接"
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"  ⚠️ {pname} 草稿记录同步失败（主记录已写入）: {exc}")
            continue

        url, item_id = collect_sync(ptype, cookie, platform_title, args.episode)
        if not url and direct_url:
            # 发布器已拿到真实链接（如微博、贴吧 /p/ 帖子），优先采用，采集仅作兜底
            url, item_id = direct_url, _item_id_from_url(ptype, direct_url)
            print(f"  🔗 使用发布器返回链接: {url}")
        if not url:
            error_msg = "发布动作已完成，但未能采集到标题匹配的公开链接；请核查平台后台"
            code, _ = failure_classify.record_failure(
                DB_PATH, args.episode, ptype, pname, error_msg,
                history_id=history_id,
                failure_step="collect_link",
            )
            publish_record.finish_record(
                DB_PATH, history_id, publish_record.STATUS_FAILED,
                error_msg=error_msg,
            )
            results.append({
                "platform": pname, "type": ptype, "status": "failed",
                "reason": error_msg, "failure_code": code,
            })
            print(f"  ❌ {pname} 发布结果无法核验 [{code}]: 未采集到公开链接")
            continue

        publish_record.finish_record(
            DB_PATH, history_id, publish_record.STATUS_PUBLISHED,
            published_url=url, platform_item_id=item_id,
            screenshot_dir="logs/screenshots",
        )
        try:
            draft_files = (
                [str(p) for p in sorted(Path(assets["image_dir"]).glob("*.png"))]
                if assets.get("image_dir") and Path(assets["image_dir"]).is_dir()
                else assets["gallery"]
            )
            sync_published_draft(
                args.episode, ptype, pname, platform_title, audit_body,
                draft_files, str(check.get("account") or ""), url,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ {pname} 前端发布记录同步失败（主记录已成功）: {exc}")
        results.append({"platform": pname, "type": ptype, "status": "published", "url": url, "item_id": item_id})
        print(f"  ✅ {pname} 已发布: {url}")

    summary = {
        "episode": args.episode,
        "title": title,
        "platforms": platforms,
        "jobs": jobs,
        "results": results,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = RUN_LOG_DIR / f"episode_{args.episode}_publish_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    published = sum(1 for r in results if r["status"] == "published")
    drafted = sum(1 for r in results if r["status"] == "draft")
    packaged = sum(1 for r in results if r["status"] == "manual_package")
    prepared = sum(1 for r in results if r["status"] == "prepared")
    print(
        f"\n📊 自动发布 {published} 个平台，已存草稿 {drafted} 个平台，"
        f"头条代填 {prepared} 个，待发内容包 {packaged} 个，明细已写入 {out}"
    )
    return 0 if (published or drafted or packaged or prepared or args.dry_run) else 1


def asyncio_run_preflight(platforms: List[int], verify_cookie: bool) -> List[Dict[str, Any]]:
    import asyncio

    return asyncio.run(
        preflight.preflight(platforms, DB_PATH, Path(BASE_DIR), verify_cookie=verify_cookie)
    )


if __name__ == "__main__":
    raise SystemExit(main())
