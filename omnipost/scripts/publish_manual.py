# -*- coding: utf-8 -*-
"""人工/测试辅助脚本（默认完全离线，禁止发布）。

历史用途：把已生成的长文母本填入企鹅号 / 大鱼号发文页，供人工点发布。
2026-11-06 收口后的安全边界：

- 默认只读取当期母本与 6 张配图，做静态合规检查；
- 不启动 Playwright、不打开平台编辑页、不写 publish_history / publish_drafts；
- 不写飞书台账、不点击「发布 / 发表 / 正式发布」、不等待人工发布；
- `--publish` 仅为旧命令兼容参数，命中即拒绝执行；真实发布统一走
  `scripts/publish_episode.py`，且必须由用户当次明确授权。

平台：
  - qiehao  企鹅号 type=15
  - dayu    大鱼号 type=14

用法（均为离线测试）：
  python omnipost/scripts/publish_manual.py --episode 16 --platform qiehao
  python omnipost/scripts/publish_manual.py --episode 16 --platform dayu
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
MATERIAL_ROOT = BASE_DIR / "河狸云蓝枢_自媒体物料包"
OMNIPOST_DIR = BASE_DIR / "omnipost"
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(OMNIPOST_DIR / "scripts"))

from myUtils import preflight  # noqa: E402
from scripts.compliance_check import ComplianceRuleError, compliance_check  # noqa: E402
from scripts.publish_episode import (  # noqa: E402
    _body_with_inline_markers,
    _extract_clean_body,
    find_episode_dir,
    find_image_dir,
)

# 只有这些平台的上传器已支持 `[[插图N]]` 精确插图；其余平台仍用清洗后的纯正文。
INLINE_MARKER_PLATFORMS = (15,)


def _platform_meta(platform: str) -> tuple[int, str, str]:
    """返回 (平台类型, 母本文件名, 平台显示名)。"""
    if platform == "qiehao":
        return 15, "母本B_15_企鹅号.md", "企鹅号"
    if platform == "dayu":
        return 14, "母本B_14_大鱼号.md", "大鱼号"
    raise ValueError(f"不支持的平台: {platform}")


def load_material(episode: str, platform: str) -> dict:
    """读取当期母本与配图；全程只读本地文件。

    `find_image_dir()` 内部会自行拼接「图片素材」目录，调用方传物料包根目录即可，
    不能再传 `MATERIAL_ROOT / "图片素材"`，否则会拼出双重目录而找不到 6 张图。
    """
    ptype, filename, pname = _platform_meta(platform)
    episode_dir = find_episode_dir(MATERIAL_ROOT, episode)

    md_path = episode_dir / filename
    if not md_path.exists():
        raise RuntimeError(f"未找到母本文件: {md_path}")

    raw = md_path.read_text(encoding="utf-8")
    title = raw.splitlines()[0].lstrip("# ").strip()
    clean_body = _extract_clean_body(raw)

    image_dir = find_image_dir(MATERIAL_ROOT, episode, title)
    pngs = sorted(image_dir.glob("*.png")) if image_dir else []
    cover = next((p for p in pngs if "01_" in p.name and "16x9" in p.name), None)
    if not cover:
        cover = next((p for p in pngs if "16x9" in p.name), pngs[0] if pngs else None)
    inline_files = [p for p in pngs if "16x9" in p.name and p != cover][:2]

    body = (
        _body_with_inline_markers(raw, len(inline_files))
        if ptype in INLINE_MARKER_PLATFORMS
        else clean_body
    )
    return {
        "episode": str(episode),
        "ptype": ptype,
        "pname": pname,
        "title": title,
        "body": body,
        "clean_body": clean_body,
        "cover": str(cover) if cover else "",
        "inline": [str(p) for p in inline_files],
        "episode_dir": str(episode_dir),
    }


def offline_check(material: dict) -> dict:
    """只做静态检查：封禁平台、素材完整性、标题/正文/配图/违规词合规。"""
    ptype = int(material["ptype"])
    preflight.assert_platform_allowed(ptype)

    cover = Path(material["cover"]) if material["cover"] else None
    inline = [Path(p) for p in material["inline"]]
    if not cover or not cover.is_file():
        raise RuntimeError(f"未找到可用 16:9 封面: {material['cover'] or '空'}")
    missing = [str(p) for p in inline if not p.is_file()]
    if missing:
        raise RuntimeError(f"内文图文件缺失: {missing}")
    if len(inline) < 2:
        raise RuntimeError(f"16:9 内文图不足 2 张，实际 {len(inline)} 张")

    body = material["body"]
    result = compliance_check(
        material["title"], body, ptype,
        has_cover=True, inline_count=len(inline),
    )
    markers = re.findall(r"\[\[插图(\d+)\]\]", body)
    if ptype in INLINE_MARKER_PLATFORMS and markers != ["1", "2"]:
        result["problems"].append(f"企鹅号插图位置标记异常: {markers}")
        result["passed"] = False

    body_chars = len(re.sub(r"\s|\[\[插图\d+\]\]", "", body))
    print(f"📦 第 {material['episode']} 期 | {material['pname']} | 标题: {material['title']}", flush=True)
    print(f"🖼  封面: {cover.name} | 内文图: {len(inline)} 张 | 正文: {body_chars} 字", flush=True)
    print(f"🧩 插图标记: {markers or '无'}", flush=True)
    for problem in result["problems"]:
        print(f"  ❌ {problem}", flush=True)
    for warning in result["warnings"]:
        print(f"  ⚠️ {warning}", flush=True)
    if result["passed"]:
        print(f"  ✅ {material['pname']} 离线合规检查通过", flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="人工/测试辅助脚本：默认完全离线，不发布")
    ap.add_argument("--episode", required=True)
    ap.add_argument("--platform", required=True, choices=["qiehao", "dayu"])
    ap.add_argument(
        "--publish", action="store_true",
        help="旧命令兼容参数；当前安全策略下会直接拒绝，绝不启动浏览器或点击发布",
    )
    args = ap.parse_args(argv)

    if args.publish:
        print(
            "❌ 已拒绝 --publish：本脚本只做离线测试。真实发布请走 "
            "scripts/publish_episode.py，并在用户当次明确授权后执行。",
            file=sys.stderr,
        )
        return 2

    try:
        material = load_material(args.episode, args.platform)
        result = offline_check(material)
    except (FileNotFoundError, RuntimeError, ValueError, ComplianceRuleError) as exc:
        print(f"❌ 离线检查失败: {exc}", file=sys.stderr)
        return 2

    print("🧪 离线检查完成：未启动浏览器、未点击发布、未写发布记录 / 飞书台账", flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
