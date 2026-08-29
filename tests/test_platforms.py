import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.database import Database
from backend.platforms import (
    BilibiliAdapter,
    DouyinAdapter,
    HuyaAdapter,
    PlatformError,
    parse_room_reference,
)


class RoomReferenceTests(unittest.TestCase):
    def test_parse_bilibili_url_and_id(self):
        from_url = parse_room_reference("https://live.bilibili.com/12345?from=search", "bilibili")
        from_id = parse_room_reference("12345", "bilibili")
        self.assertEqual(from_url.room_key, "12345")
        self.assertEqual(from_url.room_url, "https://live.bilibili.com/12345")
        self.assertEqual(from_id, from_url)

    def test_parse_huya_url_and_slug(self):
        from_url = parse_room_reference("https://www.huya.com/room-name?source=home", "huya")
        from_slug = parse_room_reference("room-name", "huya")
        self.assertEqual(from_url.room_key, "room-name")
        self.assertEqual(from_url, from_slug)

    def test_reject_wrong_platform_host(self):
        with self.assertRaises(ValueError):
            parse_room_reference("https://www.huya.com/123", "bilibili")

    def test_parse_douyin_room_and_profile(self):
        reference = parse_room_reference(
            "https://live.douyin.com/6096197105?anchor_id=60241003767&is_vs=0",
            "douyin",
            "https://www.douyin.com/user/MS4wLjABAAAAigD3FYb1hZWuz_rrD1V25V2hlpCkO3NtT7GDMJlwvkI?from_tab_name=live",
        )
        self.assertEqual(reference.room_key, "6096197105")
        self.assertEqual(reference.anchor_key, "60241003767")
        self.assertEqual(
            reference.room_url,
            "https://live.douyin.com/6096197105?anchor_id=60241003767",
        )
        self.assertEqual(
            reference.profile_url,
            "https://www.douyin.com/user/MS4wLjABAAAAigD3FYb1hZWuz_rrD1V25V2hlpCkO3NtT7GDMJlwvkI",
        )


class PlatformAdapterTests(unittest.TestCase):
    @patch("backend.platforms.http_get")
    def test_bilibili_live_snapshot(self, mock_get):
        mock_get.return_value = json.dumps(
            {
                "code": 0,
                "data": {
                    "room_id": 12345,
                    "title": "晚间直播",
                    "live_status": 1,
                    "live_time": "2026-08-29 20:00:00",
                    "user_cover": "https://example.com/cover.jpg",
                },
            }
        )
        reference = parse_room_reference("12345", "bilibili")
        snapshot = BilibiliAdapter().fetch(reference)
        self.assertEqual(snapshot.status, "live")
        self.assertEqual(snapshot.title, "晚间直播")
        self.assertEqual(snapshot.room_id, "12345")
        self.assertEqual(
            snapshot.live_started_at,
            "2026-08-29T12:00:00+00:00",
        )

    @patch("backend.platforms.http_get")
    def test_huya_offline_snapshot(self, mock_get):
        mock_get.return_value = """
            <body class="liveStatus-off">
              <script>
                var TT_ROOM_DATA = {"isOn":false,"id":7788,"roomName":"休息中"};
                var TT_PROFILE_INFO = {"nick":"测试主播","profileRoom":7788};
              </script>
            </body>
        """
        reference = parse_room_reference("7788", "huya")
        snapshot = HuyaAdapter().fetch(reference)
        self.assertEqual(snapshot.status, "offline")
        self.assertEqual(snapshot.anchor_name, "测试主播")
        self.assertEqual(snapshot.room_id, "7788")

    @patch("backend.platforms.http_get")
    def test_huya_missing_room_is_error(self, mock_get):
        mock_get.return_value = '<body class="liveStatus-off"></body>'
        reference = parse_room_reference("missing-room", "huya")
        with self.assertRaises(PlatformError):
            HuyaAdapter().fetch(reference)

    @patch("backend.platforms.http_get")
    def test_huya_falls_back_to_full_room_page(self, mock_get):
        full_page = """
            <body class="liveStatus-on">
              <script>
                var TT_ROOM_DATA = {"isOn":true,"id":1199630339535,
                  "profileRoom":149721,"roomName":"GENG大战T1",
                  "startTime":1700000000};
                var TT_PROFILE_INFO = {"nick":"神枪小子","profileRoom":149721};
              </script>
            </body>
        """
        mock_get.side_effect = [
            '<html><body>degraded page</body></html>',
            full_page,
        ]
        reference = parse_room_reference("shenqiangxiaozi", "huya")
        snapshot = HuyaAdapter().fetch(reference)
        self.assertEqual(snapshot.status, "live")
        self.assertEqual(snapshot.anchor_name, "神枪小子")
        self.assertEqual(snapshot.room_id, "149721")
        self.assertEqual(
            snapshot.live_started_at,
            "2023-11-14T22:13:20+00:00",
        )
        self.assertEqual(
            mock_get.call_args_list[1].args[0],
            "https://www.huya.com/shenqiangxiaozi?hyaction=home",
        )

    @patch("backend.platforms.http_get")
    def test_douyin_live_snapshot(self, mock_get):
        mock_get.return_value = """
            <script>
              self.__pace_f.push([1,
                "\\\"web_rid\\\":\\\"6096197105\\\","
                "\\\"anchor\\\":{\\\"id_str\\\":\\\"60241003767\\\","
                "\\\"sec_uid\\\":\\\"MS4wLjABAAAAprofile\\\",\\\"nickname\\\":\\\"Gus\\\"},"
                "\\\"roomInfo\\\":{\\\"roomId\\\":\\\"7679259829717732130\\\"},"
                "\\\"logOptions\\\":{\\\"is_live_end\\\":0,\\\"title\\\":\\\"测试直播\\\"},"
                "\\\"poster\\\":\\\"https://example.com/poster.jpg\\\""
              ]);
            </script>
        """
        reference = parse_room_reference("6096197105", "douyin")
        snapshot = DouyinAdapter().fetch(reference)
        self.assertEqual(snapshot.status, "live")
        self.assertEqual(snapshot.anchor_name, "Gus")
        self.assertEqual(snapshot.title, "测试直播")
        self.assertEqual(snapshot.room_id, "7679259829717732130")
        self.assertEqual(snapshot.cover_url, "https://example.com/poster.jpg")
        self.assertEqual(snapshot.live_started_at, "")

    @patch("backend.platforms.http_get")
    def test_douyin_missing_status_is_error(self, mock_get):
        mock_get.return_value = '"web_rid":"6096197105","anchor":{"nickname":"Gus"}'
        reference = parse_room_reference("6096197105", "douyin")
        with self.assertRaises(PlatformError):
            DouyinAdapter().fetch(reference)


