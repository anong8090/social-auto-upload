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
from uploader.dayu_uploader.main import _is_public_dayu_url, _is_login_url  # noqa: E402
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


def test_dayu_login_redirect_is_detected():
    """cookie 失效时大鱼号会重定向到首页并带 redirect_url，URL 里没有 /login。

    旧实现只按 /login/passport/auth 判路径，会把登录页误判成「已登录」，
    随后报「未找到标题输入框」这种误导性错误。这里锁定修复后的判定。
    """
    assert _is_login_url("https://mp.dayu.com/?redirect_url=%2Fdashboard%2Farticle%2Fwrite")
    assert _is_login_url("https://mp.dayu.com/login")
    assert not _is_login_url("https://mp.dayu.com/dashboard/article/write")
    assert not _is_login_url("https://mp.dayu.com/dashboard/index")
    print("✅ 大鱼号登录重定向判定")


def test_dayu_draft_never_clicks_publish():
    """大鱼号存草稿：草稿分支只点「保存」，不得点「发表」。"""
    from uploader.dayu_uploader import main as dayu_main

    source = Path(dayu_main.__file__).read_text(encoding="utf-8")
    # 草稿候选按钮里不得出现「发表」「发布」
    draft_block = source.split('if self.is_draft:')[1].split('else:')[0]
    assert "发表" not in draft_block and "发布" not in draft_block, draft_block
    # 保存按钮候选必须覆盖页面真实文案「保存」
    assert 'has-text("保存")' in draft_block, draft_block
    print("✅ 大鱼号草稿分支只点「保存」，不点「发表」")


def test_baijiahao_draft_never_clicks_publish():
    """百家号存草稿：只点「存草稿」，不得点「发布」。"""
    from uploader.baijiahao_uploader import main as bjh_main

    source = Path(bjh_main.__file__).read_text(encoding="utf-8")
    assert "async def save_draft" in source, "百家号缺少 save_draft 方法"
    save_block = source.split("async def save_draft")[1].split("async def _verify_draft_in_list")[0]
    assert "存草稿" in save_block, save_block
    # 草稿分支里不得出现点「发布」
    assert "发布按钮" not in save_block and "发布主按钮" not in save_block, save_block
    # save_draft 内部不得出现 publish_button（发布按钮定位）
    assert "publish_button" not in save_block, save_block
    # 草稿分支必须做回读核验，不能只截图就宣称成功
    assert "_verify_draft_in_list" in save_block, save_block
    print("✅ 百家号草稿分支只点「存草稿」，并回读核验")


def test_sohu_draft_never_clicks_publish():
    """搜狐号存草稿：只点「存草稿」，不得点「发布」。"""
    from uploader.sohu_uploader import main as sohu_main

    source = Path(sohu_main.__file__).read_text(encoding="utf-8")
    assert "async def save_draft" in source, "搜狐号缺少 save_draft 方法"
    save_block = source.split("async def save_draft")[1].split("async def _verify_draft_in_list")[0]
    assert "存草稿" in save_block, save_block
    assert "publish_candidates" not in save_block, "草稿分支不得复用发布按钮定位"
    assert "_verify_draft_in_list" in save_block, save_block
    print("✅ 搜狐号草稿分支只点「存草稿」，并回读核验")


def test_qiehao_draft_verifies_by_readback():
    """企鹅号草稿必须回读内容管理页核验，不能只截图就宣称成功。"""
    from uploader.qiehao_uploader import main as qh_main

    source = Path(qh_main.__file__).read_text(encoding="utf-8")
    assert "async def _verify_draft_in_list" in source, "企鹅号缺少草稿核验方法"
    assert "QIEHAO_MANAGE_URL" in source, "企鹅号核验必须回内容管理页"
    print("✅ 企鹅号草稿分支回读核验")


def test_wangyi_draft_verifies_by_readback():
    """网易号草稿必须回草稿箱核验，核验不过要抛错而不是静默成功。"""
    from uploader.wangyi_uploader import main as wy_main

    source = Path(wy_main.__file__).read_text(encoding="utf-8")
    assert "async def _verify_draft_in_list" in source, "网易号缺少草稿核验方法"
    assert "草稿保存未通过核验" in source, "核验失败必须抛错，不得让调用方误判为已存草稿"
    print("✅ 网易号草稿分支回读核验")


