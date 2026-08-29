from __future__ import annotations

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend.app import LiveMonitorApp
from backend.notifier import NotificationError


PROJECT_ROOT = Path(__file__).resolve().parent
APP = LiveMonitorApp(PROJECT_ROOT)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "LiveRoomMonitor/1.0"

    def _send_json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_static(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 1_048_576:
            raise ValueError("请求内容过大")
        raw = self.rfile.read(content_length) if content_length else b"{}"
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是 JSON 对象")
        return payload

    def _handle_api(self, method: str) -> None:
        parsed = urlsplit(self.path)
        try:
            payload = self._read_json() if method in {"POST", "PUT", "PATCH"} else None
            status, body = APP.handle_api(method, parsed.path, payload, parse_qs(parsed.query))
            self._send_json(status, body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "请求 JSON 格式不正确"})
        except NotificationError as exc:
            self._send_json(400, {"error": str(exc)})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            print(f"[api-error] {exc}", file=sys.stderr)
            self._send_json(500, {"error": "服务器内部错误"})

    def _handle_request(self, method: str) -> None:
        if urlsplit(self.path).path.startswith("/api/"):
            self._handle_api(method)
            return

        if method != "GET":
            self._send_json(405, {"error": "不支持的请求方法"})
            return

        try:
            status, content_type, payload = APP.static_file(urlsplit(self.path).path)
            self._send_static(status, content_type, payload)
        except FileNotFoundError:
            self._send_json(404, {"error": "页面不存在"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})

    def do_GET(self) -> None:
        self._handle_request("GET")

    def do_POST(self) -> None:
        self._handle_request("POST")

    def do_PUT(self) -> None:
        self._handle_request("PUT")

    def do_PATCH(self) -> None:
        self._handle_request("PATCH")

    def do_DELETE(self) -> None:
        self._handle_request("DELETE")

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"[http] {self.address_string()} - {format % args}\n")


def main() -> None:
    host = os.environ.get("LIVE_MONITOR_HOST", "127.0.0.1")
    port = int(os.environ.get("LIVE_MONITOR_PORT", "8765"))
    httpd = ThreadingHTTPServer((host, port), RequestHandler)
    APP.start()
    print(f"直播间监测已启动：http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        APP.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
