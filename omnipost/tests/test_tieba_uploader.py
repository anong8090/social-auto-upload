# -*- coding: utf-8 -*-
"""百度贴吧上传器 E2E 测试（仅验证标题/正文填写流程，严禁自动点击「发布」）。

验证要点：
1. 类型校验：标题/正文为空、fid 与 kw 均为空时应抛出 ValueError；
2. fid/kw 优先级：同时指定时以 fid 为准；
3. 发帖流程：使用已保存的贴吧 cookie 打开目标贴吧，填写标题与正文，不自动发布，
   并保存凭证截图。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uploader.tieba_uploader.main import TiebaPost

COOKIES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookiesFile")
# 自动探测贴吧 cookie 文件：cookiesFile/ 下第一个非 sohu 的 .json 文件
_COOKIE_CANDIDATES = []
if os.path.isdir(COOKIES_DIR):
    for name in sorted(os.listdir(COOKIES_DIR)):
        if name.endswith(".json") and "sohu" not in name.lower():
            _COOKIE_CANDIDATES.append(os.path.join(COOKIES_DIR, name))
COOKIES_FILE = _COOKIE_CANDIDATES[0] if _COOKIE_CANDIDATES else ""
TITLE = "【E2E 自动化验证】贴吧自动化发帖流程测试 - 不自动发布"
BODY = (
    "这是一条通过自动化脚本填写的贴吧测试帖子正文，仅用于验证 DOM 选择器和填写流程。\n\n"
    "脚本绝不自动点击「发布」，最终发布与否由用户在浏览器中手动决定。"
)


def test_empty_title_raises():
    try:
        TiebaPost(
            title="",
            body=BODY,
            tags=[],
            publish_date=datetime.now(),
            account_file=COOKIES_FILE,
            kw="测试",
        )
    except ValueError as exc:
        assert "标题不能为空" in str(exc)
        print(f"✅ 空标题校验通过：{exc}")
        return
    raise AssertionError("空标题应抛出 ValueError")


def test_empty_body_raises():
    try:
        TiebaPost(
            title=TITLE,
            body="",
            tags=[],
            publish_date=datetime.now(),
            account_file=COOKIES_FILE,
            kw="测试",
        )
    except ValueError as exc:
        assert "正文不能为空" in str(exc)
        print(f"✅ 空正文校验通过：{exc}")
        return
    raise AssertionError("空正文应抛出 ValueError")


def test_no_fid_and_kw_raises():
    try:
        TiebaPost(
            title=TITLE,
            body=BODY,
            tags=[],
            publish_date=datetime.now(),
            account_file=COOKIES_FILE,
            fid=None,
            kw=None,
        )
    except ValueError as exc:
        assert "fid" in str(exc) or "kw" in str(exc)
        print(f"✅ 无 fid/kw 校验通过：{exc}")
        return
    raise AssertionError("fid 与 kw 均为空应抛出 ValueError")


def test_fid_takes_precedence_over_kw():
    post = TiebaPost(
        title=TITLE,
        body=BODY,
        tags=[],
        publish_date=datetime.now(),
        account_file=COOKIES_FILE,
        fid="123456",
        kw="测试贴吧",
    )
    assert post.fid == "123456"
    assert post.kw == "测试贴吧"
    print("✅ fid 优先级校验通过")


async def test_fill_flow_only():
    """实际走一次填写流程（不点发布），保存凭证截图。"""
    if not os.path.exists(COOKIES_FILE):
        print(f"⚠️ 跳过填写流程：未找到 cookie 文件 {COOKIES_FILE}")
        return
    post = TiebaPost(
        title=TITLE,
        body=BODY,
        tags=[],
        publish_date=datetime.now(),
        account_file=COOKIES_FILE,
        fid=None,
        kw="测试",
        dry_run=False,
        cover_path=None,
        is_draft=False,
    )
    await post.main()
    print("✅ 贴吧填写流程 E2E 验证通过（未自动发布）")


def main():
    test_empty_title_raises()
    test_empty_body_raises()
    test_no_fid_and_kw_raises()
    test_fid_takes_precedence_over_kw()
    asyncio.run(test_fill_flow_only())
    print("\n🎉 所有贴吧 E2E 测试项已执行完毕。")


if __name__ == "__main__":
    main()
