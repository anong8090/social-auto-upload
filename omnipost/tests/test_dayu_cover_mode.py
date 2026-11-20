# -*- coding: utf-8 -*-
"""大鱼号单张头图必须先切到单封面模式。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OMNIPOST_DIR))

from uploader.dayu_uploader.main import DayuPost  # noqa: E402


class _EmptyLocator:
    @property
    def first(self):
        return self

    async def count(self):
        return 0


class _Page:
    def __init__(self):
        self.events = []

    def locator(self, _selector):
        self.events.append("locate_cover_slot")
        return _EmptyLocator()


class _Image:
    def __init__(self, visible, box=None, loaded=True):
        self.visible = visible
        self.box = box
        self.loaded = loaded

    async def is_visible(self):
        return self.visible

    async def bounding_box(self):
        return self.box

    async def evaluate(self, _script):
        return self.loaded


class _ImageList:
    def __init__(self, images):
        self.images = images

    async def count(self):
        return len(self.images)

    def nth(self, index):
        return self.images[index]


class _ImagePage:
    def __init__(self, images):
        self.images = _ImageList(images)

    def locator(self, _selector):
        return self.images


class _Radio:
    def __init__(self, checked=False):
        self.checked = checked

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def evaluate(self, _script):
        return self.checked

    async def click(self, **_kwargs):
        self.checked = True


class _ModeOption:
    def __init__(self, radio):
        self.radio = radio

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    def locator(self, _selector):
        return self.radio


class _ModePage:
    def __init__(self):
        self.radio = _Radio()

    def locator(self, _selector):
        return _ModeOption(self.radio)

    async def wait_for_timeout(self, _milliseconds):
        return None


def test_switch_single_cover_clicks_and_verifies_radio():
    async def run():
        post = DayuPost("标题", "正文", [], 0, "unused.json", is_draft=True)
        page = _ModePage()
        assert await post._switch_single_cover(page) is True
        assert page.radio.checked is True

    asyncio.run(run())


def test_confirm_cover_switches_to_single_mode_before_locating_slot():
    async def run():
        page = _Page()
        post = DayuPost("标题", "正文", [], 0, "unused.json", is_draft=True)

        async def switch_to_single_cover(_page):
            page.events.append("switch_to_single_cover")

        post._switch_single_cover = switch_to_single_cover
        assert await post._confirm_cover_from_body(page) is False
        assert page.events == ["switch_to_single_cover", "locate_cover_slot"]

    asyncio.run(run())


def test_hidden_uploaded_cover_is_not_accepted():
    async def run():
        post = DayuPost("标题", "正文", [], 0, "unused.json", is_draft=True)
        page = _ImagePage([_Image(visible=False, box={"width": 100, "height": 60})])
        assert await post._has_uploaded_cover(page) is False

    asyncio.run(run())


def test_broken_uploaded_cover_is_not_accepted():
    async def run():
        post = DayuPost("标题", "正文", [], 0, "unused.json", is_draft=True)
        page = _ImagePage([
            _Image(visible=True, box={"width": 100, "height": 60}, loaded=False)
        ])
        assert await post._has_uploaded_cover(page) is False

    asyncio.run(run())


def test_visible_uploaded_cover_is_accepted():
    async def run():
        post = DayuPost("标题", "正文", [], 0, "unused.json", is_draft=True)
        page = _ImagePage([_Image(visible=True, box={"width": 100, "height": 60})])
        assert await post._has_uploaded_cover(page) is True

    asyncio.run(run())
