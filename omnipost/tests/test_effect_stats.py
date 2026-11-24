# tests/test_effect_stats.py
import sqlite3
import unittest
from datetime import datetime, timedelta

from myUtils.effect_stats import (
    apply_snapshots,
    parse_tieba_action_texts,
    parse_timestamp,
    parse_wechat_publish_response,
    parse_weibo_status,
    parse_zhihu_html,
    snapshot_slots,
    topics_from_markdown,
)


class ParseTests(unittest.TestCase):
    def test_weibo_keeps_real_zero_and_omits_missing_read(self):
        parsed = parse_weibo_status({
            "attitudes_count": 0,
            "comments_count": 2,
            "reposts_count": 0,
        })
        self.assertEqual(parsed["metrics"]["like_count"], 0)
        self.assertEqual(parsed["metrics"]["comment_count"], 2)
        self.assertNotIn("read_count", parsed["metrics"])

    def test_weibo_reads_count_when_present(self):
        parsed = parse_weibo_status({"reads_count": 8, "attitudes_count": 1})
        self.assertEqual(parsed["metrics"]["read_count"], 8)

    def test_zhihu_html_has_no_read_count(self):
        html = '{"voteupCount": 3, "commentCount": 1, "likedCount": 4}'
        parsed = parse_zhihu_html(html)
        self.assertEqual(parsed["metrics"], {"like_count": 3, "comment_count": 1})
        self.assertEqual(parsed["extra"], {"liked_count": 4})
        self.assertNotIn("read_count", parsed["metrics"])

    def test_tieba_number_sticks_to_previous_label(self):
        parsed = parse_tieba_action_texts(["转发", "回复", "1", "收藏"])
        self.assertEqual(parsed["metrics"], {"comment_count": 1})
        self.assertNotIn("share_count", parsed["metrics"])
        self.assertNotIn("collect_count", parsed["metrics"])

    def test_tieba_splits_label_and_number_in_one_node(self):
        parsed = parse_tieba_action_texts(["转发", "回复\n1", "收藏"])
        self.assertEqual(parsed["metrics"], {"comment_count": 1})

    def test_tieba_orphan_number_is_ignored(self):
        parsed = parse_tieba_action_texts(["1", "回复"])
        self.assertEqual(parsed["metrics"], {})

    def test_wechat_publish_page_keeps_zero_read_and_skips_collect(self):
        payload = {
            "publish_page": (
                '{"publish_list":[{"publish_info":{'
                '"sent_info":{"time":1700000000},'
                '"appmsg_info":[{"appmsgid":42,"title":"模型可以起草",'
                '"read_num":14,"like_num":0,"comment_num":0,"share_num":1,'
                '"old_like_num":2,"content_url":"https://mp.weixin.qq.com/s/abc"}]}}]}'
            )
        }
        rows = parse_wechat_publish_response(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_id"], "42")
        self.assertEqual(rows[0]["metrics"]["read_count"], 14)
        self.assertEqual(rows[0]["metrics"]["like_count"], 0)
        self.assertNotIn("collect_count", rows[0]["metrics"])
        self.assertEqual(rows[0]["extra"]["zaikan"], 2)
        self.assertIsNotNone(rows[0]["published_at"])

    def test_topics_skip_header_and_keep_first_title(self):
        text = "\n".join([
            "| 日期 | 标题 |",
            "| --- | --- |",
            "| 2026-11-24 | 模型可以起草，不能放行 | 已存草稿 |",
            "| 2026-11-25 | 模型可以起草，不能放行 | 重复 |",
        ])
        self.assertEqual(
            topics_from_markdown(text),
            {"模型可以起草，不能放行": "2026-11-24"},
        )


class SlotTests(unittest.TestCase):
    def test_windows(self):
        now = datetime(2026, 11, 24, 12, 0, 0)
        self.assertEqual(snapshot_slots(now - timedelta(hours=2), now), ["latest"])
        self.assertEqual(
            snapshot_slots(now - timedelta(hours=30), now),
            ["latest", "h24"],
        )
        self.assertEqual(
            snapshot_slots(now - timedelta(days=7, hours=2), now),
            ["latest", "d7"],
        )
        self.assertEqual(snapshot_slots(now - timedelta(days=10), now), ["latest"])
        self.assertEqual(snapshot_slots(None, now), ["latest"])

    def test_timestamp_seconds(self):
        parsed = parse_timestamp(1700000000)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.year, 2023)


class StoreTests(unittest.TestCase):
    def test_latest_updates_and_h24_does_not(self):
        conn = sqlite3.connect(":memory:")
        now = datetime(2026, 11, 24, 12, 0, 0)
        published = now - timedelta(hours=30)
        common = dict(
            platform_type=11,
            platform_name="微博",
            item_id="mid1",
            title="标题",
            episode="27",
            published_url="https://weibo.com/1/mid1",
            published_at=published,
            extra={},
            note="微博",
        )
        apply_snapshots(conn, metrics={"like_count": 1, "comment_count": 0}, now=now, **common)
        later = now + timedelta(hours=1)
        apply_snapshots(conn, metrics={"like_count": 5}, now=later, **common)
        latest = conn.execute(
            "SELECT like_count, comment_count, read_count FROM effect_snapshots WHERE snapshot_slot='latest'"
        ).fetchone()
        frozen = conn.execute(
            "SELECT like_count, comment_count FROM effect_snapshots WHERE snapshot_slot='h24'"
        ).fetchone()
        self.assertEqual(latest, (5, None, None))
        self.assertEqual(frozen, (1, 0))
        conn.close()


if __name__ == "__main__":
    unittest.main()
