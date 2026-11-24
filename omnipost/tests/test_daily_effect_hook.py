# -*- coding: utf-8 -*-
"""每日预检挂接效果回收：只跑三家，失败不改退出码，不打开贴吧。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

OMNIPOST_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OMNIPOST_DIR))
sys.path.insert(0, str(OMNIPOST_DIR / "scripts"))

import collect_effect_stats  # noqa: E402
import run_daily_preflight as daily  # noqa: E402


class DailyEffectHookTests(unittest.TestCase):
    def test_daily_platforms_exclude_tieba(self):
        self.assertEqual(daily.DAILY_EFFECT_PLATFORMS, (9, 11, 12))
        self.assertNotIn(10, daily.DAILY_EFFECT_PLATFORMS)

    def test_collect_called_with_three_platforms_and_failure_is_logged(self):
        calls = {}

        def boom(platforms, dry_run):
            calls["platforms"] = list(platforms)
            calls["dry_run"] = dry_run
            raise RuntimeError("chrome down")

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(daily, "EFFECT_STATS_LOG_DIR", Path(tmp)), patch.object(
                collect_effect_stats, "collect", boom
            ), patch.object(daily.signal, "setitimer") as alarm:
                outcome = daily._run_effect_stats()
                files = list(Path(tmp).glob("daily_fail_*.json"))
                payload = json.loads(files[0].read_text(encoding="utf-8")) if files else {}
            self.assertFalse(alarm.called)
        self.assertEqual(calls["platforms"], [9, 11, 12])
        self.assertFalse(calls["dry_run"])
        self.assertFalse(outcome["ok"])
        self.assertIn("chrome down", outcome["error"])
        self.assertEqual(payload.get("platforms"), [9, 11, 12])
        self.assertNotIn(10, payload.get("platforms") or [])

    def test_effect_failure_does_not_change_exit(self):
        summary = {
            "link_health_verified_abnormal": 0,
            "review_sync_failed": False,
            "review_conflicts": 0,
            "effect_stats_ok": False,
            "effect_stats_error": "collect 退出码 1",
        }
        self.assertFalse(daily._needs_attention_exit([], summary))
        self.assertTrue(daily._needs_attention_exit([{"ok": False}], summary))


if __name__ == "__main__":
    unittest.main()
