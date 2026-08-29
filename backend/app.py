from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from .database import Database, DuplicateStreamError
from .monitor import MonitorService
from .notifier import mask_secret
from .platforms import parse_room_reference


class LiveMonitorApp:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.frontend_dir = self.project_root / "frontend"
        self.database = Database(self.project_root / "data" / "monitor.db")
        self.monitor = MonitorService(self.database)

    def start(self) -> None:
        self.monitor.start()

    def stop(self) -> None:
        self.monitor.stop()

    def handle_api(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        query: dict[str, list[str]],
    ) -> tuple[int, dict[str, Any]]:
        body = payload or {}
        if path == "/api/health" and method == "GET":
            return 200, {"ok": True, "service": "live-room-monitor"}

        if path == "/api/streams" and method == "GET":
            return self._dashboard_payload()

        if path == "/api/streams" and method == "POST":
            return self._create_stream(body)

        if path == "/api/check-all" and method == "POST":
            self.monitor.check_all(allow_notify=True)
            return self._dashboard_payload()

        if path == "/api/settings" and method == "GET":
            return 200, {"settings": self._public_settings()}

        if path == "/api/settings" and method == "PUT":
            return self._update_settings(body)

        if path == "/api/notifications" and method == "GET":
            raw_limit = (query.get("limit") or ["8"])[0]
            try:
                limit = int(raw_limit)
            except ValueError:
                limit = 8
            return 200, {"items": self.database.list_notification_events(limit)}

        if path == "/api/notifications/test" and method == "POST":
            self.monitor.send_test_notification()
            return 200, {"message": "测试通知已发送"}

        parts = [part for part in path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["api", "streams"]:
            stream_id = self._parse_stream_id(parts[2])
            action = parts[3]
            if action == "check" and method == "POST":
                stream = self.monitor.check_stream(stream_id, allow_notify=True)
                if stream is None:
                    return 404, {"error": "直播间不存在"}
                return 200, {"stream": stream}
            return 404, {"error": "接口不存在"}

        if len(parts) == 3 and parts[:2] == ["api", "streams"]:
            stream_id = self._parse_stream_id(parts[2])
            if method == "PATCH":
                return self._update_stream(stream_id, body)
            if method == "DELETE":
                if not self.database.delete_stream(stream_id):
                    return 404, {"error": "直播间不存在"}
                return 200, {"message": "直播间已删除"}

        return 404, {"error": "接口不存在"}

    def static_file(self, path: str) -> tuple[int, str, bytes]:
        relative = path.lstrip("/") or "index.html"
        if relative == "":
            relative = "index.html"
        candidate = (self.frontend_dir / relative).resolve()
        frontend_root = self.frontend_dir.resolve()
        if frontend_root not in candidate.parents and candidate != frontend_root:
            raise ValueError("非法文件路径")
        if not candidate.is_file():
            if "." not in relative:
                candidate = self.frontend_dir / "index.html"
            else:
                raise FileNotFoundError(relative)
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or candidate.suffix in {".js", ".css"}:
            content_type = f"{content_type}; charset=utf-8"
        return 200, content_type, candidate.read_bytes()

    def _dashboard_payload(self) -> tuple[int, dict[str, Any]]:
        return 200, {
            "items": self.database.list_streams(),
            "metrics": self.database.metrics(),
            "settings": self._public_settings(),
        }

    def _create_stream(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        platform = str(body.get("platform") or "")
        room_input = str(body.get("room_url") or "")
        profile_url = str(body.get("profile_url") or "")
        display_name = str(body.get("display_name") or "").strip()[:100]
        reference = parse_room_reference(room_input, platform, profile_url)
        try:
            stream_id = self.database.add_stream(
                reference.platform,
                reference.room_key,
                reference.room_url,
                display_name,
                reference.anchor_key,
                reference.profile_url,
            )
        except DuplicateStreamError as exc:
            return 409, {"error": str(exc)}

        stream = self.monitor.check_stream(stream_id, allow_notify=False)
        return 201, {"stream": stream}

    def _update_stream(
        self, stream_id: int, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        current = self.database.get_stream(stream_id)
        if current is None:
            return 404, {"error": "直播间不存在"}
        display_name = body.get("display_name")
        if display_name is not None:
            display_name = str(display_name).strip()[:100]
        enabled = body.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("enabled 必须是布尔值")

        if "platform" in body or "room_url" in body or "profile_url" in body:
            platform = str(body.get("platform") or current["platform"])
            room_input = str(body.get("room_url") or current["room_url"]).strip()
            if not room_input:
                raise ValueError("请输入直播间链接或房间 ID")
            profile_url = str(
                body.get("profile_url")
                if "profile_url" in body
                else current.get("profile_url", "")
            )
            reference = parse_room_reference(room_input, platform, profile_url)
            try:
                stream = self.database.update_stream_reference(
                    stream_id,
                    platform=reference.platform,
                    room_key=reference.room_key,
                    room_url=reference.room_url,
                    display_name=display_name,
                    anchor_key=reference.anchor_key,
                    profile_url=reference.profile_url,
                )
            except DuplicateStreamError as exc:
                return 409, {"error": str(exc)}
            if stream is None:
                return 404, {"error": "直播间不存在"}
            stream = self.monitor.check_stream(stream_id, allow_notify=False)
            return 200, {"stream": stream}

        stream = self.database.update_stream(
            stream_id,
            display_name=display_name,
            enabled=enabled,
        )
        return 200, {"stream": stream}

    def _update_settings(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        current = self.database.get_settings()
        provider = str(body.get("notify_provider", current["notify_provider"]))
        if provider not in {"none", "serverchan", "wecom"}:
            raise ValueError("通知方式不正确")

        try:
            interval = int(body.get("monitor_interval_seconds", current["monitor_interval_seconds"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("检查间隔必须是数字") from exc
        if not 15 <= interval <= 3600:
            raise ValueError("检查间隔需要在 15 到 3600 秒之间")

        updates = {
            "monitor_interval_seconds": str(interval),
            "notify_provider": provider,
            "notify_on_start": "1"
            if self._as_bool(body.get("notify_on_start"), current["notify_on_start"] == "1")
            else "0",
            "notify_on_stop": "1"
            if self._as_bool(body.get("notify_on_stop"), current["notify_on_stop"] == "1")
            else "0",
        }
        for key in ("serverchan_sendkey", "wecom_webhook"):
            if key in body and str(body[key]).strip():
                updates[key] = str(body[key]).strip()
        settings = self.database.save_settings(updates)
        return 200, {"settings": self._public_settings(settings)}

    def _public_settings(self, settings: dict[str, str] | None = None) -> dict[str, Any]:
        settings = settings or self.database.get_settings()
        return {
            "monitor_interval_seconds": int(settings.get("monitor_interval_seconds", "60")),
            "notify_provider": settings.get("notify_provider", "none"),
            "notify_on_start": settings.get("notify_on_start", "1") == "1",
            "notify_on_stop": settings.get("notify_on_stop", "0") == "1",
            "serverchan_sendkey_set": bool(settings.get("serverchan_sendkey")),
            "serverchan_sendkey_masked": mask_secret(settings.get("serverchan_sendkey", "")),
            "wecom_webhook_set": bool(settings.get("wecom_webhook")),
            "wecom_webhook_masked": mask_secret(settings.get("wecom_webhook", "")),
        }

    @staticmethod
    def _parse_stream_id(value: str) -> int:
        try:
            stream_id = int(value)
        except ValueError as exc:
            raise ValueError("直播间 ID 不正确") from exc
        if stream_id < 1:
            raise ValueError("直播间 ID 不正确")
        return stream_id

    @staticmethod
    def _as_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