class DatabaseTests(unittest.TestCase):
    def test_record_check_preserves_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.db")
            stream_id = database.add_stream(
                "bilibili",
                "12345",
                "https://live.bilibili.com/12345",
                "测试直播间",
            )
            stream, previous = database.record_check(
                stream_id,
                status="live",
                title="正在直播",
            )
            self.assertEqual(previous, "unknown")
            self.assertEqual(stream["status"], "live")
            self.assertEqual(stream["last_live_at"], stream["last_checked_at"])

    def test_record_check_tracks_and_replaces_live_session_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.db")
            stream_id = database.add_stream(
                "douyin",
                "6096197105",
                "https://live.douyin.com/6096197105",
                "测试抖音直播间",
            )
            first_start = (
                datetime.now(timezone.utc) - timedelta(seconds=90)
            ).isoformat(timespec="seconds")

            live, previous = database.record_check(
                stream_id,
                status="live",
                live_started_at=first_start,
            )

            self.assertEqual(previous, "unknown")
            self.assertEqual(live["live_started_at"], first_start)
            self.assertGreaterEqual(live["live_duration_seconds"], 89)

            offline, previous = database.record_check(
                stream_id,
                status="offline",
            )

            self.assertEqual(previous, "live")
            self.assertEqual(offline["live_started_at"], first_start)
            self.assertGreaterEqual(offline["live_duration_seconds"], 89)

            second_start = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            next_live, previous = database.record_check(
                stream_id,
                status="live",
                live_started_at=second_start,
            )

            self.assertEqual(previous, "offline")
            self.assertEqual(next_live["live_started_at"], second_start)
            self.assertLessEqual(next_live["live_duration_seconds"], 1)

    def test_stream_and_notification_pagination(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.db")
            for index in range(21):
                database.add_stream(
                    "bilibili",
                    str(10000 + index),
                    f"https://live.bilibili.com/{10000 + index}",
                    f"直播间 {index}",
                )
                database.record_notification(
                    None,
                    event_type="started",
                    title=f"通知 {index}",
                    message="测试通知",
                    delivered=True,
                )

            streams, stream_pagination = database.list_streams_page(
                page=2,
                page_size=20,
            )
            events, event_pagination = database.list_notification_events_page(
                page=2,
                page_size=20,
            )
            self.assertEqual(len(streams), 1)
            self.assertEqual(stream_pagination["total"], 21)
            self.assertEqual(stream_pagination["total_pages"], 2)
            self.assertEqual(event_pagination["total"], 21)
            self.assertEqual(event_pagination["total_pages"], 2)
            self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
