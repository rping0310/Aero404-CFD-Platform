"""Small in-memory, cookie-backed session store for this internal service."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

from .config import settings


@dataclass
class Session:
    name: str
    role: str
    expires_at: float


class SessionStore:
    def __init__(self):
        self._items: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, name: str, role: str) -> str:
        timeout = int(settings.get_app_settings().get("session_timeout_min", 480)) * 60
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._items[token] = Session(name, role, time.time() + timeout)
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            session = self._items.get(token)
            if not session or session.expires_at <= time.time():
                self._items.pop(token, None)
                return None
            return session

    def delete(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._items.pop(token, None)


sessions = SessionStore()
