"""A bounded, thread-safe TTL/LRU cache with isolated result objects."""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from typing import Callable, Generic, TypeVar


Value = TypeVar("Value")


class TTLCache(Generic[Value]):
    """Expire from insertion time; reads update LRU but do not extend TTL.

    max_size=0 or ttl_seconds=0 disables caching. Values are deep-copied on
    both insertion and retrieval so response mutation cannot affect another
    request. The clock is injectable for deterministic expiration tests.
    """

    def __init__(
        self,
        max_size: int = 128,
        ttl_seconds: float = 1800,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_size = max(0, int(max_size))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, Value]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> Value | None:
        if not key or not self.max_size or not self.ttl_seconds:
            return None
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            stored_at, value = item
            if self._clock() - stored_at >= self.ttl_seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return copy.deepcopy(value)

    def put(self, key: str, value: Value) -> None:
        if not key or not self.max_size or not self.ttl_seconds:
            return
        with self._lock:
            now = self._clock()
            expired = [
                entry_key for entry_key, (stored_at, _) in self._entries.items()
                if now - stored_at >= self.ttl_seconds
            ]
            for entry_key in expired:
                del self._entries[entry_key]
            self._entries.pop(key, None)
            while len(self._entries) >= self.max_size:
                self._entries.popitem(last=False)
            self._entries[key] = (now, copy.deepcopy(value))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