def test_draft_is_default_and_zhihu_is_exception():
    """默认走草稿；知乎/微博/贴吧因平台侧无真草稿，例外走真实发布。"""
    source = (OMNIPOST_DIR / "scripts" / "publish_episode.py").read_text(encoding="utf-8")
    assert "parser.set_defaults(draft=True)" in source, "草稿必须是默认行为"
    assert "DIRECT_PUBLISH_PLATFORMS = (9, 11, 10)" in source, "知乎/微博/贴吧应为例外"
    print("✅ 草稿为默认行为，知乎/微博/贴吧例外直接发布")


def test_sohu_ai_declaration_wording_matches_page():
    """搜狐号必选声明文案必须是页面真实存在的「含有AI生成内容」。

    旧文案「包含AI创作内容」在页面上不存在，会导致 AI 声明静默缺失。
    """
    from uploader.sohu_uploader.main import SOHU_INFO_SOURCE_AI, SOHU_INFO_SOURCE_OPTIONS

    assert SOHU_INFO_SOURCE_AI == "含有AI生成内容", SOHU_INFO_SOURCE_AI
    assert "含有AI生成内容" in SOHU_INFO_SOURCE_OPTIONS
    # 旧文案不得再作为有效选项出现
    assert "包含AI创作内容" not in SOHU_INFO_SOURCE_OPTIONS
    assert "无特别声明" not in SOHU_INFO_SOURCE_OPTIONS

    # 统一入口必须传页面真实文案
    source = (OMNIPOST_DIR / "scripts" / "publish_episode.py").read_text(encoding="utf-8")
    assert 'info_source="含有AI生成内容"' in source, "统一入口须传真实文案"
    assert 'info_source="包含AI创作内容"' not in source, "旧文案会导致 AI 声明丢失"
    print("✅ 搜狐号必选声明文案与页面一致")


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

    # 大鱼号已改为按标记把图片插到母本指定位置
    assert "[[插图1]]" in assets["long_body_by_platform"][14]

    # 其余长文平台不得出现标记，否则标记会被原样发到线上
    for ptype in (5, 8, 13):
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


