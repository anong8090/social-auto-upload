#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人 IP 模式 B 长文：只存微信公众号 / 今日头条草稿，不发布。

与 publish_ai_share.py 的每日 180~320 字流水线分开：
- 长文正文至少 500 个中文字符，不套用每日段落上限；
- 优先使用同目录的 正文排版.html，避免 Markdown 标记进入公众号；
- 只允许微信公众号（12）与今日头条（7），只存草稿，不提供发布参数；
- 头条正文只有一个图片块：封面取第 1 张，内文只插第 2 张。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
OMNIPOST_DIR = PROJECT_DIR / "omnipost"
sys.path.insert(0, str(OMNIPOST_DIR))

from myUtils import postVideo, preflight  # noqa: E402
from scripts import publish_ai_share as share  # noqa: E402

TITLE_LIMIT = 64
TOUTIAO_TITLE_LIMIT = 30
ALLOWED_PLATFORMS = (7, 12)
DEFAULT_MIN_CHARS = 500
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def chinese_count(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def parse_content(day_dir: Path) -> dict:
    path = day_dir / "内容.md"
    if not path.exists():
        raise SystemExit(f"缺少内容文件：{path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    title = ""
    for i, line in enumerate(lines):
        if line.strip().startswith("# "):
            title = line.strip()[2:].strip()
            title_index = i
            break
    else:
        raise SystemExit(f"{path} 缺少一级标题（# 标题）")
    body = "\n".join(lines[title_index + 1:]).strip()
    html_path = day_dir / "正文排版.html"
    html = html_path.read_text(encoding="utf-8").strip() if html_path.exists() else ""
    return {"title": title, "body": body, "html": html}


def collect_images(day_dir: Path) -> list[Path]:
    return sorted(
        p for p in day_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )


def check_content(day_dir: Path, min_chars: int, platforms: list[int]) -> dict:
    content = parse_content(day_dir)
    title, body = content["title"], content["body"]
    images = collect_images(day_dir)
    errors: list[str] = []
    warnings: list[str] = []

    if 12 in platforms and len(title) > TITLE_LIMIT:
        errors.append(f"标题 {len(title)} 字，超过公众号 {TITLE_LIMIT} 字")
    if 7 in platforms and len(title) > TOUTIAO_TITLE_LIMIT:
        errors.append(f"标题 {len(title)} 字，超过今日头条 {TOUTIAO_TITLE_LIMIT} 字")
    body_chars = chinese_count(body)
    if body_chars < min_chars:
        errors.append(f"正文 {body_chars} 个中文字符，长文至少要求 {min_chars}")

    for word in share.BRAND_EXCLUSIONS:
        if word in body:
            errors.append(f"正文出现品牌排除词「{word}」")
    for word in preflight.STATIC_BANNED_WORDS:
        if word in body:
            errors.append(f"正文出现项目零容忍词「{word}」")
    public_errors, public_warnings = share.check_public_level(body)
    errors.extend(public_errors)
    warnings.extend(public_warnings)
    if "瑞信" in body:
        errors.append("正文出现「瑞信」；机构名只放平台简介/账号资料")
    if "河狸云" in body:
        warnings.append("正文出现「河狸云」；确认只放平台简介/账号资料")

    for phrase in ("我最近重看", "我发现之前想窄", "这次改稿", "素材来自"):
        if phrase in body:
            warnings.append(f"正文出现生产过程叙述「{phrase}」")

    if not images:
        errors.append("目录中没有配图；至少需要 1 张")
    hashes: dict[str, Path] = {}
    for image in images:
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if digest in hashes:
            errors.append(f"配图完全重复：{hashes[digest].name} 与 {image.name}")
        else:
            hashes[digest] = image

    if not content["html"]:
        warnings.append("没有 正文排版.html，公众号将按纯文本注入，Markdown 标记可能原样出现")

    anchors: list[str] = []
    anchors_path = day_dir / "配图位置.json"
    if anchors_path.exists():
        try:
            raw_anchors = json.loads(anchors_path.read_text(encoding="utf-8"))
            if not isinstance(raw_anchors, list):
                raise ValueError("配图位置.json 必须是字符串数组")
            anchors = [str(x).strip() for x in raw_anchors if str(x).strip()]
        except Exception as exc:
            errors.append(f"配图位置.json 无法解析：{exc}")
        if anchors and len(anchors) != len(images):
            errors.append(
                f"配图位置 {len(anchors)} 个，配图 {len(images)} 张，数量必须一致"
            )
    elif len(images) > 1:
        warnings.append("没有 配图位置.json，多张配图将批量插入，可能堆在文末")

    return {
        "dir": str(day_dir),
        "title": title,
        "body": body,
        "html": content["html"],
        "body_chars": body_chars,
        "images": images,
        "anchors": anchors,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="个人 IP 模式 B 长文：只存公众号/头条草稿")
    parser.add_argument("--dir", required=True, help="长文目录，需含 内容.md 与配图")
    parser.add_argument("--platforms", default="12",
                        help="平台类型，仅允许 7(今日头条)、12(微信公众号)；默认 12")
    parser.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS, help="正文最少中文字符数")
    parser.add_argument("--check-only", action="store_true", help="只做离线预检，不启动浏览器")
    parser.add_argument(
        "--patch-existing",
        action="store_true",
        help="按标题打开既有草稿，重新注入正文并按段落位置替换配图，不新建草稿",
    )
    parser.add_argument(
        "--existing-title",
        default="",
        help="修补既有草稿时用于定位的旧标题；换标题时必填，避免新建重复草稿",
    )
    args = parser.parse_args()

    platforms = [int(x) for x in args.platforms.split(",") if x.strip()]
    illegal = [p for p in platforms if p not in ALLOWED_PLATFORMS]
    if illegal:
        raise SystemExit(f"只允许 {ALLOWED_PLATFORMS}，收到非法平台 {illegal}")
    if args.patch_existing and platforms != [12]:
        raise SystemExit("--patch-existing 只允许与 --platforms 12 一起使用")

    day_dir = Path(args.dir).expanduser().resolve()
    if not day_dir.is_dir():
        raise SystemExit(f"目录不存在：{day_dir}")

    assets = check_content(day_dir, args.min_chars, platforms)
    print(f"标题：{assets['title']}（{len(assets['title'])} 字）")
    print(f"正文：{assets['body_chars']} 个中文字符 | 配图：{len(assets['images'])} 张")
    if assets["anchors"]:
        print(f"配图位置：{len(assets['anchors'])} 个段落锚点")
    for item in assets["errors"]:
        print(f"[错误] {item}")
    for item in assets["warnings"]:
        print(f"[提醒] {item}")
    if assets["errors"]:
        print("[未通过] 先修正错误再存草稿。")
        return 2
    if args.check_only:
        print("[通过] 长文离线预检通过；未启动浏览器、未写数据库。")
        return 0

    # 新建模式：已有同名草稿不重复创建；修补模式则明确更新既有草稿。
    # 头条草稿只能由上传器在同一个会话里核验，这里不预读，交给下方逐个平台处理。
    skip_wechat = False
    if not args.patch_existing and 12 in platforms:
        try:
            if share._readback_drafts(12, assets["title"], require_image=False):
                print("[跳过] 公众号草稿箱已存在同名草稿，未重复创建。")
                skip_wechat = True
        except Exception as exc:
            print(f"[提醒] 同名草稿预检未完成：{exc}")

    body_for_wechat = assets["html"] or assets["body"]
    # 修补既有草稿时，定位用旧标题、写入用新标题；否则换标题会找不到旧草稿。
    existing_title = (args.existing_title or "").strip()
    locate_title = existing_title or assets["title"]

    results: list[dict] = []
    screenshot_copy: Path | None = None
    for ptype in platforms:
        label = "微信公众号" if ptype == 12 else "今日头条"
        if ptype == 12 and skip_wechat:
            results.append({"platform": label, "type": ptype, "readback": True, "status": "已存在同名草稿，跳过"})
            continue
        try:
            accounts = share._account_files(ptype)
            if not accounts:
                raise RuntimeError(f"未找到{label}账号")
            if ptype == 12:
                postVideo.post_article_wechat_mp(
                    title=assets["title"],
                    body=body_for_wechat,
                    tags=[],
                    account_file=accounts,
                    cover_path=str(assets["images"][0]),
                    file_list=[str(p) for p in assets["images"]],
                    is_draft=True,
                    dry_run=False,
                    existing_draft_title=locate_title if args.patch_existing else "",
                    replacement_title=assets["title"] if args.patch_existing else "",
                    inline_image_anchors=assets["anchors"],
                )
                hit = share._readback_drafts(12, assets["title"], require_image=True)
            else:
                # 头条正文单图块：封面第 1 张，内文只插第 2 张；is_draft 走 save=0。
                postVideo.post_article_toutiao(
                    title=assets["title"],
                    body=share.to_plain_text(assets["body"]),
                    tags=[],
                    account_file=accounts,
                    cover_path=str(assets["images"][0]),
                    inline_image_paths=[str(p) for p in assets["images"][1:2]],
                    work_statements=["引用AI"],
                    is_draft=True,
                    prepare_only=False,
                    dry_run=False,
                )
                hit = share._readback_toutiao_draft(assets["title"])
            status = "已存草稿（回读核验通过）" if hit else "存草稿未通过回读核验"
        except Exception as exc:
            hit, status = False, f"失败：{exc}"
        results.append({"platform": label, "type": ptype, "readback": bool(hit), "status": status})

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if 12 in platforms:
        screenshot = OMNIPOST_DIR / "cookiesFile" / "wechat_mp_draft_saved.png"
        screenshot_copy = PROJECT_DIR / "logs" / f"wechat_mp_draft_longform_{stamp}.png"
        if screenshot.exists():
            shutil.copy2(screenshot, screenshot_copy)

    summary = {
        "date": stamp,
        "title": assets["title"],
        "body_chars": assets["body_chars"],
        "images": [p.name for p in assets["images"]],
        "results": results,
        "screenshot": str(screenshot_copy) if screenshot_copy and screenshot_copy.is_file() else "",
    }
    summary_path = PROJECT_DIR / "logs" / f"ai_share_longform_{stamp}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    failed = [r for r in results if not r["readback"]]
    if failed:
        print("[失败] 以下平台未通过草稿回读，不得误判为成功：" +
              "、".join(r["platform"] for r in failed), file=sys.stderr)
        return 2
    print("[成功] 已存入草稿箱，未发布。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
