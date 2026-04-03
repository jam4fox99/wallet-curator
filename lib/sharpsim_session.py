from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SharpsimSession:
    session_id: str
    payload: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)


class SharpsimSessionManager:
    def __init__(self, ttl_seconds: int = 1800, max_sessions: int = 12):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._lock = threading.RLock()
        self._sessions: dict[str, SharpsimSession] = {}

    def create_session(self, payload: dict[str, Any]) -> str:
        with self._lock:
            self._evict_locked()
            session_id = uuid.uuid4().hex
            self._sessions[session_id] = SharpsimSession(session_id=session_id, payload=payload)
            return session_id

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._evict_locked()
            session = self._sessions.get(session_id)
            if not session:
                return None
            session.last_accessed = time.time()
            return session.payload

    def get_wallet_meta(self, session_id: str, wallet: str) -> dict[str, Any] | None:
        payload = self.get_session(session_id)
        if not payload:
            return None
        return payload.get("wallets", {}).get(wallet.strip().lower())

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _evict_locked(self) -> None:
        now = time.time()
        stale_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_accessed > self.ttl_seconds
        ]
        for session_id in stale_ids:
            self._sessions.pop(session_id, None)
        if len(self._sessions) <= self.max_sessions:
            return
        oldest_sessions = sorted(self._sessions.values(), key=lambda session: session.last_accessed)
        for session in oldest_sessions[: len(self._sessions) - self.max_sessions]:
            self._sessions.pop(session.session_id, None)


_MANAGER: SharpsimSessionManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_sharpsim_session_manager() -> SharpsimSessionManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = SharpsimSessionManager()
    return _MANAGER
