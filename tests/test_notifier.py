import json
import unittest
from unittest.mock import patch

from backend.notifier import _send_wecom


class WecomNotificationTests(unittest.TestCase):
    @patch("backend.notifier._request_json")
    def test_empty_message_does_not_add_extra_line(self, mock_request):
        mock_request.return_value = {"errcode": 0}
        _send_wecom("https://example.com/webhook", "（虎牙）紫皮小子开播了", "")

        request = mock_request.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["text"]["content"], "（虎牙）紫皮小子开播了")


if __name__ == "__main__":
    unittest.main()
