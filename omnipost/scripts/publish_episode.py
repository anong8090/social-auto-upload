#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一稿五投统一发布入口。

替代根目录下 20+ 个 `_publish_*_epNN.py` 一次性脚本。能力：
- 按素材目录自动发现母本 A/B 与 6 张配图；
- 发布前预检（封禁平台、账号、cookie 文件），自动从可用池随机抽 5 台；
- 每平台独立执行，单个失败不影响其余；
- 成功/失败都写入 publish_history（含期数、选题、公开链接）；
- 按「期数 + 平台」幂等，已成功发布过的默认跳过。

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
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    long_body = _extract_clean_body(long_raw)
    short_body = _extract_clean_body(short_raw)

    # 平台专属长文：`母本B_<平台type>_<平台名>.md`，用于保证 5 篇内容互不重复
    long_body_by_platform: Dict[int, str] = {}
    title_by_platform: Dict[int, str] = {}
    for path in md_files:
        match = re.match(r"母本B_(\d+)_", path.name)
        if not match:
            continue
        ptype = int(match.group(1))
        raw = _read_markdown(path)
        long_body_by_platform[ptype] = _extract_clean_body(raw)
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


from scripts.compliance_check import compliance_check

# ---------------------------------------------------------------- 发布分发


def _dispatch(platform_type: int, assets: Dict[str, Any], title: str, body: str,
              account_list: List[str], *, dry_run: bool, is_draft: bool, tieba_kw: str = "") -> None:
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
            inline_image_paths=inline, **common,
        )
    elif platform_type == 7:
        return post_article_toutiao(
            title, body, tags, account_list, work_statements=["引用AI"],
            inline_image_paths=inline, **common,
        )
    elif platform_type == 8:
        return post_article_sohu(
            title, body, tags, account_list,
            cover_paths=[cover] if cover else [],
            inline_image_paths=inline,
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
        return post_article_dayu(title, body, tags, account_list, is_draft=is_draft, file_list=inline, **common)
    elif platform_type == 15:
        return post_article_qiehao(title, body, tags, account_list, is_draft=is_draft, file_list=inline, **common)
    else:
        raise ValueError(f"暂不支持的平台类型: {platform_type}")


# ---------------------------------------------------------------- 主流程


def resolve_platforms(args, assets: Dict[str, Any]) -> List[int]:
    if args.platforms != "auto":
        requested = [int(x) for x in args.platforms.split(",") if x.strip()]
        return [p for p in requested if p not in preflight.BANNED_PLATFORMS]

    picked = preflight.pick_random_platforms(
        DB_PATH, Path(BASE_DIR),
        count=args.count,
        seed=args.seed,
    )
    if len(picked) < args.count:
        print(f"⚠️ 可用平台只有 {len(picked)} 个（目标 {args.count} 个），请先补绑账号")
    return picked


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="一稿五投统一发布入口")
    parser.add_argument("--episode", required=True, help="期数，如 15")
    parser.add_argument("--dir", help="直接指定该期文案目录（默认按 --episode 自动发现）")
    parser.add_argument("--base", default=str(DEFAULT_MATERIAL_ROOT), help="物料包根目录")
    parser.add_argument("--platforms", default="auto", help="auto 或逗号分隔的平台类型，如 5,7,9,11")
    parser.add_argument("--count", type=int, default=5, help="auto 模式下抽几个平台")
    parser.add_argument("--seed", type=int, help="随机种子，便于复现")
    parser.add_argument("--title", help="覆盖自动解析出的标题")
    parser.add_argument("--dry-run", action="store_true", help="只填表不提交")
    parser.add_argument("--draft", action="store_true", help="保存为平台草稿")
    parser.add_argument("--verify-cookie", action="store_true", help="发布前真正开浏览器校验 cookie（慢）")
    parser.add_argument("--force", action="store_true", help="忽略幂等检查，强制重新发布")
    parser.add_argument("--tieba-kw", default="", help="贴吧目标吧名（type=10 必填）")
    args = parser.parse_args(argv)

    if not DB_PATH.exists():
        print(f"❌ 数据库不存在: {DB_PATH}")
        return 2
    publish_record.ensure_publish_history_schema(DB_PATH)

    try:
        assets = load_assets(args.episode, Path(args.base), Path(args.dir) if args.dir else None)
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        return 2

    title = args.title or assets["topic"]
    if not title:
        print("❌ 未能解析出标题，请用 --title 显式指定")
        return 2

    platforms = resolve_platforms(args, assets)
    if not platforms:
        print("❌ 没有可用平台")
        return 2

    print(f"📦 第 {args.episode} 期 | 标题: {title}")
    print(f"🖼  封面: {assets['cover'] or '无'} | 内文图: {len(assets['inline'])} 张")
    print(f"🎯 本轮平台: {[preflight.PLATFORM_NAMES.get(p, p) for p in platforms]}")

    # 预检（含封禁、账号、cookie 文件）
    checks = asyncio_run_preflight(platforms, args.verify_cookie)
    for c in checks:
        icon = "✅" if c["ok"] else "❌"
        print(f"  {icon} {c['platform_name']}: {c['reason']}")

    results: List[Dict[str, Any]] = []
    for check in checks:
        ptype = check["platform_type"]
        pname = check["platform_name"]

        if not check["ok"]:
            results.append({"platform": pname, "type": ptype, "status": "skipped", "reason": check["reason"]})
            continue

        if not args.force:
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
        platform_title = (
            assets["short_title"]
            if ptype in SHORT_FORM_PLATFORMS
            else assets["title_by_platform"].get(ptype, title)
        )
        compliance_result = compliance_check(
            platform_title, body, ptype,
            has_cover=bool(assets["cover"]),
            inline_count=len(assets["inline"]),
        )
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
                    import hashlib
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

        cookie = Path(str(check["cookie_path"]))
        account_file = [cookie.name]

        history_id = publish_record.create_record(
            DB_PATH,
            platform_type=ptype,
            platform_name=pname,
            account_name=str(check.get("account") or "默认账号"),
            content_type="article",
            title=platform_title,
            content=body,
            files=assets["gallery"],
            status=publish_record.STATUS_PREVIEW if args.dry_run
            else (publish_record.STATUS_DRAFT if args.draft else publish_record.STATUS_PUBLISHING),
            episode=args.episode,
            topic=assets["topic"],
        )

        direct_url = ""
        try:
            direct_url = str(
                _dispatch(
                    ptype, assets, platform_title, body, account_file,
                    dry_run=args.dry_run, is_draft=args.draft, tieba_kw=args.tieba_kw,
                ) or ""
            ).strip()
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            # 失败分类与记录
            code, is_new = failure_classify.record_failure(
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

        if args.dry_run or args.draft:
            final_status = publish_record.STATUS_PREVIEW if args.dry_run else publish_record.STATUS_DRAFT
            publish_record.finish_record(DB_PATH, history_id, final_status)
            results.append({"platform": pname, "type": ptype, "status": final_status})
            print(f"  ✅ {pname} 已{('预览' if args.dry_run else '存草稿')}")
            continue

        url, item_id = collect_sync(ptype, cookie, platform_title)
        if not url and direct_url:
            # 发布器已拿到真实链接（如微博），优先采用，采集仅作兜底
            url, item_id = direct_url, _item_id_from_url(ptype, direct_url)
            print(f"  🔗 使用发布器返回链接: {url}")
        if not url:
            error_msg = "发布动作已完成，但未能采集到标题匹配的公开链接；请核查平台后台"
            code, is_new = failure_classify.record_failure(
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
        )
        results.append({"platform": pname, "type": ptype, "status": "published", "url": url, "item_id": item_id})
        print(f"  ✅ {pname} 已发布: {url}")

    summary = {
        "episode": args.episode,
        "title": title,
        "platforms": platforms,
        "results": results,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = RUN_LOG_DIR / f"episode_{args.episode}_publish_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    published = sum(1 for r in results if r["status"] == "published")
    print(f"\n📊 完成 {published} 个平台，明细已写入 {out}")
    return 0 if published or args.dry_run else 1


def asyncio_run_preflight(platforms: List[int], verify_cookie: bool) -> List[Dict[str, Any]]:
    import asyncio

    return asyncio.run(
        preflight.preflight(platforms, DB_PATH, Path(BASE_DIR), verify_cookie=verify_cookie)
    )


if __name__ == "__main__":
    raise SystemExit(main())
