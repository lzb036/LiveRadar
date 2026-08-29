from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_SETTINGS = {
    "monitor_interval_seconds": "60",
    "notify_provider": "serverchan",
    "serverchan_sendkey": "",
    "wecom_webhook": "",
    "notify_on_start": "1",
    "notify_on_stop": "1",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DuplicateStreamError(ValueError):
    """Raised when the same platform room is already being monitored."""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock:
            with self._session() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS streams (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform TEXT NOT NULL,
                        room_key TEXT NOT NULL,
                        room_url TEXT NOT NULL,
                        anchor_key TEXT NOT NULL DEFAULT '',
                        profile_url TEXT NOT NULL DEFAULT '',
                        display_name TEXT NOT NULL DEFAULT '',
                        anchor_name TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        cover_url TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'unknown',
                        error_message TEXT NOT NULL DEFAULT '',
                        live_session_active INTEGER NOT NULL DEFAULT 0,
                        start_notification_sent INTEGER NOT NULL DEFAULT 0,
                        stop_notification_sent INTEGER NOT NULL DEFAULT 0,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        last_checked_at TEXT,
                        last_live_at TEXT,
                        last_offline_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(platform, room_key)
                    );

                    CREATE INDEX IF NOT EXISTS idx_streams_status
                        ON streams(status);

                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS auth_users (
                        username TEXT PRIMARY KEY,
                        password_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        token_hash TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        expires_at REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(username) REFERENCES auth_users(username)
                            ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires
                        ON auth_sessions(expires_at);

                    CREATE TABLE IF NOT EXISTS notification_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        stream_id INTEGER,
                        event_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        message TEXT NOT NULL,
                        delivered INTEGER NOT NULL DEFAULT 0,
                        error_message TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(stream_id) REFERENCES streams(id) ON DELETE SET NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_notification_events_created
                        ON notification_events(created_at DESC);
                    """
                )
                existing_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(streams)").fetchall()
                }
                for column, definition in (
                    ("anchor_key", "TEXT NOT NULL DEFAULT ''"),
                    ("profile_url", "TEXT NOT NULL DEFAULT ''"),
                    ("live_session_active", "INTEGER NOT NULL DEFAULT 0"),
                    ("start_notification_sent", "INTEGER NOT NULL DEFAULT 0"),
                    ("stop_notification_sent", "INTEGER NOT NULL DEFAULT 0"),
                ):
                    if column not in existing_columns:
                        connection.execute(
                            f"ALTER TABLE streams ADD COLUMN {column} {definition}"
                        )
                connection.execute(
                    """
                    UPDATE streams
                    SET live_session_active = 1,
                        start_notification_sent = 1,
                        stop_notification_sent = 0
                    WHERE status = 'live'
                      AND live_session_active = 0
                    """
                )
                connection.execute(
                    """
                    UPDATE streams
                    SET live_session_active = 1,
                        start_notification_sent = 1,
                        stop_notification_sent = 0
                    WHERE status = 'error'
                      AND live_session_active = 0
                      AND id IN (
                        SELECT latest.stream_id
                        FROM notification_events AS latest
                        WHERE latest.event_type = 'started'
                          AND latest.delivered = 1
                          AND latest.id = (
                            SELECT MAX(candidate.id)
                            FROM notification_events AS candidate
                            WHERE candidate.stream_id = latest.stream_id
                              AND candidate.event_type = 'started'
                              AND candidate.delivered = 1
                          )
                          AND NOT EXISTS (
                            SELECT 1
                            FROM notification_events AS stopped
                            WHERE stopped.stream_id = latest.stream_id
                              AND stopped.event_type = 'stopped'
                              AND stopped.delivered = 1
                              AND stopped.id > latest.id
                          )
                    )
                    """
                )
                for key, value in DEFAULT_SETTINGS.items():
                    connection.execute(
                        "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                        (key, value),
                    )

    def create_auth_session(
        self,
        token_hash: str,
        username: str,
        expires_at: float,
    ) -> None:
        with self._lock:
            with self._session() as connection:
                connection.execute(
                    """
                    INSERT INTO auth_sessions(
                        token_hash, username, expires_at, created_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (token_hash, username, expires_at, utc_now()),
                )

    def get_auth_session(
        self,
        token_hash: str,
        now: float,
    ) -> str | None:
        with self._lock:
            with self._session() as connection:
                row = connection.execute(
                    """
                    SELECT username, expires_at
                    FROM auth_sessions
                    WHERE token_hash = ?
                    """,
                    (token_hash,),
                ).fetchone()
                if row is None:
                    return None
                if float(row["expires_at"]) <= now:
                    connection.execute(
                        "DELETE FROM auth_sessions WHERE token_hash = ?",
                        (token_hash,),
                    )
                    return None
                return str(row["username"])

    def delete_auth_session(self, token_hash: str) -> None:
        with self._lock:
            with self._session() as connection:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?",
                    (token_hash,),
                )

    def purge_expired_auth_sessions(self, now: float) -> None:
        with self._lock:
            with self._session() as connection:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE expires_at <= ?",
                    (now,),
                )

    def get_auth_user(self, username: str) -> str | None:
        with self._lock:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT password_hash FROM auth_users WHERE username = ?",
                    (username,),
                ).fetchone()
                return str(row["password_hash"]) if row else None

    def create_auth_user(self, username: str, password_hash: str) -> None:
        now = utc_now()
        with self._lock:
            with self._session() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO auth_users(
                        username, password_hash, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (username, password_hash, now, now),
                )

    @staticmethod
    def _serialize_stream(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        return item

    def list_streams(self) -> list[dict[str, Any]]:
        with self._lock:
            with self._session() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM streams
                    ORDER BY
                        CASE status
                            WHEN 'live' THEN 0
                            WHEN 'error' THEN 1
                            WHEN 'unknown' THEN 2
                            ELSE 3
                        END,
                        created_at DESC
                    """
                ).fetchall()
                return [self._serialize_stream(row) for row in rows]

    def list_streams_page(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status_filter: str = "all",
        search_query: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        clauses: list[str] = []
        params: list[Any] = []

        if status_filter == "live":
            clauses.append("enabled = 1 AND status = 'live'")
        elif status_filter == "offline":
            clauses.append("enabled = 1 AND status IN ('offline', 'replay')")
        elif status_filter == "attention":
            clauses.append(
                "(enabled = 0 OR status IN ('error', 'unknown'))"
            )

        search_query = (search_query or "").strip()[:100]
        if search_query:
            pattern = f"%{search_query}%"
            clauses.append(
                """
                (
                    display_name LIKE ? COLLATE NOCASE
                    OR anchor_name LIKE ? COLLATE NOCASE
                    OR title LIKE ? COLLATE NOCASE
                    OR room_key LIKE ? COLLATE NOCASE
                    OR platform LIKE ? COLLATE NOCASE
                )
                """
            )
            params.extend([pattern] * 5)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            with self._session() as connection:
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) AS total FROM streams {where}",
                        params,
                    ).fetchone()["total"]
                    or 0
                )
                offset = (page - 1) * page_size
                rows = connection.execute(
                    f"""
                    SELECT *
                    FROM streams
                    {where}
                    ORDER BY
                        CASE status
                            WHEN 'live' THEN 0
                            WHEN 'error' THEN 1
                            WHEN 'unknown' THEN 2
                            ELSE 3
                        END,
                        created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, page_size, offset],
                ).fetchall()
                items = [self._serialize_stream(row) for row in rows]

        return items, {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    def get_stream(self, stream_id: int) -> dict[str, Any] | None:
        with self._lock:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT * FROM streams WHERE id = ?", (stream_id,)
                ).fetchone()
                return self._serialize_stream(row) if row else None

    def add_stream(
        self,
        platform: str,
        room_key: str,
        room_url: str,
        display_name: str,
        anchor_key: str = "",
        profile_url: str = "",
    ) -> int:
        now = utc_now()
        with self._lock:
            try:
                with self._session() as connection:
                    cursor = connection.execute(
                        """
                        INSERT INTO streams(
                            platform, room_key, room_url, anchor_key, profile_url,
                            display_name,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            platform,
                            room_key,
                            room_url,
                            anchor_key,
                            profile_url,
                            display_name,
                            now,
                            now,
                        ),
                    )
                    return int(cursor.lastrowid)
            except sqlite3.IntegrityError as exc:
                raise DuplicateStreamError("这个平台的直播间已经添加过了") from exc

    def update_stream(
        self,
        stream_id: int,
        *,
        display_name: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        changes: list[str] = []
        values: list[Any] = []
        if display_name is not None:
            changes.append("display_name = ?")
            values.append(display_name)
        if enabled is not None:
            changes.append("enabled = ?")
            values.append(int(enabled))
        if not changes:
            return self.get_stream(stream_id)

        changes.append("updated_at = ?")
        values.append(utc_now())
        values.append(stream_id)
        with self._lock:
            with self._session() as connection:
                connection.execute(
                    f"UPDATE streams SET {', '.join(changes)} WHERE id = ?",
                    values,
                )
        return self.get_stream(stream_id)

    def update_stream_reference(
        self,
        stream_id: int,
        *,
        platform: str,
        room_key: str,
        room_url: str,
        display_name: str | None = None,
        anchor_key: str = "",
        profile_url: str = "",
    ) -> dict[str, Any] | None:
        with self._lock:
            try:
                with self._session() as connection:
                    current = connection.execute(
                        "SELECT display_name FROM streams WHERE id = ?",
                        (stream_id,),
                    ).fetchone()
                    if current is None:
                        return None
                    name = (
                        current["display_name"]
                        if display_name is None
                        else display_name
                    )
                    connection.execute(
                        """
                        UPDATE streams
                        SET platform = ?,
                            room_key = ?,
                            room_url = ?,
                            anchor_key = ?,
                            profile_url = ?,
                            display_name = ?,
                            anchor_name = '',
                            title = '',
                            cover_url = '',
                            status = 'unknown',
                            error_message = '',
                            live_session_active = 0,
                            start_notification_sent = 0,
                            stop_notification_sent = 0,
                            last_checked_at = NULL,
                            last_live_at = NULL,
                            last_offline_at = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            platform,
                            room_key,
                            room_url,
                            anchor_key,
                            profile_url,
                            name,
                            utc_now(),
                            stream_id,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise DuplicateStreamError("这个平台的直播间已经添加过了") from exc
        return self.get_stream(stream_id)

    def update_notification_state(
        self,
        stream_id: int,
        *,
        live_session_active: bool | None = None,
        start_notification_sent: bool | None = None,
        stop_notification_sent: bool | None = None,
    ) -> dict[str, Any] | None:
        changes: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("live_session_active", live_session_active),
            ("start_notification_sent", start_notification_sent),
            ("stop_notification_sent", stop_notification_sent),
        ):
            if value is not None:
                changes.append(f"{column} = ?")
                values.append(int(value))
        if not changes:
            return self.get_stream(stream_id)

        changes.append("updated_at = ?")
        values.append(utc_now())
        values.append(stream_id)
        with self._lock:
            with self._session() as connection:
                connection.execute(
                    f"UPDATE streams SET {', '.join(changes)} WHERE id = ?",
                    values,
                )
        return self.get_stream(stream_id)

    def delete_stream(self, stream_id: int) -> bool:
        with self._lock:
            with self._session() as connection:
                cursor = connection.execute(
                    "DELETE FROM streams WHERE id = ?", (stream_id,)
                )
                return cursor.rowcount > 0

    def record_check(
        self,
        stream_id: int,
        *,
        status: str,
        anchor_name: str = "",
        title: str = "",
        cover_url: str = "",
        error_message: str = "",
        anchor_key: str | None = None,
        profile_url: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        now = utc_now()
        with self._lock:
            with self._session() as connection:
                before = connection.execute(
                    """
                    SELECT
                        status,
                        last_live_at,
                        last_offline_at,
                        anchor_key,
                        profile_url
                    FROM streams
                    WHERE id = ?
                    """,
                    (stream_id,),
                ).fetchone()
                if before is None:
                    return None, "unknown"

                last_live_at = before["last_live_at"]
                last_offline_at = before["last_offline_at"]
                stored_anchor_key = (
                    before["anchor_key"] if anchor_key is None else anchor_key
                )
                stored_profile_url = (
                    before["profile_url"] if profile_url is None else profile_url
                )
                if status == "live":
                    last_live_at = now
                elif status in {"offline", "replay"}:
                    last_offline_at = now

                connection.execute(
                    """
                    UPDATE streams
                    SET status = ?,
                        anchor_name = ?,
                        title = ?,
                        cover_url = ?,
                        anchor_key = ?,
                        profile_url = ?,
                        error_message = ?,
                        last_checked_at = ?,
                        last_live_at = ?,
                        last_offline_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        anchor_name,
                        title,
                        cover_url,
                        stored_anchor_key,
                        stored_profile_url,
                        error_message,
                        now,
                        last_live_at,
                        last_offline_at,
                        now,
                        stream_id,
                    ),
                )
                previous_status = str(before["status"])
        return self.get_stream(stream_id), previous_status

    def get_settings(self) -> dict[str, str]:
        with self._lock:
            with self._session() as connection:
                rows = connection.execute("SELECT key, value FROM settings").fetchall()
                settings = dict(DEFAULT_SETTINGS)
                settings.update({row["key"]: row["value"] for row in rows})
                return settings

    def save_settings(self, updates: dict[str, str]) -> dict[str, str]:
        with self._lock:
            with self._session() as connection:
                for key, value in updates.items():
                    connection.execute(
                        """
                        INSERT INTO settings(key, value)
                        VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (key, value),
                    )
        return self.get_settings()

    def record_notification(
        self,
        stream_id: int | None,
        *,
        event_type: str,
        title: str,
        message: str,
        delivered: bool,
        error_message: str = "",
    ) -> None:
        with self._lock:
            with self._session() as connection:
                connection.execute(
                    """
                    INSERT INTO notification_events(
                        stream_id, event_type, title, message,
                        delivered, error_message, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stream_id,
                        event_type,
                        title,
                        message,
                        int(delivered),
                        error_message,
                        utc_now(),
                    ),
                )

    def list_notification_events(self, limit: int = 8) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 50))
        with self._lock:
            with self._session() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        events.*,
                        streams.display_name,
                        streams.anchor_name,
                        streams.platform,
                        streams.room_key
                    FROM notification_events AS events
                    LEFT JOIN streams ON streams.id = events.stream_id
                    ORDER BY events.created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    item["delivered"] = bool(item["delivered"])
                    items.append(item)
                return items

    def list_notification_events_page(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        with self._lock:
            with self._session() as connection:
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) AS total FROM notification_events"
                    ).fetchone()["total"]
                    or 0
                )
                rows = connection.execute(
                    """
                    SELECT
                        events.*,
                        streams.display_name,
                        streams.anchor_name,
                        streams.platform,
                        streams.room_key
                    FROM notification_events AS events
                    LEFT JOIN streams ON streams.id = events.stream_id
                    ORDER BY events.created_at DESC, events.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (page_size, (page - 1) * page_size),
                ).fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    item["delivered"] = bool(item["delivered"])
                    items.append(item)

        return items, {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            with self._session() as connection:
                counts = connection.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled,
                        SUM(CASE WHEN enabled = 1 AND status = 'live' THEN 1 ELSE 0 END) AS live,
                        SUM(CASE WHEN enabled = 1 AND status = 'offline' THEN 1 ELSE 0 END) AS offline,
                        SUM(CASE WHEN enabled = 1 AND status = 'error' THEN 1 ELSE 0 END) AS errors,
                        SUM(CASE WHEN enabled = 1 AND status = 'unknown' THEN 1 ELSE 0 END) AS unknown
                    FROM streams
                    """
                ).fetchone()
                latest = connection.execute(
                    "SELECT MAX(last_checked_at) AS last_checked_at FROM streams"
                ).fetchone()
                return {
                    "total": int(counts["total"] or 0),
                    "enabled": int(counts["enabled"] or 0),
                    "live": int(counts["live"] or 0),
                    "offline": int(counts["offline"] or 0),
                    "errors": int(counts["errors"] or 0),
                    "unknown": int(counts["unknown"] or 0),
                    "last_checked_at": latest["last_checked_at"],
                }
