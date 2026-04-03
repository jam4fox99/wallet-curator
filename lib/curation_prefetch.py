import heapq
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from lib.clickhouse_charts import (
    CURATION_ALL_RANGE,
    build_wallet_curation_payload_from_base,
    get_wallet_curation_base_data,
    normalize_curation_range_key,
)

logger = logging.getLogger(__name__)

WalletBaseKey = tuple[str, str, str]
WalletDerivedKey = tuple[str, str, str, str]
WalletConfig = dict[str, str]


@dataclass
class CacheEntry:
    key: WalletBaseKey
    session_id: str | None = None
    status: str = "queued"
    priority: int = 99
    payload: dict[str, Any] | None = None
    error: str | None = None
    updated_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)


class CurationPrefetchManager:
    def __init__(self, max_workers: int = 4, ttl_seconds: int = 1800, max_ready_entries: int = 300):
        self.max_workers = max_workers
        self.ttl_seconds = ttl_seconds
        self.max_ready_entries = max_ready_entries
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="cur-prefetch")
        self._cache: dict[WalletBaseKey, CacheEntry] = {}
        self._derived_cache: dict[WalletDerivedKey, dict[str, Any] | None] = {}
        self._queue: list[tuple[int, int, WalletBaseKey]] = []
        self._inflight: dict[WalletBaseKey, Future] = {}
        self._sessions: dict[str, list[WalletBaseKey]] = {}
        self._session_last_access: dict[str, float] = {}
        self._seq = 0

    @staticmethod
    def make_base_key(wallet: str, filter_level: str, filter_value: str) -> WalletBaseKey:
        return (
            wallet.strip().lower(),
            str(filter_level or "detail"),
            str(filter_value or ""),
        )

    def prime_session(
        self,
        session_id: str,
        wallet_configs: list[WalletConfig],
        warm_count: int = 6,
    ) -> None:
        keys = [
            self.make_base_key(
                config.get("address", ""),
                config.get("filter_level", "detail"),
                config.get("filter_value", ""),
            )
            for config in wallet_configs
            if config.get("address")
        ]
        with self._lock:
            self._sessions[session_id] = keys
            self._session_last_access[session_id] = time.time()
            if not keys:
                return
            for idx, key in enumerate(keys):
                priority = 0 if idx == 0 else (1 if idx < warm_count else 2)
                self._enqueue_locked(key, session_id=session_id, priority=priority)
            self._dispatch_locked()

    def warm_session_index(self, session_id: str, index: int, warm_count: int = 6) -> None:
        with self._lock:
            keys = self._sessions.get(session_id) or []
            self._session_last_access[session_id] = time.time()
            if not keys:
                return
            end = min(len(keys), max(index + warm_count, index + 1))
            for pos in range(index, end):
                if pos < 0:
                    continue
                priority = 0 if pos == index else 1
                self._enqueue_locked(keys[pos], session_id=session_id, priority=priority)
            self._dispatch_locked()

    @staticmethod
    def make_payload_key(base_key: WalletBaseKey, range_key: Any) -> WalletDerivedKey:
        wallet, filter_level, filter_value = base_key
        return (wallet, filter_level, filter_value, normalize_curation_range_key(range_key))

    def get_payload(self, base_key: WalletBaseKey, range_key: Any = CURATION_ALL_RANGE) -> dict[str, Any] | None:
        payload_key = self.make_payload_key(base_key, range_key)
        with self._lock:
            entry = self._cache.get(base_key)
            if not entry:
                return None
            entry.last_accessed = time.time()
            if entry.status != "ready" or not entry.payload:
                return None
            if payload_key in self._derived_cache:
                return self._derived_cache[payload_key]
            base_payload = entry.payload

        derived_payload = build_wallet_curation_payload_from_base(base_payload, payload_key[-1])
        with self._lock:
            entry = self._cache.get(base_key)
            if entry:
                entry.last_accessed = time.time()
            self._derived_cache[payload_key] = derived_payload
            return derived_payload

    def get_base_payload(self, base_key: WalletBaseKey) -> dict[str, Any] | None:
        with self._lock:
            entry = self._cache.get(base_key)
            if not entry or entry.status != "ready":
                return None
            entry.last_accessed = time.time()
            return entry.payload

    def get_status(self, key: WalletBaseKey) -> str:
        with self._lock:
            entry = self._cache.get(key)
            return entry.status if entry else "missing"

    def get_error(self, key: WalletBaseKey) -> str | None:
        with self._lock:
            entry = self._cache.get(key)
            return entry.error if entry else None

    def _drop_derived_payloads_locked(self, base_key: WalletBaseKey) -> None:
        keys_to_drop = [key for key in self._derived_cache if key[:3] == base_key]
        for key in keys_to_drop:
            self._derived_cache.pop(key, None)

    def _enqueue_locked(self, key: WalletBaseKey, session_id: str, priority: int) -> None:
        now = time.time()
        entry = self._cache.get(key)
        if entry and entry.status == "ready":
            entry.last_accessed = now
            entry.session_id = session_id
            return None
        if entry and entry.status == "running":
            entry.last_accessed = now
            entry.session_id = session_id
            return
        if entry and entry.status == "queued" and priority >= entry.priority:
            entry.last_accessed = now
            entry.session_id = session_id
            return

        if not entry:
            entry = CacheEntry(key=key, session_id=session_id, status="queued", priority=priority)
            self._cache[key] = entry
        else:
            entry.session_id = session_id
            entry.status = "queued"
            entry.priority = priority
            entry.error = None
            entry.updated_at = now
            entry.last_accessed = now
            entry.payload = None
            self._drop_derived_payloads_locked(key)

        self._seq += 1
        heapq.heappush(self._queue, (priority, self._seq, key))

    def get_session_progress(self, session_id: str) -> dict[str, int]:
        with self._lock:
            keys = self._sessions.get(session_id) or []
            if keys:
                self._session_last_access[session_id] = time.time()
            counts = {"total": len(keys), "ready": 0, "running": 0, "queued": 0, "error": 0}
            for key in keys:
                status = self._cache.get(key).status if key in self._cache else "missing"
                if status in counts:
                    counts[status] += 1
            return counts

    def _dispatch_locked(self) -> None:
        self._evict_locked()
        while len(self._inflight) < self.max_workers and self._queue:
            priority, _, key = heapq.heappop(self._queue)
            entry = self._cache.get(key)
            if not entry or entry.status != "queued" or entry.priority != priority:
                continue
            entry.status = "running"
            entry.updated_at = time.time()
            future = self._executor.submit(self._fetch_payload, key)
            self._inflight[key] = future
            future.add_done_callback(lambda fut, cache_key=key: self._handle_done(cache_key, fut))

    def _fetch_payload(self, key: WalletBaseKey) -> dict[str, Any] | None:
        wallet, filter_level, filter_value = key
        return get_wallet_curation_base_data(wallet, filter_value, filter_level)

    def _handle_done(self, key: WalletBaseKey, future: Future) -> None:
        with self._lock:
            self._inflight.pop(key, None)
            entry = self._cache.get(key)
            if not entry:
                return
            entry.last_accessed = time.time()
            entry.updated_at = entry.last_accessed
            try:
                entry.payload = future.result()
                entry.error = None
                entry.status = "ready"
                self._drop_derived_payloads_locked(key)
            except Exception as exc:
                entry.payload = None
                entry.error = str(exc)
                entry.status = "error"
                logger.exception("Wallet curation prefetch failed for %s", key[0])
            self._dispatch_locked()

    def _evict_locked(self) -> None:
        now = time.time()
        stale_sessions = [
            session_id
            for session_id, last_access in self._session_last_access.items()
            if now - last_access > self.ttl_seconds
        ]
        for session_id in stale_sessions:
            self._session_last_access.pop(session_id, None)
            self._sessions.pop(session_id, None)

        stale_keys = [
            key for key, entry in self._cache.items()
            if entry.status in {"ready", "error"} and now - entry.updated_at > self.ttl_seconds
        ]
        for key in stale_keys:
            self._drop_derived_payloads_locked(key)
            self._cache.pop(key, None)

        ready_keys = [
            key for key, entry in self._cache.items()
            if entry.status == "ready"
        ]
        if len(ready_keys) <= self.max_ready_entries:
            return
        ready_keys.sort(key=lambda cache_key: self._cache[cache_key].last_accessed)
        for key in ready_keys[: max(0, len(ready_keys) - self.max_ready_entries)]:
            self._drop_derived_payloads_locked(key)
            self._cache.pop(key, None)


_MANAGER: CurationPrefetchManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_curation_prefetch_manager() -> CurationPrefetchManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = CurationPrefetchManager()
    return _MANAGER
