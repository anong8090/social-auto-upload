#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人 IP「日常浏览器填入」本地桥接服务（小红书 / 今日头条共用）。

背景
----
Chrome 136+ 禁止对**默认 user data dir** 开启远程调试，外部 Playwright
无法接管用户日常在用的 Chrome。要让脚本操作日常浏览器，唯一干净的做法是
浏览器内的扩展自己读内容——本服务就是扩展的数据来源。

它只做三件事，且**只读**：

    GET /health                          健康检查
    GET /dirs                            可选的每日目录与长文目录
    GET /payload?date=YYYY-MM-DD         当天短稿（标题/正文/配图）
    GET /payload?dir=长文-xxx&kind=long  长文（标题/正文/配图）
    GET /image/<dir>/<filename>          配图二进制

合规闸门复用 `publish_ai_share.precheck`（短稿）与
`publish_ai_share_longform.check_content`（长文）：任一不过返回 4xx，
扩展不会填入。

只用 127.0.0.1 绑定，不对外暴露；不写数据库、不发布、不点击任何按钮。

用法：

    omnipost/.venv/bin/python omnipost/scripts/xhs_fill_bridge.py --port 5419
    omnipost/.venv/bin/python omnipost/scripts/xhs_fill_bridge.py --date 2026-11-27
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OMNIPOST_DIR))

from flask import Flask, abort, jsonify, request, send_file  # noqa: E402
from flask_cors import CORS  # noqa: E402

from scripts import publish_ai_share as share  # noqa: E402
from scripts import publish_ai_share_longform as longform  # noqa: E402

app = Flask(__name__)
# 浏览器扩展的 origin 是 chrome-extension://<随机 id>，无法预先枚举；
# 服务只绑 127.0.0.1，内容本身就是本机文件，因此允许跨源读取。
CORS(app)

DAY_DIR_OVERRIDE: Path | None = None
LONGFORM_MIN_CHARS = 500


def _resolve_dir(date_arg: str, dir_arg: str) -> Path:
    root = share.MATERIAL_ROOT.resolve()
    if DAY_DIR_OVERRIDE is not None:
        return DAY_DIR_OVERRIDE
    if dir_arg:
        target = (share.MATERIAL_ROOT / dir_arg).resolve()
    elif date_arg:
        target = (share.MATERIAL_ROOT / date_arg).resolve()
    else:
        abort(400, "缺少 date 或 dir 参数")
    if root not in target.parents and target != root:
        abort(400, "目录越界")
    if not target.is_dir():
        abort(404, f"内容目录不存在：{target}")
    return target


def _read_tags(day_dir: Path) -> list[str]:
    tag_file = day_dir / "标签.txt"
    if not tag_file.exists():
        return []
    tags = []
    for line in tag_file.read_text(encoding="utf-8").splitlines():
        for item in line.replace("，", ",").split(","):
            clean = item.strip().lstrip("#")
            if clean:
                tags.append(clean)
    return tags[:5]


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "xhs-fill-bridge"})


@app.get("/dirs")
def dirs():
    root = share.MATERIAL_ROOT
    daily, longform_dirs = [], []
    if root.is_dir():
        for p in root.iterdir():
            if not p.is_dir():
                continue
            if p.name.startswith("长文-"):
                longform_dirs.append(p.name)
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name) and (p / "内容.md").exists():
                daily.append(p.name)
    return jsonify(
        {
            "ok": True,
            "daily": sorted(daily, reverse=True),
            "long": sorted(longform_dirs, reverse=True),
        }
    )


@app.get("/payload")
def payload():
    kind = request.args.get("kind", "daily")
    day_dir = _resolve_dir(request.args.get("date", ""), request.args.get("dir", ""))

    if kind == "long":
        assets = longform.check_content(day_dir, LONGFORM_MIN_CHARS, [7])
        if assets["errors"]:
            return jsonify({"ok": False, "error": "合规自检未通过：" + "；".join(assets["errors"])}), 422
        title, body = assets["title"], share.to_plain_text(assets["body"])
        images = assets["images"]
        body_chars = assets["body_chars"]
        warnings = assets["warnings"]
    else:
        try:
            assets = share.precheck(day_dir, [1])
        except Exception as exc:
            return jsonify({"ok": False, "error": f"合规自检未通过：{exc}"}), 422
        title, body = assets["title"], assets["body"]
        images = [Path(p) for p in assets["images"]]
        body_chars = assets["body_chars"]
        warnings = assets.get("warnings", [])

    return jsonify(
        {
            "ok": True,
            "kind": kind,
            "dir": day_dir.name,
            "title": title,
            "body": body,
            "tags": _read_tags(day_dir),
            "images": [
                {"name": p.name, "url": f"/image/{day_dir.name}/{p.name}"}
                for p in images[:9]
            ],
            "body_chars": body_chars,
            "warnings": warnings,
        }
    )


@app.get("/image/<day>/<name>")
def image(day: str, name: str):
    day_dir = _resolve_dir("", day)
    # 只允许目录下的直接文件，阻断 ../ 与子目录穿越
    target = (day_dir / name).resolve()
    if target.parent != day_dir or not target.is_file():
        abort(404, "图片不存在")
    if target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        abort(415, "不支持的图片格式")
    return send_file(target)


def main() -> int:
    global DAY_DIR_OVERRIDE
    parser = argparse.ArgumentParser(description="个人 IP 日常浏览器填入桥接服务（只读）")
    parser.add_argument("--port", type=int, default=5419)
    parser.add_argument("--date", default="", help="固定服务某一天的内容")
    parser.add_argument("--dir", default="", help="直接指定内容目录（优先于 --date）")
    args = parser.parse_args()

    if args.dir:
        DAY_DIR_OVERRIDE = Path(args.dir).expanduser().resolve()
        if not DAY_DIR_OVERRIDE.is_dir():
            raise SystemExit(f"目录不存在：{DAY_DIR_OVERRIDE}")
    elif args.date:
        DAY_DIR_OVERRIDE = share.MATERIAL_ROOT / args.date
        if not DAY_DIR_OVERRIDE.is_dir():
            raise SystemExit(f"目录不存在：{DAY_DIR_OVERRIDE}")

    print(f"🌉 个人 IP 填入桥接已启动：http://127.0.0.1:{args.port}")
    print("   扩展里选日期/长文并「读取内容」即可；本服务只读，不会发布。")
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
