"""Thread-safe in-memory cache with a time-to-live."""

from __future__ import annotations

import time
from threading import Lock


class TTLCache:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[float, str]] = {}
        self._lock = Lock()

    def get(self, key: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            item = self._items.get(key)
            return item[1] if item is not None else None

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._purge(time.monotonic())
            self._items[key] = (time.monotonic() + self.ttl_seconds, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _purge(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
        for key in expired:
            self._items.pop(key, None)
