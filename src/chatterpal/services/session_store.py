"""Bounded legacy session ownership; provider calls never hold the registry lock."""

from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Any, Callable, Iterator

from ..core.llm.base import LLMError


class SessionStore:
    """Pin sessions before waiting for their turn lock so cleanup cannot detach them."""

    def __init__(self, max_sessions: int, timeout: float):
        self.max_sessions = max_sessions
        self.timeout = timeout
        self.sessions: dict[str, Any] = {}
        self._users: dict[str, int] = {}
        self._lock = RLock()
        self._expired = 0
        self._created = 0
        self._evicted = 0
        self._rejected = 0

    def create(self, factory: Callable[[], Any], session_id: str | None = None) -> Any:
        with self._lock:
            self.cleanup()
            if session_id is not None and session_id in self.sessions:
                return self.sessions[session_id]
            if len(self.sessions) >= self.max_sessions:
                for idle in sorted(
                    self.sessions.values(), key=lambda item: item.last_activity
                ):
                    if self._users.get(idle.session_id, 0) or not idle.lock.acquire(
                        blocking=False
                    ):
                        continue
                    try:
                        del self.sessions[idle.session_id]
                        self._evicted += 1
                        break
                    finally:
                        idle.lock.release()
                else:
                    self._rejected += 1
                    raise LLMError(
                        "Chat session capacity reached; retry after an idle session expires"
                    )
            session = factory()
            self.sessions[session.session_id] = session
            self._created += 1
            return session

    def get(self, session_id: str) -> Any:
        with self._lock:
            self.cleanup()
            return self.sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if self._users.get(session_id, 0):
                return False
            return self.sessions.pop(session_id, None) is not None

    def clear(self) -> bool:
        with self._lock:
            for session_id in list(self.sessions):
                self.delete(session_id)
            return not self.sessions

    def cleanup(self) -> int:
        with self._lock:
            now = datetime.now()
            removed = 0
            for session_id, session in list(self.sessions.items()):
                if self._users.get(session_id, 0) or not session.lock.acquire(
                    blocking=False
                ):
                    continue
                try:
                    if (now - session.last_activity).total_seconds() > self.timeout:
                        del self.sessions[session_id]
                        removed += 1
                finally:
                    session.lock.release()
            self._expired += removed
            return removed

    @contextmanager
    def lease(
        self, session_id: str | None, factory: Callable[[], Any]
    ) -> Iterator[Any]:
        with self._lock:
            self.cleanup()
            session = self.sessions.get(session_id)
            if session is None:
                session = self.create(factory)
            session_id = session.session_id
            self._users[session_id] = self._users.get(session_id, 0) + 1
        try:
            yield session
        finally:
            # Do not acquire a turn lock under the registry lock. Cleanup uses a
            # nonblocking turn lock and every waiting turn already owns a pin.
            with session.lock:
                session.last_activity = datetime.now()
            with self._lock:
                remaining = self._users[session_id] - 1
                if remaining:
                    self._users[session_id] = remaining
                else:
                    del self._users[session_id]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sessions": len(self.sessions),
                "active_sessions": len(self._users),
                "session_leases": sum(self._users.values()),
                "max_sessions": self.max_sessions,
                "expired_sessions": self._expired,
                "created_sessions": self._created,
                "evicted_sessions": self._evicted,
                "rejected_sessions": self._rejected,
            }
