"""Small, process-local safeguards for the public demonstration.

Chat requests and actual SDK attempts have separate sliding windows. All model
generation, query rewriting and embedding attempts must use MeteredModels to
share the same API budget; failed attempts also consume a reservation. Callers
decide whether cached chat responses should count by placing check_chat before
or after their cache lookup.

State is held only in memory: restarting the process resets every window,
including the hourly API budget. Multiple worker processes do not share state;
use one worker or an external shared limiter for a deployment with many workers.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


class PublicLimitError(RuntimeError):
    """A safe Korean message that may be shown to a demo visitor."""


@dataclass
class _ClientWindow:
    last_accepted_at: float
    requests: deque[float] = field(default_factory=deque)


def _count(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _seconds(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


class PublicLimits:
    """Thread-safe, bounded sliding-window limits using a monotonic clock.

    Defaults: six accepted chats per client per 60 seconds, and 60 SDK call
    attempts per process per 3,600 seconds. A zero chat/API limit blocks those
    operations; max_clients=0 blocks chat requests. Zero never means unlimited.

    client_key must be a SHA-256 hexadecimal digest computed by the caller,
    preferably with a per-process secret salt; raw IP addresses are not accepted
    or stored here. Entries expire client_ttl_seconds after the last *accepted*
    chat. TTL must cover the entire chat window, so expiry cannot erase active
    quota. When storage is full, new clients are refused instead of evicting an
    active client's quota. Refused requests do not extend the TTL.

    clock must be monotonic and return elapsed seconds. Decisions and increments
    share one lock, so simultaneous requests cannot reserve beyond the limit.
    """

    def __init__(
        self,
        chat_limit: int = 6,
        chat_window_seconds: float = 60,
        api_limit: int = 60,
        api_window_seconds: float = 3600,
        max_clients: int = 1024,
        client_ttl_seconds: float = 600,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.chat_limit = _count(chat_limit, "chat_limit")
        self.api_limit = _count(api_limit, "api_limit")
        self.max_clients = _count(max_clients, "max_clients")
        self.chat_window_seconds = _seconds(chat_window_seconds, "chat_window_seconds")
        self.api_window_seconds = _seconds(api_window_seconds, "api_window_seconds")
        self.client_ttl_seconds = _seconds(client_ttl_seconds, "client_ttl_seconds")
        if self.client_ttl_seconds < self.chat_window_seconds:
            raise ValueError("client_ttl_seconds must cover chat_window_seconds")
        self._clock = clock
        self._lock = threading.Lock()
        self._clients: dict[str, _ClientWindow] = {}
        self._api_attempts: deque[float] = deque()

    @staticmethod
    def _expire_requests(requests: deque[float], now: float, window: float) -> None:
        while requests and now - requests[0] >= window:
            requests.popleft()

    def _expire_clients(self, now: float) -> None:
        expired = [
            key for key, entry in self._clients.items()
            if now - entry.last_accepted_at >= self.client_ttl_seconds
        ]
        for key in expired:
            del self._clients[key]

    def check_chat(self, client_key: str) -> None:
        """Atomically accept one chat, or raise without spending API quota."""
        if not isinstance(client_key, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", client_key):
            raise ValueError("client_key must be a SHA-256 hexadecimal digest")
        # Normalize equivalent digests so letter casing cannot split a quota.
        key = client_key.lower()
        with self._lock:
            now = self._clock()
            self._expire_clients(now)
            if self.chat_limit == 0 or self.max_clients == 0:
                raise PublicLimitError("공개 데모의 질문 접수가 잠시 중지되었습니다. 나중에 다시 이용해 주세요.")
            entry = self._clients.get(key)
            if entry is None:
                if len(self._clients) >= self.max_clients:
                    raise PublicLimitError("현재 데모 이용자가 많습니다. 잠시 후 다시 질문해 주세요.")
                entry = _ClientWindow(last_accepted_at=now)
                self._clients[key] = entry
            self._expire_requests(entry.requests, now, self.chat_window_seconds)
            if len(entry.requests) >= self.chat_limit:
                raise PublicLimitError("짧은 시간에 질문이 많습니다. 잠시 기다린 뒤 다시 질문해 주세요.")
            entry.requests.append(now)
            entry.last_accepted_at = now

    def reserve_api(self) -> None:
        """Reserve one SDK attempt before network I/O; failures are not refunded."""
        with self._lock:
            now = self._clock()
            self._expire_requests(self._api_attempts, now, self.api_window_seconds)
            if len(self._api_attempts) >= self.api_limit:
                raise PublicLimitError("공개 데모의 AI 사용 한도에 도달했습니다. 잠시 후 다시 이용해 주세요.")
            self._api_attempts.append(now)

    @property
    def tracked_clients(self) -> int:
        """Return the count of unexpired client entries without exposing keys."""
        with self._lock:
            self._expire_clients(self._clock())
            return len(self._clients)


class MeteredModels:
    """Synchronous SDK models proxy for the two API methods used by this app.

    Wrap the SDK's models object once and route every generation, rewriting and
    embedding path through this object. SDK retries inside a single method are
    outside this counter: configure the underlying SDK with one attempt if the
    quota must bound actual HTTP calls. Streaming and asynchronous SDK methods
    are intentionally not exposed.
    """

    def __init__(self, models: Any, limits: PublicLimits) -> None:
        self._models = models
        self._limits = limits

    def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        self._limits.reserve_api()
        return self._models.generate_content(*args, **kwargs)

    def embed_content(self, *args: Any, **kwargs: Any) -> Any:
        self._limits.reserve_api()
        return self._models.embed_content(*args, **kwargs)