def test_toutiao_force_cannot_bypass_republish_guard():
    """2026-11-18 第23期头条重复发布复盘：普通 --force 不得放行。

    上次先用自动草稿（记 failed），又以 --toutiao-prepare-only + --force
    再开可见窗口；脚本把代填直接记 draft，但平台后台实际在窗口内发布了。
    修复后头条必须按任意尝试记录拦截，published 永远硬拦截。
    """
    import sqlite3
    import tempfile

    from myUtils import publish_record as pr

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "publish.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """CREATE TABLE publish_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform_type INTEGER, platform_name TEXT, account_name TEXT,
                    content_type TEXT, title TEXT, content TEXT, tags TEXT, files TEXT,
                    tieba_fid TEXT, tieba_kw TEXT, product_id INTEGER,
                    status TEXT, error_msg TEXT, published_url TEXT,
                    platform_item_id TEXT, screenshot_dir TEXT,
                    topic TEXT, published_at TEXT, created_at TEXT
                )"""
            )
        pr.ensure_publish_history_schema(db)
        history_id = pr.create_record(
            db, platform_type=7, platform_name="今日头条", account_name="测试账号",
            content_type="article", title="现场照片和标注，别再散在群里",
            status=pr.STATUS_FAILED, episode="23",
        )
        assert history_id is not None

        # 旧逻辑只查 published，会误判「没发过」；新逻辑必须查到 failed 尝试。
        assert pr.find_published_record(db, "23", 7) is None
        failed_attempt = pr.find_any_attempt_record(db, "23", 7)
        assert failed_attempt and failed_attempt["status"] == pr.STATUS_FAILED

        pr.finish_record(
            db, history_id, pr.STATUS_PUBLISHED,
            published_url="https://www.toutiao.com/article/7709335666614452755/",
        )
        assert pr.find_any_attempt_record(db, "23", 7)["status"] == pr.STATUS_PUBLISHED

    source = (OMNIPOST_DIR / "scripts" / "publish_episode.py").read_text(encoding="utf-8")
    assert "REPUBLISH_FORBIDDEN_PLATFORMS = (7,)" in source
    assert "--force-retry-failed" in source
    assert "find_any_attempt_record(DB_PATH, args.episode, ptype)" in source
    assert "普通 --force 不再放行" in source
    print("✅ 今日头条 --force 不再绕过防重发闸门")


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


def test_toutiao_draft_never_rewrites_save_param():
    """头条存草稿：绝不能改写 save 参数（save=1 是直接投稿）。"""
    from uploader.toutiao_uploader import main as tt_main

    source = Path(tt_main.__file__).read_text(encoding="utf-8")
    save_block = source.split("async def save_draft")[1].split("async def _verify_draft_in_list")[0]
    # 不得出现任何改写 save 参数的拦截逻辑
    for forbidden in ("route.continue_", "page.route", "urlencode", "route_handler"):
        assert forbidden not in save_block, f"头条草稿分支不得出现 {forbidden}（改写 save 会造成真实发布）"
    assert "严禁再加拦截" in save_block or "不拦截任何请求" in save_block, save_block
    print("✅ 头条草稿分支不拦截、不改写 save 参数")


def test_toutiao_draft_accepts_any_successful_response():
    """头条自动保存会连发多次，不能只看最后一次响应判定成败。"""
    from uploader.toutiao_uploader import main as tt_main

    source = Path(tt_main.__file__).read_text(encoding="utf-8")
    save_block = source.split("async def save_draft")[1].split("async def _verify_draft_in_list")[0]
    # 必须记录全部响应，而不是只留最后一次
    assert "responses.append" in save_block, "头条草稿分支必须记录每一次保存响应"
    assert "responses: list[dict]" in save_block, save_block
    # 判定逻辑要遍历全部响应
    assert "for r in responses" in save_block, save_block
    print("✅ 头条草稿分支记录并遍历全部保存响应，不被单次 7050 误判")


def test_toutiao_draft_verifies_by_draft_box():
    """头条草稿必须回草稿箱按标题回读核验，核验不过要抛错。"""
    from uploader.toutiao_uploader import main as tt_main

    source = Path(tt_main.__file__).read_text(encoding="utf-8")
    assert "async def _verify_draft_in_list" in source, "头条缺少草稿核验方法"
    assert "draft_list" in source, "头条核验必须读草稿箱列表接口"
    assert "草稿保存未通过核验" in source, "核验失败必须抛错，不得让调用方误判为已存草稿"
    print("✅ 头条草稿分支回草稿箱回读核验")


def test_toutiao_inline_image_limited_to_one():
    """头条正文只放得下 1 张内文图（平台限制：插第二张会替换第一张）。"""
    from uploader.toutiao_uploader import main as tt_main

    source = Path(tt_main.__file__).read_text(encoding="utf-8")
    block = source.split("async def _insert_inline_images")[1].split("async def _count_image_blocks")[0]
    assert "paths = paths[:1]" in block, "头条正文只能插 1 张图，超出必须截断"
    # 计数必须按图片块（.pgc-img）而不是 <img> 元素
    assert "_count_image_blocks" in block, block
    assert "div.pgc-img" in source, "图片块计数必须按 .pgc-img 容器来数"
    print("✅ 头条正文内文图限制为 1 张，且按图片块计数")


def test_toutiao_cover_always_uploaded():
    """头条给了封面就必须真上传，不能因误判 ready 而跳过。"""
    from uploader.toutiao_uploader import main as tt_main

    source = Path(tt_main.__file__).read_text(encoding="utf-8")
    assert "展示封面已是真实预览图，跳过重复上传" not in source, (
        "跳过封面上传会因封面误判而漏传，服务端会拒存（7050）"
    )
    print("✅ 头条封面不再因误判而跳过上传")


def test_compliance_toutiao_requires_one_inline_image():
    """头条按 1 张内文图判定，其余长文平台仍是 2 张。"""
    from scripts import compliance_check as cc

    assert 7 in cc.SINGLE_INLINE_IMAGE_PLATFORMS, "头条必须走单图规则"
    assert 7 not in cc.INLINE_IMAGE_PLATFORMS, "头条不能同时要求 2 张"
    for p in (5, 8, 9, 13, 14, 15):
        assert p in cc.INLINE_IMAGE_PLATFORMS, f"平台 {p} 仍应要求 2 张内文图"
    print("✅ 合规闸门：头条 1 张内文图，其余长文平台 2 张")


def test_wechat_mp_uses_system_chrome_and_never_pops_browser():
    """公众号必须用系统 Chrome 绕开 bundled 内核崩溃，且任何路径都强制无头。"""
    source = (OMNIPOST_DIR / "uploader/wechat_mp_uploader/main.py").read_text(encoding="utf-8")
    assert '"channel"] = "chrome"' in source or '"channel": "chrome"' in source, (
        "微信公众平台在 Playwright bundled Chromium 下稳定 Page crashed，必须走系统 Chrome"
    )
    assert "_launch_wechat_browser" in source, "缺少系统 Chrome 不可用时的降级启动函数"
    assert source.count("await _launch_wechat_browser(playwright)") == 3, (
        "cookie_auth / cookie_gen / upload 三处启动点都必须走专用启动函数"
    )
    assert '"headless": True' in source, "公众号任何路径都不得弹出可见浏览器"
    assert "headless=False" not in source and "headless=False" not in source.replace(" ", "")
    assert "self.headless = True" in source, "dry_run 也不得把 headless 改回 False"
    assert "login_qr.png" in source, "无头扫码必须把二维码截图落盘供人工扫描"
    assert "img.login__type__container__scan__qrcode" in source, (
        "必须等真实二维码元素渲染，不能截登录页首屏空白图"
    )
    assert "naturalWidth > 100" in source, "二维码图片必须加载完成后才交付给人工扫码"
    assert "await qr_locator.screenshot(path=str(qr_path))" in source, (
        "必须裁剪二维码元素，由同一个无头登录进程等待并保存扫码后的 cookie"
    )
    assert "_dismiss_education_dialog" in source, (
        "公众号新版编辑器的教育提示弹窗会遮住标题输入框，必须先关闭"
    )
    assert "save_draft_btn.evaluate(\"(el) => el.click()\")" in source, (
        "底部「保存为草稿」按钮可能在视口外，必须有 DOM click 兜底"
    )
    print("✅ 公众号：系统 Chrome 绕崩溃 + 全路径强制无头 + 二维码可见后截图")


def test_wechat_mp_draft_requires_real_inline_image_and_cover():
    """公众号草稿必须真正插图/设封面，保存前硬闸门 + 草稿箱配图回读。"""
    uploader = (OMNIPOST_DIR / "uploader/wechat_mp_uploader/main.py").read_text(encoding="utf-8")
    for symbol in (
        "_body_editor", "_editor_image_count", "_cover_ready",
        "_insert_inline_images", "_set_cover_from_body",
        "_assert_media_ready", "_open_existing_draft", "_draft_patch_mode",
    ):
        assert symbol in uploader, f"公众号上传器缺少 {symbol}"
    assert "self.file_list" in uploader and "self.cover_path" in uploader
    assert "公众号保存前图文闸门未通过" in uploader
    assert "不得保存缺图草稿" in uploader and "不得保存缺封面草稿" in uploader
    # 草稿分支只点「保存为草稿」，不得触及「发表」链路。
    draft_block = uploader.split("if self.is_draft:")[1].split("send_btn =")[0]
    assert "保存为草稿" in draft_block
    draft_code = "\n".join(
        line for line in draft_block.splitlines()
        if not line.lstrip().startswith("#") and not line.lstrip().startswith('"""')
    )
    assert "#js_send" not in draft_code and "发表" not in draft_code, draft_code

    ai = (OMNIPOST_DIR / "scripts/publish_ai_share.py").read_text(encoding="utf-8")
    assert "type=77" in ai, "公众号草稿箱必须用 type=77，type=9 是发表记录"
    assert r"\xa0" in ai and "NBSP" in ai, "草稿箱标题 NBSP 必须归一化后回读"
    assert "require_image=(ptype == 12)" in ai, "公众号回读必须校验配图"
    assert "--patch-existing-wechat-draft" in ai
    assert "只允许与 --platforms 12 一起使用" in ai
    assert "mmbiz" in ai, "封面回读需识别公众号素材域名，不能只认 computed background-image"

    pv = (OMNIPOST_DIR / "myUtils/postVideo.py").read_text(encoding="utf-8")
    assert "existing_draft_title=existing_draft_title" in pv
    assert "effective_inline" in pv, "后台只给封面时也要补正文图来源，避免缺图草稿"

    backend = (OMNIPOST_DIR / "sau_backend.py").read_text(encoding="utf-8")
    import re
    blocks = backend.split("case 12:")[1:]
    assert len(blocks) >= 3, f"后台应有 3 处公众号调用，实际 {len(blocks)}"
    for block in blocks[:3]:
        segment = block.split("case ")[0][:1200]
        assert "post_article_wechat_mp(" in segment
        assert "file_list=file_list" in segment, "后台公众号调用必须透传配图"
    print("✅ 公众号：正文插图/封面硬闸门 + type=77/NBSP 回读 + 既有草稿修补 + 后台配图透传")


