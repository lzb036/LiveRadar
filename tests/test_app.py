import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import LiveMonitorApp
from backend.platforms import PlatformError, RoomSnapshot


class AppApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = LiveMonitorApp(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def login_headers(self):
        response = self.app.handle_api(
            "POST",
            "/api/auth/login",
            {
                "username": self.app.auth.username,
                "password": self.app.auth.initial_credentials.password,
            },
            {},
            {"X-Forwarded-Proto": "https"},
        )
        self.assertEqual(response.status, 200)
        return {"Cookie": response.headers["Set-Cookie"].split(";", 1)[0]}

    def test_authentication_flow(self):
        status, payload = self.app.handle_api("GET", "/api/streams", None, {})
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "请先登录")

        headers = self.login_headers()
        response = self.app.handle_api(
            "GET",
            "/api/auth/me",
            None,
            {},
            headers,
        )
        self.assertTrue(response.body["authenticated"])
        self.assertEqual(response.body["username"], self.app.auth.username)

        status, payload = self.app.handle_api(
            "POST",
            "/api/auth/logout",
            None,
            {},
            headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["authenticated"])

    def test_authentication_accepts_lowercase_proxy_headers(self):
        response = self.app.handle_api(
            "POST",
            "/api/auth/login",
            {
                "username": self.app.auth.username,
                "password": self.app.auth.initial_credentials.password,
            },
            {},
            {"cookie": "", "x-forwarded-proto": "https"},
        )
        self.assertEqual(response.status, 200)
        self.assertIn("Secure", response.headers["Set-Cookie"])
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        protected = self.app.handle_api(
            "GET",
            "/api/streams",
            None,
            {},
            {"cookie": cookie, "x-forwarded-proto": "https"},
        )
        self.assertEqual(protected.status, 200)

    @patch("backend.monitor.fetch_room")
    def test_create_update_and_delete_stream(self, mock_fetch):
        headers = self.login_headers()
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
            headers,
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
            headers,
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
            headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["stream"]["enabled"])

        status, _ = self.app.handle_api(
            "DELETE",
            f"/api/streams/{stream_id}",
            None,
            {},
            headers,
        )
        self.assertEqual(status, 200)

    @patch("backend.monitor.send_notification")
    @patch("backend.monitor.fetch_room")
    def test_live_transition_records_delivered_event(self, mock_fetch, mock_send):
        headers = self.login_headers()
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
            headers,
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
            headers,
        )
        self.assertEqual(status, 200)
        mock_send.assert_called_once()
        events = self.app.database.list_notification_events()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["delivered"])
        self.assertEqual(events[0]["event_type"], "started")

    @patch("backend.monitor.send_notification")
    @patch("backend.monitor.fetch_room")
    def test_transient_check_failure_does_not_repeat_start(
        self, mock_fetch, mock_send
    ):
        headers = self.login_headers()
        mock_fetch.return_value = RoomSnapshot(
            status="offline",
            room_id="7788",
        )
        _, payload = self.app.handle_api(
            "POST",
            "/api/streams",
            {
                "platform": "huya",
                "room_url": "7788",
                "display_name": "稳定测试",
            },
            {},
            headers,
        )
        stream_id = payload["stream"]["id"]
        mock_fetch.return_value = RoomSnapshot(status="live", room_id="7788")
        self.app.handle_api(
            "POST",
            f"/api/streams/{stream_id}/check",
            None,
            {},
            headers,
        )
        mock_fetch.side_effect = PlatformError("虎牙页面暂时无法解析直播间信息")
        self.app.handle_api(
            "POST",
            f"/api/streams/{stream_id}/check",
            None,
            {},
            headers,
        )
        mock_fetch.side_effect = None
        mock_fetch.return_value = RoomSnapshot(status="live", room_id="7788")
        self.app.handle_api(
            "POST",
            f"/api/streams/{stream_id}/check",
            None,
            {},
            headers,
        )
        self.assertEqual(mock_send.call_count, 1)
        events = self.app.database.list_notification_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "started")

    @patch("backend.monitor.send_notification")
    @patch("backend.monitor.fetch_room")
    def test_offline_checks_do_not_repeat_stop(
        self, mock_fetch, mock_send
    ):
        headers = self.login_headers()
        status, _ = self.app.handle_api(
            "PUT",
            "/api/settings",
            {
                "notify_provider": "serverchan",
                "notify_on_start": True,
                "notify_on_stop": True,
                "monitor_interval_seconds": 60,
            },
            {},
            headers,
        )
        self.assertEqual(status, 200)
        mock_fetch.return_value = RoomSnapshot(status="offline", room_id="7788")
        _, payload = self.app.handle_api(
            "POST",
            "/api/streams",
            {
                "platform": "huya",
                "room_url": "7788",
                "display_name": "下播测试",
            },
            {},
            headers,
        )
        stream_id = payload["stream"]["id"]
        mock_fetch.return_value = RoomSnapshot(status="live", room_id="7788")
        self.app.handle_api(
            "POST",
            f"/api/streams/{stream_id}/check",
            None,
            {},
            headers,
        )
        mock_fetch.return_value = RoomSnapshot(status="offline", room_id="7788")
        self.app.handle_api(
            "POST",
            f"/api/streams/{stream_id}/check",
            None,
            {},
            headers,
        )
        self.app.handle_api(
            "POST",
            f"/api/streams/{stream_id}/check",
            None,
            {},
            headers,
        )
        self.assertEqual(mock_send.call_count, 2)
        events = self.app.database.list_notification_events()
        self.assertCountEqual(
            [event["event_type"] for event in events],
            ["started", "stopped"],
        )

    def test_settings_do_not_expose_secrets(self):
        headers = self.login_headers()
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
            headers,
        )
        self.assertEqual(status, 200)
        status, payload = self.app.handle_api(
            "GET",
            "/api/settings",
            None,
            {},
            headers,
        )
        self.assertEqual(status, 200)
        settings = payload["settings"]
        self.assertFalse(settings["notify_on_start"])
        self.assertTrue(settings["serverchan_sendkey_set"])
        self.assertNotIn("SCT-test-secret", str(settings))


if __name__ == "__main__":
    unittest.main()
