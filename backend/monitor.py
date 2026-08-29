from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from .database import Database
from .notifier import NotificationError, send_notification
from .platforms import PlatformError, RoomReference, RoomSnapshot, fetch_room


PLATFORM_LABELS = {
    "bilibili": "Bilibili",
    "huya": "虎牙",
    "douyin": "抖音",
}


class MonitorService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._stop_event = threading.Event()
        self._run_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="live-room-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run_loop(self) -> None:
        self.check_all(allow_notify=False)
        while not self._stop_event.is_set():
            settings = self.database.get_settings()
            try:
                interval = max(15, min(3600, int(settings["monitor_interval_seconds"])))
            except (KeyError, ValueError):
                interval = 60
            if self._stop_event.wait(interval):
                break
            self.check_all(allow_notify=True)

    def check_all(self, *, allow_notify: bool) -> list[dict[str, Any]]:
        results = []
        for stream in self.database.list_streams():
            if self._stop_event.is_set():
                break
            if not stream["enabled"]:
                continue
            result = self.check_stream(stream["id"], allow_notify=allow_notify)
            if result:
                results.append(result)
        return results

    def check_stream(
        self, stream_id: int, *, allow_notify: bool = True
    ) -> dict[str, Any] | None:
        stream = self.database.get_stream(stream_id)
        if stream is None:
            return None
        if not stream["enabled"]:
            return stream

        with self._run_lock:
            try:
                reference = RoomReference(
                    platform=stream["platform"],
                    room_key=stream["room_key"],
                    room_url=stream["room_url"],
                    anchor_key=stream.get("anchor_key", ""),
                    profile_url=stream.get("profile_url", ""),
                )
                snapshot = fetch_room(reference)
                updated, previous_status = self.database.record_check(
                    stream_id,
                    status=snapshot.status,
                    anchor_name=snapshot.anchor_name,
                    title=snapshot.title,
                    cover_url=snapshot.cover_url,
                    anchor_key=snapshot.anchor_key or None,
                    profile_url=snapshot.profile_url or None,
                )
                if (
                    updated
                    and allow_notify
                    and previous_status != snapshot.status
                ):
                    self._notify_transition(updated, previous_status, snapshot)
                return updated
            except (PlatformError, ValueError, TypeError) as exc:
                updated, _ = self.database.record_check(
                    stream_id,
                    status="error",
                    error_message=str(exc),
                )
                return updated
            except Exception as exc:
                updated, _ = self.database.record_check(
                    stream_id,
                    status="error",
                    error_message=f"未知错误：{exc}",
                )
                return updated

    def send_test_notification(self) -> None:
        settings = self.database.get_settings()
        title = "直播间监测测试通知"
        message = f"测试发送成功。时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        send_notification(settings, title, message)

    def _notify_transition(
        self,
        stream: dict[str, Any],
        previous_status: str,
        snapshot: RoomSnapshot,
    ) -> None:
        settings = self.database.get_settings()
        started = snapshot.status == "live" and previous_status in {
            "offline",
            "replay",
            "error",
        }
        stopped = (
            previous_status == "live"
            and snapshot.status in {"offline", "replay"}
        )
        if started and settings.get("notify_on_start") == "1":
            event_type = "started"
            title = f"{self._stream_name(stream)} 开播了"
            message = self._message_for(stream, snapshot, "已检测到直播开始")
        elif stopped and settings.get("notify_on_stop") == "1":
            event_type = "stopped"
            title = f"{self._stream_name(stream)} 已下播"
            message = self._message_for(stream, snapshot, "直播状态已结束")
        else:
            return

        try:
            send_notification(settings, title, message)
        except NotificationError as exc:
            self.database.record_notification(
                stream["id"],
                event_type=event_type,
                title=title,
                message=message,
                delivered=False,
                error_message=str(exc),
            )
        else:
            self.database.record_notification(
                stream["id"],
                event_type=event_type,
                title=title,
                message=message,
                delivered=True,
            )

    @staticmethod
    def _stream_name(stream: dict[str, Any]) -> str:
        return (
            stream["display_name"]
            or stream["anchor_name"]
            or f'{PLATFORM_LABELS.get(stream["platform"], stream["platform"])} {stream["room_key"]}'
        )

    @staticmethod
    def _message_for(
        stream: dict[str, Any],
        snapshot: RoomSnapshot,
        status_text: str,
    ) -> str:
        platform = PLATFORM_LABELS.get(stream["platform"], stream["platform"])
        lines = [
            status_text,
            f"平台：{platform}",
            f"直播间：{stream['room_url']}",
        ]
        if snapshot.title:
            lines.append(f"标题：{snapshot.title}")
        return "\n".join(lines)
