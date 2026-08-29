import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import LiveMonitorApp
from backend.platforms import RoomSnapshot


class AppApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = LiveMonitorApp(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    @patch("backend.monitor.fetch_room")
    def test_create_update_and_delete_stream(self, mock_fetch):
        mock_fetch.return_value = RoomSnapshot(
            status="offline",
            title="暂未开播",
            room_id="12345",
        )
        status, payload = self.app.handle_api(
            "POST",
            "/api/streams",
            {
                "platform": "bilibili",
                "room_url": "12345",
                "display_name": "晚间关注",
            },
            {},
        )
        self.assertEqual(status, 201)
        stream_id = payload["stream"]["id"]
        self.assertEqual(payload["stream"]["status"], "offline")

        status, payload = self.app.handle_api(
            "PATCH",
            f"/api/streams/{stream_id}",
            {
                "platform": "huya",
                "room_url": "https://www.huya.com/room-name?from=edit",
                "display_name": "修改后的关注",
            },
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["stream"]["platform"], "huya")
        self.assertEqual(payload["stream"]["room_key"], "room-name")
        self.assertEqual(payload["stream"]["room_url"], "https://www.huya.com/room-name")
        self.assertEqual(payload["stream"]["display_name"], "修改后的关注")

        status, payload = self.app.handle_api(
            "PATCH",
            f"/api/streams/{stream_id}",
            {"enabled": False},
            {},
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["stream"]["enabled"])

        status, _ = self.app.handle_api(
            "DELETE",
            f"/api/streams/{stream_id}",
            None,
            {},
        )
        self.assertEqual(status, 200)

    @patch("backend.monitor.send_notification")
    @patch("backend.monitor.fetch_room")
    def test_live_transition_records_delivered_event(self, mock_fetch, mock_send):
        mock_fetch.return_value = RoomSnapshot(
            status="offline",
            title="还没开始",
            room_id="7788",
        )
        _, payload = self.app.handle_api(
            "POST",
            "/api/streams",
            {
                "platform": "huya",
                "room_url": "7788",
                "display_name": "测试主播",
            },
            {},
        )
        stream_id = payload["stream"]["id"]
        mock_fetch.return_value = RoomSnapshot(
            status="live",
            title="已经开播",
            room_id="7788",
        )

        status, _ = self.app.handle_api(
            "POST",
            f"/api/streams/{stream_id}/check",
            None,
            {},
        )
        self.assertEqual(status, 200)
        mock_send.assert_called_once()
        events = self.app.database.list_notification_events()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["delivered"])
        self.assertEqual(events[0]["event_type"], "started")

    def test_settings_do_not_expose_secrets(self):
        status, _ = self.app.handle_api(
            "PUT",
            "/api/settings",
            {
                "monitor_interval_seconds": 45,
                "notify_provider": "serverchan",
                "serverchan_sendkey": "SCT-test-secret",
                "notify_on_start": "false",
            },
            {},
        )
        self.assertEqual(status, 200)
        status, payload = self.app.handle_api("GET", "/api/settings", None, {})
        self.assertEqual(status, 200)
        settings = payload["settings"]
        self.assertFalse(settings["notify_on_start"])
        self.assertTrue(settings["serverchan_sendkey_set"])
        self.assertNotIn("SCT-test-secret", str(settings))


if __name__ == "__main__":
    unittest.main()
