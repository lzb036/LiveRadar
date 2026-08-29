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
                was_checked = bool(stream.get("last_checked_at"))
                updated, previous_status = self.database.record_check(
                    stream_id,
                    status=snapshot.status,
                    anchor_name=snapshot.anchor_name,
                    title=snapshot.title,
                    cover_url=snapshot.cover_url,
                    anchor_key=snapshot.anchor_key or None,
                    profile_url=snapshot.profile_url or None,
                )
                if updated:
                    self._process_notification_state(
                        stream=stream,
                        updated=updated,
                        previous_status=previous_status,
                        snapshot=snapshot,
                        was_checked=was_checked,
                        allow_notify=allow_notify,
                    )
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

    def _process_notification_state(
        self,
        *,
        stream: dict[str, Any],
        updated: dict[str, Any],
        previous_status: str,
        snapshot: RoomSnapshot,
        was_checked: bool,
        allow_notify: bool,
    ) -> None:
        settings = self.database.get_settings()
        session_active = bool(stream.get("live_session_active"))
        start_sent = bool(stream.get("start_notification_sent"))
        stop_sent = bool(stream.get("stop_notification_sent"))

        if snapshot.status == "live":
            should_start = (
                not session_active
                and allow_notify
                and was_checked
                and previous_status in {"offline", "replay"}
            )
            if not session_active:
                self.database.update_notification_state(
                    stream["id"],
                    live_session_active=True,
                    start_notification_sent=not should_start,
                    stop_notification_sent=False,
                )
                session_active = True
                start_sent = not should_start
                stop_sent = False

            if (
                session_active
                and not start_sent
                and allow_notify
                and settings.get("notify_on_start") == "1"
            ):
                delivered = self._deliver_transition(
                    updated,
                    snapshot,
                    event_type="started",
                    title=self._notification_text(
                        updated,
                        snapshot,
                        event_type="started",
                    ),
                    message="",
                )
                if delivered:
                    self.database.update_notification_state(
                        stream["id"],
                        start_notification_sent=True,
                    )
            return

        if snapshot.status not in {"offline", "replay"} or not session_active:
            return

        if not allow_notify:
            self.database.update_notification_state(
                stream["id"],
                live_session_active=False,
                start_notification_sent=False,
                stop_notification_sent=False,
            )
            return

        if settings.get("notify_on_stop") == "1" and not stop_sent:
            delivered = self._deliver_transition(
                updated,
                snapshot,
                event_type="stopped",
                title=self._notification_text(
                    updated,
                    snapshot,
                    event_type="stopped",
                ),
                message="",
            )
            if not delivered:
                return

        self.database.update_notification_state(
            stream["id"],
            live_session_active=False,
            start_notification_sent=False,
            stop_notification_sent=False,
        )

    def _deliver_transition(
        self,
        stream: dict[str, Any],
        snapshot: RoomSnapshot,
        *,
        event_type: str,
        title: str,
        message: str,
    ) -> bool:
        try:
            send_notification(self.database.get_settings(), title, message)
        except NotificationError as exc:
            self.database.record_notification(
                stream["id"],
                event_type=event_type,
                title=title,
                message=message,
                delivered=False,
                error_message=str(exc),
            )
            return False

        self.database.record_notification(
            stream["id"],
            event_type=event_type,
            title=title,
            message=message,
            delivered=True,
        )
        return True

    @staticmethod
    def _stream_name(
        stream: dict[str, Any],
        snapshot: RoomSnapshot | None = None,
    ) -> str:
        return (
            str(stream.get("display_name") or "").strip()
            or str(stream.get("anchor_name") or "").strip()
            or str(snapshot.anchor_name if snapshot else "").strip()
            or f'{PLATFORM_LABELS.get(stream["platform"], stream["platform"])} {stream["room_key"]}'
        )

    @staticmethod
    def _notification_text(
        stream: dict[str, Any],
        snapshot: RoomSnapshot,
        *,
        event_type: str,
    ) -> str:
        name = MonitorService._stream_name(stream, snapshot)
        if event_type == "started":
            platform = PLATFORM_LABELS.get(stream["platform"], stream["platform"])
            title = f"（{platform}）{name}开播了"
            live_title = str(snapshot.title or "").strip()
            return f"{title}：{live_title}" if live_title else title
        if event_type == "stopped":
            return f"{name}下播了"
        raise ValueError(f"不支持的通知事件：{event_type}")