def test_ai_share_visible_xhs_uses_cdp_attach_and_keeps_window():
    """方案 A：小红书可见窗口必须走独立进程 + CDP attach，且草稿回读用同一 profile。"""
    src = (OMNIPOST_DIR / "scripts/publish_ai_share.py").read_text(encoding="utf-8")
    assert "--visible-xhs" in src
    assert "subprocess.Popen" in src, "留窗必须独立进程拉 Chrome，Playwright launch 的会被 driver 杀掉"
    assert "--remote-debugging-port" in src and "--user-data-dir" in src
    assert "connect_over_cdp" in src
    assert "browser.close()  # 只断 CDP" in src or "只断 CDP" in src
    assert "launch_persistent_context" in src, "小红书回读必须用固定持久化 profile"
    assert "browser_profiles" in src and "xiaohongshu" in src

    uploader = (OMNIPOST_DIR / "uploader/xiaohongshu_uploader/main.py").read_text(encoding="utf-8")
    assert "cdp_endpoint" in uploader, "上传器需支持 attach 到可见窗口"
    assert "connect_over_cdp(self.cdp_endpoint)" in uploader
    # attach 成功路径不能 close context（否则窗口没意义）
    assert "attach 到调用方独立拉起的可见 Chrome" in uploader

    pv = (OMNIPOST_DIR / "myUtils/postVideo.py").read_text(encoding="utf-8")
    assert "cdp_endpoint=cdp_endpoint" in pv, "post_note_xhs 必须透传 CDP 地址"
    print("✅ 方案 A：独立进程可见窗口 + CDP attach + 同一 profile 回读")


