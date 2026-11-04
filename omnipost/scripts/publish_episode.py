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
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OMNIPOST_DIR))

from conf import BASE_DIR  # noqa: E402
from myUtils import preflight, publish_record  # noqa: E402
from myUtils.link_collector import collect_sync, _item_id_from_url  # noqa: E402

DEFAULT_MATERIAL_ROOT = OMNIPOST_DIR.parent / "河狸云蓝枢_自媒体物料包"
DB_PATH = Path(BASE_DIR) / "db" / "database.db"
RUN_LOG_DIR = OMNIPOST_DIR.parent / "logs"

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

    long_body = _read_markdown(long_md) if long_md else ""
    short_body = _read_markdown(short_md) if short_md else long_body
    topic = _first_heading(long_body) or episode_dir.name

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
        "short_body": short_body,
        "cover": str(cover) if cover else "",
        "inline": [str(p) for p in (inline or [])],
        "gallery": [str(p) for p in gallery],
        "image_dir": str(image_dir) if image_dir else "",
    }


# ---------------------------------------------------------------- 合规闸门


BANNED_WORDS = (
    "15天免费试点", "免费试跑", "免费领", "私信领资料", "加微信", "扫码体验", "点击链接",
    "www.ricent.com", "国家级", "全网最强", "100%零失误",
)


def compliance_check(title: str, body: str, platform_type: int, *, has_cover: bool) -> List[str]:
    """返回不通过原因列表；空列表表示通过。"""
    problems: List[str] = []
    low, high = preflight.TITLE_LIMITS.get(platform_type, (1, 2000))
    title_len = len(title or "")
    if not title:
        problems.append("标题为空")
    elif title_len < low or title_len > high:
        problems.append(f"标题 {title_len} 字，超出该平台 {low}~{high} 字红线")

    text = f"{title}\n{body}"
    hits = [w for w in BANNED_WORDS if w in text]
    if hits:
        problems.append(f"命中违规词: {', '.join(hits)}")

    if platform_type in preflight.COVER_REQUIRED_PLATFORMS and not has_cover:
        problems.append("该平台要求 16:9 封面，但未找到 01_16x9 头图")

    return problems


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
        post_article_baijiahao(title, body, tags, account_list, ai_generated=True, **common)
    elif platform_type == 7:
        post_article_toutiao(title, body, tags, account_list, work_statements=["引用AI"], **common)
    elif platform_type == 8:
        post_article_sohu(
            title, body, tags, account_list,
            cover_paths=[p for p in ([cover] + inline) if p],
            **common,
        )
    elif platform_type == 9:
        post_article_zhihu(
            title, body, tags, account_list,
            creation_statement=True, is_draft=is_draft, **common,
        )
    elif platform_type == 10:
        post_article_tieba(
            title, body, tags, account_list,
            kw=tieba_kw or None, is_draft=is_draft,
            **{k: v for k, v in common.items() if k != "cover_path"},
            cover_path=cover,
        )
    elif platform_type == 11:
        post_article_weibo(
            title, body, tags, account_list,
            is_draft=is_draft, file_list=gallery,
            **common,
        )
    elif platform_type == 12:
        post_article_wechat_mp(title, body, tags, account_list, is_draft=True, **common)
    elif platform_type == 13:
        post_article_wangyi(title, body, tags, account_list, is_draft=is_draft, file_list=inline, **common)
    elif platform_type == 14:
        post_article_dayu(title, body, tags, account_list, is_draft=is_draft, file_list=inline, **common)
    elif platform_type == 15:
        post_article_qiehao(title, body, tags, account_list, is_draft=is_draft, file_list=inline, **common)
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

        body = assets["long_body"] if ptype in LONG_FORM_PLATFORMS else assets["short_body"]
        problems = compliance_check(title, body, ptype, has_cover=bool(assets["cover"]))
        if problems:
            results.append({"platform": pname, "type": ptype, "status": "blocked", "reason": "; ".join(problems)})
            print(f"  ⛔ {pname} 合规拦截: {'; '.join(problems)}")
            continue

        cookie = Path(str(check["cookie_path"]))
        account_file = [cookie.name]

        history_id = publish_record.create_record(
            DB_PATH,
            platform_type=ptype,
            platform_name=pname,
            account_name=str(check.get("account") or "默认账号"),
            content_type="article",
            title=title,
            content=body,
            files=assets["gallery"],
            status=publish_record.STATUS_PREVIEW if args.dry_run
            else (publish_record.STATUS_DRAFT if args.draft else publish_record.STATUS_PUBLISHING),
            episode=args.episode,
            topic=assets["topic"],
        )

        try:
            _dispatch(
                ptype, assets, title, body, account_file,
                dry_run=args.dry_run, is_draft=args.draft, tieba_kw=args.tieba_kw,
            )
        except Exception as exc:  # noqa: BLE001
            publish_record.finish_record(DB_PATH, history_id, publish_record.STATUS_FAILED, error_msg=str(exc))
            results.append({"platform": pname, "type": ptype, "status": "failed", "reason": str(exc)})
            print(f"  ❌ {pname} 发布失败: {exc}")
            continue

        if args.dry_run or args.draft:
            final_status = publish_record.STATUS_PREVIEW if args.dry_run else publish_record.STATUS_DRAFT
            publish_record.finish_record(DB_PATH, history_id, final_status)
            results.append({"platform": pname, "type": ptype, "status": final_status})
            print(f"  ✅ {pname} 已{('预览' if args.dry_run else '存草稿')}")
            continue

        url, item_id = collect_sync(ptype, cookie, title)
        publish_record.finish_record(
            DB_PATH, history_id, publish_record.STATUS_PUBLISHED,
            published_url=url, platform_item_id=item_id,
        )
        results.append({"platform": pname, "type": ptype, "status": "published", "url": url, "item_id": item_id})
        print(f"  ✅ {pname} 已发布: {url or '链接待补采'}")

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
