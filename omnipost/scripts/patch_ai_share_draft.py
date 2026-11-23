#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 AI 能力分享的既有公众号草稿替换为当日定稿正文与配图，不新建草稿。"""
import importlib.util
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
SHARE_SCRIPT = PROJECT_DIR / "omnipost" / "scripts" / "publish_ai_share.py"

spec = importlib.util.spec_from_file_location("publish_ai_share", SHARE_SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path.insert(0, str(PROJECT_DIR / "omnipost"))

from myUtils import postVideo  # noqa: E402

DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-11-24"
OLD_TITLE = sys.argv[2] if len(sys.argv) > 2 else ""
day_dir = PROJECT_DIR / "河狸云蓝枢_自媒体物料包" / "AI能力分享内容线" / DATE
assets = module.precheck(day_dir, [12])
if not OLD_TITLE:
    raise SystemExit("必须传入草稿箱中当前的旧标题，避免新建重复草稿")

html_body_path = day_dir / "正文排版.html"
body = html_body_path.read_text(encoding="utf-8") if html_body_path.exists() else assets["body"]
accounts = module._account_files(12)
result = postVideo.post_article_wechat_mp(
    title=assets["title"],
    body=body,
    tags=[],
    account_file=accounts,
    cover_path=assets["images"][0],
    file_list=assets["images"],
    is_draft=True,
    dry_run=False,
    existing_draft_title=OLD_TITLE,
    replacement_title=assets["title"],
)
hit = module._readback_drafts(12, assets["title"], require_image=True)
print(json.dumps({
    "date": DATE,
    "title": assets["title"],
    "old_title": OLD_TITLE,
    "readback": hit,
    "result": result,
}, ensure_ascii=False, indent=2))
if not hit:
    raise SystemExit(2)