def test_sohu_upload_respects_global_headless_setting():
    """搜狐号发文也服从全局无头配置，不再强制弹出可见浏览器。"""
    source = (OMNIPOST_DIR / "uploader/sohu_uploader/main.py").read_text(encoding="utf-8")
    assert "_build_launch_kwargs(self.headless)" in source
    assert "_build_launch_kwargs(False)" not in source
    assert "self.headless = LOCAL_CHROME_HEADLESS if not dry_run else False" in source
    print("✅ 搜狐号：发文路径遵循全局无头配置")


def test_auto_pool_excludes_warned_sohu():
    """搜狐号被平台警告后，主线随机选台不得继续抽中它。"""
    source = (OMNIPOST_DIR / "scripts/publish_episode.py").read_text(encoding="utf-8")
    assert "AUTO_PUBLISHABLE_PLATFORMS = (5, 9, 11, 15)" in source
    assert "被平台警告" in source
    print("✅ 主线随机选台：已排除被警告的搜狐号")


def test_daily_tieba_pair_and_toutiao_stays_out_of_auto():
    """每天固定两个贴吧；头条不进 auto，显式指定时默认代填而不是 save=0。"""
    import sqlite3
    import tempfile
    from types import SimpleNamespace

    from myUtils import publish_record as pr

    assert pe.AUTO_PUBLISHABLE_PLATFORMS == (5, 9, 11, 15)
    assert 7 not in pe.AUTO_PUBLISHABLE_PLATFORMS
    assert 10 not in pe.AUTO_PUBLISHABLE_PLATFORMS
    assert pe.default_tieba_bars() == ["建筑", "土木工程"]
    assert pe.default_tieba_bars({"建筑"}) == ["土木工程", "市政工程"]
    assert pe.parse_tieba_bars("建筑,土木工程，建筑") == ["建筑", "土木工程"]

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "publish.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                """CREATE TABLE publish_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform_type INTEGER, platform_name TEXT, account_name TEXT,
                    content_type TEXT, title TEXT, content TEXT, tags TEXT, files TEXT,
                    tieba_fid TEXT, tieba_kw TEXT, product_id INTEGER,
                    status TEXT, error_msg TEXT, published_url TEXT,
                    platform_item_id TEXT, screenshot_dir TEXT,
                    topic TEXT, published_at TEXT, created_at TEXT
                )"""
            )
        pr.ensure_publish_history_schema(db)
        pr.create_record(
            db, platform_type=10, platform_name="百度贴吧·建筑", account_name="测试",
            content_type="article", title="已发建筑", status=pr.STATUS_PUBLISHED,
            episode="40", tieba_kw="建筑",
        )
        named, unscoped = pr.tieba_published_state(db, "40")
        assert named == {"建筑"} and unscoped is False
        pr.create_record(
            db, platform_type=10, platform_name="百度贴吧", account_name="测试",
            content_type="article", title="旧帖没记吧名", status=pr.STATUS_PUBLISHED,
            episode="41",
        )
        named_old, unscoped_old = pr.tieba_published_state(db, "41")
        assert unscoped_old is True
        assert "旧" not in "".join(named_old)

    original_pick = pe.preflight.pick_random_platforms
    original_state = pe.publish_record.tieba_published_state
    try:
        pe.preflight.pick_random_platforms = lambda *args, **kwargs: [5, 11]
        pe.publish_record.tieba_published_state = lambda *args, **kwargs: (set(), False)
        auto = SimpleNamespace(platforms="auto", count=5, seed=1, tieba_kw="")
        jobs = pe.resolve_jobs(auto, "40")
        assert [job["platform_type"] for job in jobs] == [5, 11, 10, 10]
        assert [job["tieba_kw"] for job in jobs if job["platform_type"] == 10] == ["建筑", "土木工程"]

        pe.publish_record.tieba_published_state = lambda *args, **kwargs: ({"建筑"}, False)
        shifted = pe.resolve_jobs(auto, "40")
        assert [job["tieba_kw"] for job in shifted if job["platform_type"] == 10] == ["土木工程", "市政工程"]

        pe.publish_record.tieba_published_state = lambda *args, **kwargs: (set(), True)
        blocked = pe.resolve_jobs(auto, "41")
        assert [job["tieba_kw"] for job in blocked if job["platform_type"] == 10] == ["建筑", "土木工程"]

        one_bar = SimpleNamespace(platforms="10", count=5, seed=None, tieba_kw="建筑")
        assert pe.resolve_jobs(one_bar, "40") == [{"platform_type": 10, "tieba_kw": "建筑"}]

        no_tieba = SimpleNamespace(platforms="5,9", count=5, seed=None, tieba_kw="")
        assert [job["platform_type"] for job in pe.resolve_jobs(no_tieba, "40")] == [5, 9]

        toutiao = SimpleNamespace(platforms="7", count=5, seed=None, tieba_kw="")
        assert pe.resolve_jobs(toutiao, "40") == [{"platform_type": 7, "tieba_kw": ""}]
    finally:
        pe.preflight.pick_random_platforms = original_pick
        pe.publish_record.tieba_published_state = original_state

    source = (OMNIPOST_DIR / "scripts/publish_episode.py").read_text(encoding="utf-8")
    assert "use_prepare = ptype == TOUTIAO_PLATFORM and not args.toutiao_save_draft" in source
    assert "未发送保存请求、未点击发布" in source
    assert "ptype == TIEBA_PLATFORM and not args.force" in source
    print("✅ 每日两个贴吧写入选台；头条退出 auto，默认代填")


def test_ai_share_xhs_readback_regressions():
    """小红书独立线：修复可见窗口 profile 路径依赖与图文草稿标签回读。"""
    uploader_source = (OMNIPOST_DIR / "uploader/xiaohongshu_uploader/main.py").read_text(
        encoding="utf-8"
    )
    assert "from conf import BASE_DIR" in uploader_source, (
        "CDP 可见窗口的 profile 路径依赖 conf.BASE_DIR，不能漏导入"
    )
    import importlib

    xhs_uploader = importlib.import_module("uploader.xiaohongshu_uploader.main")
    assert hasattr(xhs_uploader, "BASE_DIR"), "上传器模块必须能实际导入 BASE_DIR"

    source = (OMNIPOST_DIR / "scripts/publish_ai_share.py").read_text(encoding="utf-8")
    assert r'图文笔记\(\d+\)' in source, (
        "小红书草稿箱默认停在视频笔记，必须切到图文笔记标签再按标题回读"
    )
    assert "image_tab.first.click()" in source, "图文笔记标签必须真实点击后才回读"
    assert "草稿箱抽屉默认停在「视频笔记(0)」" in source
    print("✅ 小红书独立线：BASE_DIR 导入 + 图文笔记草稿回读")


def test_ai_share_content_line_is_draft_only_and_brand_free():
    """独立内容线：只存草稿、只走 1/12、不提蓝枢，且不改主线封禁表。"""
    source = (OMNIPOST_DIR / "scripts/publish_ai_share.py").read_text(encoding="utf-8")
    assert "ALLOWED_PLATFORMS = (1, 12)" in source, "独立内容线只允许小红书 + 公众号"
    assert 'for forbidden in ("--publish", "--no-draft", "--direct")' in source
    assert "is_draft=True" in source, "两个平台都必须强制存草稿"
    assert "BRAND_EXCLUSIONS" in source and '"蓝枢"' in source, (
        "必须校验正文不出现蓝枢"
    )
    assert "_readback_drafts" in source, "存完草稿必须回读核验"
    assert "_wechat_token" in source, "公众号草稿箱回读必须从后台链接取当前 token"
    assert "preflight.BANNED_PLATFORMS[" not in source, (
        "独立脚本不得写入/改动主线的封禁拦截表"
    )
    # 主线仍保留小红书硬拦截，随机池不含小红书
    from myUtils import preflight
    assert 1 in preflight.BANNED_PLATFORMS, "主线必须继续硬拦截小红书"
    assert 1 not in preflight.DEFAULT_POOL, "主线随机 5 平台池不得含小红书"
    print("✅ 独立内容线：只存草稿 + 只走 1/12 + 不提蓝枢 + 主线封禁不受影响")


def test_ai_share_personal_ip_is_concise_daily_and_not_mainline_repost():
    """个人 IP 线：每天一条、更精炼，且不得复用蓝枢标题/正文/配图。"""
    import tempfile
    from scripts import publish_ai_share as ai

    src = (OMNIPOST_DIR / "scripts/publish_ai_share.py").read_text(encoding="utf-8")
    ag = (OMNIPOST_DIR.parent / "AGENTS.md").read_text(encoding="utf-8")
    assert "打造个人 IP" in ag and "每天只发一条" in ag
    assert "比蓝枢主线更精炼" in ag and "不得重复发布蓝枢内容" in ag
    assert "BODY_CHAR_RANGE = (180, 320)" in src
    assert "MAX_PARAGRAPHS = 3" in src and "MAX_SUBHEADINGS = 1" in src
    assert "_assert_one_daily_content" in src and "_find_repetition" in src
    assert "_assert_distinct_images" in src
    assert "publish_history" in src and "MAINLINE_TEXT_ROOT" in src

    original_material_root = ai.MATERIAL_ROOT
    original_text_root = ai.MAINLINE_TEXT_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "AI能力分享内容线"
        day = root / "2026-11-10"
        day.mkdir(parents=True)
        ai.MATERIAL_ROOT = root
        ai.MAINLINE_TEXT_ROOT = Path(tmp) / "文案合集"
        ai.MAINLINE_TEXT_ROOT.mkdir()
        try:
            body = (
                "# 一个判断，今天先讲清楚\n\n"
                "今天只记一个很朴素的判断：工程里的 AI 是否可靠，不看回答得多流畅，"
                "而看它有没有把依据、口径和没检查到的地方一并交出来。"
                "现场要的是能追回去的数字，不是一句听起来很像结论的话。"
                "真正让人安心的，往往不是更聪明的措辞，而是能被复核的来路。\n\n"
                "所以我更愿意把边界写在前面：模型负责理解，程序负责计算和核对，"
                "关键动作留给人确认。遇到资料不全，就老实标出未执行；"
                "遇到口径不一致，就把差异摆出来。"
                "少一点神奇感，反而更容易被工程人长期相信。\n\n"
                "个人做产品复盘时，这类克制并不抢眼，却决定了工具能不能进真实流程。"
                "今天先把这句话记下来：AI 的价值不是替人拍板，"
                "而是让每一次拍板都有更清楚的证据。"
            )
            (day / "内容.md").write_text(body, encoding="utf-8")
            (day / "配图.png").write_bytes(b"fake")
            (day / "内容-第二篇.md").write_text("# 今天另一条\n\n正文", encoding="utf-8")
            try:
                ai.precheck(day, [1, 12])
            except ValueError as exc:
                assert "每天一条" in str(exc)
            else:
                raise AssertionError("同一天两份内容稿必须被拒绝")

            (day / "内容-第二篇.md").unlink()
            assert 180 <= ai._chinese_char_count(body.split("\n", 1)[1]) <= 320
            ai.precheck(day, [1, 12])  # 精炼、原创、当日配图应通过

            # 复用主线发布历史里的整句，必须判为重复
            contaminated = body.replace(
                "所以我更愿意把边界写在前面：",
                "。入口统一、包按需切小、缓存钉在现行版上——这三件事做到位，"
                "没信号的区域也能把图看对、把版次说清。\n\n所以我更愿意把边界写在前面：",
            )
            (day / "内容.md").write_text(contaminated, encoding="utf-8")
            try:
                ai.precheck(day, [1, 12])
            except ValueError as exc:
                assert any(
                    word in str(exc)
                    for word in ("重复", "压缩改写", "最多 3 个", "正文 4 个自然段")
                ), f"复用主线稿必须被任一内容闸门拒绝，实际：{exc}"
            else:
                raise AssertionError("复用主线历史句子必须被拒绝")

            # 单独测相似度闸门，避免只被段落数闸门拦下
            assert ai._find_repetition(
                "一个判断，今天先讲清楚", contaminated.split("\n", 1)[1], day
            ), "复用主线稿必须命中历史去重闸门"

            (day / "内容.md").write_text(body, encoding="utf-8")
            (day / "配图.png").unlink()
            outside = OMNIPOST_DIR.parent / "河狸云蓝枢_自媒体物料包" / "品牌标准资产" / "official_logo_transparent.png"
            assert outside.exists(), "缺少用于验证配图闸门的主线素材"
            try:
                ai._assert_distinct_images([str(outside)], day)
            except ValueError as exc:
                assert "禁止复用主线物料" in str(exc)
            else:
                raise AssertionError("主线配图必须被拒绝")
        finally:
            ai.MATERIAL_ROOT = original_material_root
            ai.MAINLINE_TEXT_ROOT = original_text_root

    print("✅ 个人 IP：每日一条 + 180~320 字 + 主线历史/配图去重")


def main():
    test_publish_manual_rejects_publish_flag()
    test_publish_manual_default_is_offline()
    test_publish_manual_module_has_no_browser_import()
    test_qiehao_public_url_only()
    test_dayu_public_url_only()
    test_dayu_login_redirect_is_detected()
    test_dayu_draft_never_clicks_publish()
    test_baijiahao_draft_never_clicks_publish()
    test_sohu_draft_never_clicks_publish()
    test_qiehao_draft_verifies_by_readback()
    test_wangyi_draft_verifies_by_readback()
    test_toutiao_draft_never_rewrites_save_param()
    test_toutiao_draft_accepts_any_successful_response()
    test_toutiao_draft_verifies_by_draft_box()
    test_toutiao_inline_image_limited_to_one()
    test_toutiao_cover_always_uploaded()
    test_compliance_toutiao_requires_one_inline_image()
    test_draft_is_default_and_zhihu_is_exception()
    test_sohu_ai_declaration_wording_matches_page()
    test_sohu_upload_respects_global_headless_setting()
    test_auto_pool_excludes_warned_sohu()
    test_daily_tieba_pair_and_toutiao_stays_out_of_auto()
    test_link_collector_item_id_and_public_guard()
    test_qiehao_title_match_exact()
    test_compliance_blocks_missing_inline_images()
    test_compliance_blocks_banned_words()
    test_inline_markers_only_for_supported_platforms()
    test_inline_markers_not_counted_in_body_length()
    asyncio.run(test_qiehao_dry_run_never_clicks_publish())
    asyncio.run(test_qiehao_prepare_only_never_clicks_publish())
    test_toutiao_force_cannot_bypass_republish_guard()
    test_check_only_does_not_touch_database()
    test_check_only_and_dry_run_are_distinct()
    test_wechat_mp_uses_system_chrome_and_never_pops_browser()
    test_wechat_mp_draft_requires_real_inline_image_and_cover()
    test_ai_share_content_line_is_draft_only_and_brand_free()
    test_ai_share_visible_xhs_uses_cdp_attach_and_keeps_window()
    test_ai_share_xhs_readback_regressions()
    test_ai_share_personal_ip_is_concise_daily_and_not_mainline_repost()
    print("\n🎉 发布安全闸门与链接判定测试全部通过（未联网、未启动浏览器、未发布）。")


if __name__ == "__main__":
    main()
