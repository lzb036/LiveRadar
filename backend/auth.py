from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
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
        now = time.time()
        self.database.purge_expired_auth_sessions(now)
        self.database.create_auth_session(
            hash_session_token(token),
            username,
            now + SESSION_TTL_SECONDS,
        )
        return token

    def current_user(self, cookie_header: str) -> str | None:
        token = parse_cookie(cookie_header).get(SESSION_COOKIE)
        if not token:
            return None
        username = self.database.get_auth_session(
            hash_session_token(token),
            time.time(),
        )
        return username if username == self.username else None

    def revoke_session(self, cookie_header: str) -> None:
        token = parse_cookie(cookie_header).get(SESSION_COOKIE)
        if token:
            self.database.delete_auth_session(hash_session_token(token))

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


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_cookie(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in (header or "").split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            cookies[name] = value
    return cookies
