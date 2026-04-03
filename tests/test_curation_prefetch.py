import time
import unittest
from unittest.mock import call, patch

from lib.curation_prefetch import CacheEntry, CurationPrefetchManager


class CurationPrefetchManagerTests(unittest.TestCase):
    def test_overlapping_sessions_do_not_block_each_other(self):
        manager = CurationPrefetchManager(max_workers=1, ttl_seconds=60, max_ready_entries=20)
        started = []

        def fake_fetch(wallet, filter_value, filter_level):
            started.append(wallet)
            time.sleep(0.05)
            return {"wallet": wallet}

        with patch("lib.curation_prefetch.get_wallet_curation_base_data", side_effect=fake_fetch):
            manager.prime_session("session-a", ["0xa0", "0xa1", "0xa2"], "detail", "Esports", warm_count=2)
            manager.prime_session("session-b", ["0xb0"], "detail", "Esports", warm_count=1)
            time.sleep(0.35)

        self.assertEqual(started, ["0xa0", "0xb0", "0xa1", "0xa2"])
        self.assertEqual(manager.get_session_progress("session-a")["ready"], 3)
        self.assertEqual(manager.get_session_progress("session-b")["ready"], 1)

    def test_stale_sessions_are_dropped_during_eviction(self):
        manager = CurationPrefetchManager(max_workers=1, ttl_seconds=1, max_ready_entries=20)

        def fake_fetch(wallet, filter_value, filter_level):
            return {"wallet": wallet}

        with patch("lib.curation_prefetch.get_wallet_curation_base_data", side_effect=fake_fetch):
            manager.prime_session("session-a", ["0xa0"], "detail", "Esports", warm_count=1)
            time.sleep(0.05)
            manager._session_last_access["session-a"] = time.time() - 5
            manager._evict_locked()

        self.assertEqual(manager.get_session_progress("session-a")["total"], 0)

    def test_interval_payloads_are_cached_per_base_wallet(self):
        manager = CurationPrefetchManager(max_workers=1, ttl_seconds=60, max_ready_entries=20)
        base_key = manager.make_base_key("0xabc", "detail", "Esports")
        manager._cache[base_key] = CacheEntry(
            key=base_key,
            status="ready",
            payload={"wallet": "0xabc", "trades": []},
        )

        with patch(
            "lib.curation_prefetch.build_wallet_curation_payload_from_base",
            side_effect=[{"range": "7D"}, {"range": "ALL"}],
        ) as build_payload:
            seven_day_a = manager.get_payload(base_key, "7D")
            seven_day_b = manager.get_payload(base_key, "7D")
            all_time = manager.get_payload(base_key, "ALL")

        self.assertEqual(seven_day_a, {"range": "7D"})
        self.assertEqual(seven_day_b, {"range": "7D"})
        self.assertEqual(all_time, {"range": "ALL"})
        self.assertEqual(
            build_payload.call_args_list,
            [
                call({"wallet": "0xabc", "trades": []}, "7D"),
                call({"wallet": "0xabc", "trades": []}, "ALL"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
