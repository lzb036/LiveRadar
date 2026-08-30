import json
import unittest
from unittest.mock import patch

from backend.notifier import _send_wecom, _send_wxpusher


class WecomNotificationTests(unittest.TestCase):
    @patch("backend.notifier._request_json")
    def test_empty_message_does_not_add_extra_line(self, mock_request):
        mock_request.return_value = {"errcode": 0}
        _send_wecom("https://example.com/webhook", "（虎牙）紫皮小子开播了", "")

        request = mock_request.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["text"]["content"], "（虎牙）紫皮小子开播了")


class WxpusherNotificationTests(unittest.TestCase):
    @patch("backend.notifier._request_json")
    def test_spt_message_uses_simple_push_api(self, mock_request):
        mock_request.return_value = {"code": 1000, "msg": "发送成功"}
        _send_wxpusher(
            "SPT_first, SPT_second",
            "紫皮小子下播了，时长为08:52:19",
            "",
        )

        request = mock_request.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            request.full_url,
            "https://wxpusher.zjiecode.com/api/send/message/simple-push",
        )
        self.assertEqual(payload["sptList"], ["SPT_first", "SPT_second"])
        self.assertEqual(payload["content"], "紫皮小子下播了，时长为08:52:19")
        self.assertEqual(payload["summary"], "紫皮小子下播了，时长为08:52:19")
        self.assertEqual(payload["contentType"], 1)

    @patch("backend.notifier._request_json")
    def test_rejects_invalid_spt(self, mock_request):
        with self.assertRaisesRegex(Exception, "SPT 格式不正确"):
            _send_wxpusher("invalid-token", "测试", "")
        mock_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
