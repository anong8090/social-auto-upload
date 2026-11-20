# -*- coding: utf-8 -*-
"""第 19 期母本回退回归：没有专属母本的平台也须保留精确插图标记。"""
from __future__ import annotations

import sys
from pathlib import Path

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(OMNIPOST_DIR / "scripts"))

from scripts import publish_episode as pe  # noqa: E402

MATERIAL_ROOT = OMNIPOST_DIR.parent / "河狸云蓝枢_自媒体物料包"
EPISODE = "19"


def test_supported_long_platforms_have_two_inline_markers():
    assets = pe.load_assets(EPISODE, MATERIAL_ROOT, None)
    for ptype in (9, 14, 15):
        body = assets["long_body_by_platform"].get(ptype, assets["long_body"])
        assert "[[插图1]]" in body and "[[插图2]]" in body, (
            f"平台 {ptype} 的正文必须保留 2 张精确插图位置"
        )
    print("✅ 知乎 / 大鱼号 / 企鹅号正文均含 2 个插图标记")


def test_unsupported_platforms_never_leak_markers():
    assets = pe.load_assets(EPISODE, MATERIAL_ROOT, None)
    assert "[[插图" not in assets["long_body"], "通用长文不得把内部标记发给平台"
    for ptype in (5, 8, 13):
        body = assets["long_body_by_platform"].get(ptype, "")
        assert "[[插图" not in body, f"平台 {ptype} 不应带插图标记"
    print("✅ 不支持精确插图的平台正文无标记泄漏")


if __name__ == "__main__":
    test_supported_long_platforms_have_two_inline_markers()
    test_unsupported_platforms_never_leak_markers()
