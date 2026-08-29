from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass

from .database import Database


SESSION_COOKIE = "liveradar_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ROUNDS = 240_000


@dataclass(frozen=True)
class InitialCredentials:
    username: str
    password: str


class AuthService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.username = (
            os.environ.get("LIVE_MONITOR_USERNAME", "liveradar").strip()
            or "liveradar"
        )
        self.configured_password = os.environ.get("LIVE_MONITOR_PASSWORD", "")
        self._sessions: dict[str, tuple[str, float]] = {}
        self._lock = threading.RLock()
        self.initial_credentials: InitialCredentials | None = None

    def bootstrap(self) -> InitialCredentials | None:
        if self.database.get_auth_user(self.username) is not None:
            return None

        password = self.configured_password or secrets.token_urlsafe(18)
        self.database.create_auth_user(self.username, hash_password(password))
        if self.configured_password:
            return None
        self.initial_credentials = InitialCredentials(self.username, password)
        return self.initial_credentials

    def verify_password(self, username: str, password: str) -> bool:
        if username != self.username or not password:
            return False
        stored_hash = self.database.get_auth_user(username)
        return bool(stored_hash and verify_password(password, stored_hash))

    def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_sessions()
            self._sessions[token] = (username, time.time() + SESSION_TTL_SECONDS)
        return token

    def current_user(self, cookie_header: str) -> str | None:
        token = parse_cookie(cookie_header).get(SESSION_COOKIE)
        if not token:
            return None
        with self._lock:
            self._purge_sessions()
            session = self._sessions.get(token)
            return session[0] if session else None

    def revoke_session(self, cookie_header: str) -> None:
        token = parse_cookie(cookie_header).get(SESSION_COOKIE)
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def session_cookie(self, token: str, *, secure: bool) -> str:
        attributes = [
            f"{SESSION_COOKIE}={token}",
            "Path=/",
            f"Max-Age={SESSION_TTL_SECONDS}",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if secure:
            attributes.append("Secure")
        return "; ".join(attributes)

    def clear_session_cookie(self, *, secure: bool) -> str:
        attributes = [
            f"{SESSION_COOKIE}=",
            "Path=/",
            "Max-Age=0",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if secure:
            attributes.append("Secure")
        return "; ".join(attributes)

    def _purge_sessions(self) -> None:
        now = time.time()
        expired = [
            token
            for token, (_, expires_at) in self._sessions.items()
            if expires_at <= now
        ]
        for token in expired:
            self._sessions.pop(token, None)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ROUNDS,
    )
    encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii")
    return f"pbkdf2_sha256${PASSWORD_ROUNDS}${encode(salt)}${encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_value, digest_value = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(rounds),
        )
    except (TypeError, ValueError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def parse_cookie(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in (header or "").split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            cookies[name] = value
    return cookies
