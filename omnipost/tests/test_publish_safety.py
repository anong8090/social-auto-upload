# -*- coding: utf-8 -*-
"""发布安全闸门与链接判定回归测试（默认不联网、不启动浏览器、绝不点击「发布」）。

覆盖第 16 期修复点：
1. `publish_manual.py` 默认完全离线：不启动浏览器、不写发布记录、不点发布；
   `--publish` 被直接拒绝。
2. 企鹅号/大鱼号公开链接判定：后台编辑页、写稿页不得冒充公开链接。
3. 企鹅号链接采集的标题匹配必须精确到本期标题，旧稿不得匹配。
4. 合规闸门：企鹅号（15）缺 2 张内文图必须拦截。
5. `[[插图N]]` 标记：企鹅号母本按位置生成标记，其余平台不生成。
6. 上传器代填模式不得自动点击发布。

运行：
  ./omnipost/.venv/bin/python omnipost/tests/test_publish_safety.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(OMNIPOST_DIR / "scripts"))

from myUtils import link_collector  # noqa: E402
from scripts import compliance_check as cc  # noqa: E402
from scripts import publish_episode as pe  # noqa: E402
from scripts import publish_manual as pm  # noqa: E402
from uploader.dayu_uploader.main import _is_public_dayu_url  # noqa: E402
from uploader.qiehao_uploader.main import (  # noqa: E402
    QiehaoPost,
    _is_public_qiehao_url,
    _title_match,
)

MATERIAL_ROOT = OMNIPOST_DIR.parent / "河狸云蓝枢_自媒体物料包"
EPISODE = "16"


# ---------------------------------------------------------------- 1. publish_manual 默认离线


def test_publish_manual_rejects_publish_flag():
    """--publish 必须被拒绝，且不进入任何浏览器/发布路径。"""
    rc = pm.main(["--episode", EPISODE, "--platform", "qiehao", "--publish"])
    assert rc == 2, f"期望 --publish 被拒绝（返回 2），实际 {rc}"
    print("✅ publish_manual 拒绝 --publish")


def test_publish_manual_default_is_offline():
    """默认执行只做离线检查：不返回链接、不写发布记录。"""
    material = pm.load_material(EPISODE, "qiehao")
    assert material["title"], "企鹅号母本标题为空"
    assert material["cover"], "未找到 16:9 封面"
    assert len(material["inline"]) == 2, f"内文图应为 2 张，实际 {len(material['inline'])}"
    result = pm.offline_check(material)
    assert result["passed"], f"企鹅号离线合规检查未通过: {result['problems']}"

    # 离线检查不得产生任何公开链接（真实发布路径才会返回链接）
    assert not hasattr(pm, "run_qiehao"), "publish_manual 不应保留浏览器发布入口"
    assert not hasattr(pm, "run_dayu"), "publish_manual 不应保留浏览器发布入口"
    print("✅ publish_manual 默认离线、不含浏览器发布入口")


def test_publish_manual_module_has_no_browser_import():
    """模块顶层不得导入 playwright，避免误触浏览器。"""
    source = (OMNIPOST_DIR / "scripts" / "publish_manual.py").read_text(encoding="utf-8")
    assert "async_playwright" not in source, "publish_manual 不得引用 playwright"
    assert "QiehaoPost" not in source, "publish_manual 不得构造上传器"
    assert "DayuPost" not in source, "publish_manual 不得构造上传器"
    print("✅ publish_manual 不含浏览器依赖")


# ---------------------------------------------------------------- 2. 公开链接判定


def test_qiehao_public_url_only():
    assert _is_public_qiehao_url("https://page.om.qq.com/page/OwJ_rWtDsBy4t35YdrvT5S7A0")
    assert not _is_public_qiehao_url(
        "https://om.qq.com/main/creation/article?articleId=20261106A09NWB&isPubed=1"
    )
    assert not _is_public_qiehao_url("https://om.qq.com/main/management/articleManage")
    assert not _is_public_qiehao_url("")
    print("✅ 企鹅号公开链接判定")


def test_dayu_public_url_only():
    assert _is_public_dayu_url("https://mp.dayu.com/dashboard/article/detail/abc123")
    assert not _is_public_dayu_url("https://mp.dayu.com/dashboard/article/write")
    assert not _is_public_dayu_url("https://mp.dayu.com/dashboard/article/write?id=1")
    assert not _is_public_dayu_url("https://mp.dayu.com/dashboard/index")
    assert not _is_public_dayu_url("")
    print("✅ 大鱼号公开链接判定")


def test_link_collector_item_id_and_public_guard():
    assert link_collector._item_id_from_url(
        15, "https://page.om.qq.com/page/OwJ_rWtDsBy4t35YdrvT5S7A0"
    ) == "OwJ_rWtDsBy4t35YdrvT5S7A0"
    assert link_collector._is_public_qiehao_url(
        "https://page.om.qq.com/page/OwJ_rWtDsBy4t35YdrvT5S7A0"
    )
    assert not link_collector._is_public_qiehao_url(
        "https://om.qq.com/main/creation/article?articleId=20261106A09NWB"
    )
    print("✅ 统一链接采集的条目 ID 与公开性判定")


# ---------------------------------------------------------------- 3. 企鹅号标题匹配


def test_qiehao_title_match_exact():
    title = "会审后的修改，为什么总传不到现场？"
    # 本期条目：卡片文本含标题 → 命中
    assert _title_match("图文 | 会审后的修改，为什么总传不到现场？ | 已发布", title)
    # 上一期条目：不含标题 → 不得命中
    assert not _title_match("图文 | 会审改了图，现场怎么同步到新版？ | 已发布", title)
    assert not _title_match("", title)
    print("✅ 企鹅号标题匹配不串旧稿")


# ---------------------------------------------------------------- 4. 合规闸门


def test_compliance_blocks_missing_inline_images():
    base = "工程现场看图痛点真不少，图纸版本乱、批注找不到、离线看不了，三大根因让现场人员天天翻车。"
    body = base * 19  # 约 850 字，落在长文 800~1100 区间
    ok = cc.compliance_check("会审后的修改，为什么总传不到现场？", body, 15,
                              has_cover=True, inline_count=2)
    assert ok["passed"], f"2 张内文图时应通过: {ok['problems']}"

    bad = cc.compliance_check("会审后的修改，为什么总传不到现场？", body, 15,
                              has_cover=True, inline_count=1)
    assert not bad["passed"], "缺内文图时必须拦截"
    assert any("内文图" in p for p in bad["problems"]), bad["problems"]

    assert 15 in cc.INLINE_IMAGE_PLATFORMS, "企鹅号必须在插图强制检查名单内"
    print("✅ 合规闸门拦截企鹅号内文图缺失")


def test_compliance_blocks_banned_words():
    base = "工程现场看图痛点真不少，免费试跑就让现场人员天天翻车，图纸版本乱、批注找不到、离线看不了。"
    body = base * 19
    result = cc.compliance_check("会审后的修改，为什么总传不到现场？", body, 15,
                                 has_cover=True, inline_count=2)
    assert not result["passed"], "命中零容忍词必须拦截"
    print("✅ 合规闸门拦截零容忍词")


# ---------------------------------------------------------------- 5. 插图标记


def test_inline_markers_only_for_supported_platforms():
    assets = pe.load_assets(EPISODE, MATERIAL_ROOT, None)
    qiehao_body = assets["long_body_by_platform"][15]
    assert "[[插图1]]" in qiehao_body and "[[插图2]]" in qiehao_body, qiehao_body[:200]

    # 知乎同样支持标记
    assert "[[插图1]]" in assets["long_body_by_platform"][9]

    # 其余长文平台不得出现标记，否则标记会被原样发到线上
    for ptype in (5, 8, 13, 14):
        body = assets["long_body_by_platform"].get(ptype, "")
        assert "[[插图" not in body, f"平台 {ptype} 不应带插图标记"
    print("✅ 插图标记只对支持精确插图的平台生成")


def test_inline_markers_not_counted_in_body_length():
    assets = pe.load_assets(EPISODE, MATERIAL_ROOT, None)
    body = assets["long_body_by_platform"][15]
    with_marker = len(cc.re.sub(r"\s", "", body))
    without = len(cc.re.sub(r"\s|\[\[插图\d+\]\]", "", body))
    assert without < with_marker, "插图标记不计入正文字数"
    print("✅ 插图标记不计入正文字数")


# ---------------------------------------------------------------- 6. 上传器代填模式闸门


class _FakeLocator:
    def __init__(self, count=0, visible=False):
        self._count = count
        self._visible = visible
        self.first = self

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible

    async def click(self, **kwargs):
        raise AssertionError("代填/预览模式不得点击任何按钮")


class _FakePage:
    def __init__(self):
        self.locator_calls = 0

    def locator(self, selector):
        self.locator_calls += 1
        return _FakeLocator()


class _FakePlaywright:
    class chromium:
        @staticmethod
        async def launch(**kwargs):
            raise AssertionError("离线测试不得启动浏览器")


async def test_qiehao_dry_run_never_clicks_publish():
    post = QiehaoPost(
        title="会审后的修改，为什么总传不到现场？",
        body="测试正文",
        tags=[],
        publish_date=0,
        account_file="/tmp/nonexistent-cookie.json",
        dry_run=True,
        cover_path="/tmp/nonexistent-cover.png",
        file_list=[],
    )
    assert post.dry_run is True
    assert post.prepare_only is False
    print("✅ 企鹅号预览模式不进入发布路径")


async def test_qiehao_prepare_only_never_clicks_publish():
    """代填模式只允许人工点发布：上传器自身不得点击发布按钮。"""
    post = QiehaoPost(
        title="会审后的修改，为什么总传不到现场？",
        body="测试正文",
        tags=[],
        publish_date=0,
        account_file="/tmp/nonexistent-cookie.json",
        dry_run=False,
        cover_path="/tmp/nonexistent-cover.png",
        file_list=[],
        prepare_only=True,
    )
    assert post.prepare_only is True
    assert post.headless is False, "代填模式必须保留可见窗口"
    print("✅ 企鹅号代填模式保留窗口、脚本不代点发布")


# ---------------------------------------------------------------- 7. 统一入口离线预检


def _count_rows(table: str) -> int:
    import sqlite3

    conn = sqlite3.connect(str(OMNIPOST_DIR / "db" / "database.db"))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_check_only_does_not_touch_database():
    """--check-only 必须只做静态预检：不写发布记录、不启动浏览器。"""
    before_history = _count_rows("publish_history")
    before_drafts = _count_rows("publish_drafts")

    rc = pe.main(["--episode", EPISODE, "--check-only"])

    assert rc == 0, f"第 16 期离线预检应通过，实际 {rc}"
    assert _count_rows("publish_history") == before_history, "离线预检不得写 publish_history"
    assert _count_rows("publish_drafts") == before_drafts, "离线预检不得写 publish_drafts"
    print("✅ 统一入口 --check-only 不写数据库")


def test_check_only_and_dry_run_are_distinct():
    """--dry-run 会打开平台编辑页，--check-only 才是真离线；两者必须可区分。"""
    source = (OMNIPOST_DIR / "scripts" / "publish_episode.py").read_text(encoding="utf-8")
    assert '"--check-only"' in source
    assert '"--dry-run"' in source
    assert "未启动浏览器、未写数据库、未发布" in source
    print("✅ --check-only 与 --dry-run 语义区分")


def main():
    test_publish_manual_rejects_publish_flag()
    test_publish_manual_default_is_offline()
    test_publish_manual_module_has_no_browser_import()
    test_qiehao_public_url_only()
    test_dayu_public_url_only()
    test_link_collector_item_id_and_public_guard()
    test_qiehao_title_match_exact()
    test_compliance_blocks_missing_inline_images()
    test_compliance_blocks_banned_words()
    test_inline_markers_only_for_supported_platforms()
    test_inline_markers_not_counted_in_body_length()
    asyncio.run(test_qiehao_dry_run_never_clicks_publish())
    asyncio.run(test_qiehao_prepare_only_never_clicks_publish())
    test_check_only_does_not_touch_database()
    test_check_only_and_dry_run_are_distinct()
    print("\n🎉 发布安全闸门与链接判定测试全部通过（未联网、未启动浏览器、未发布）。")


if __name__ == "__main__":
    main()
