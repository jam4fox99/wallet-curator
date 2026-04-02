import time
import unittest
from unittest.mock import patch

from lib.curation_prefetch import CurationPrefetchManager


class CurationPrefetchManagerTests(unittest.TestCase):
    def test_overlapping_sessions_do_not_block_each_other(self):
        manager = CurationPrefetchManager(max_workers=1, ttl_seconds=60, max_ready_entries=20)
        started = []

        def fake_fetch(wallet, filter_value, lookback_days, filter_level):
            started.append(wallet)
            time.sleep(0.05)
            return {"series": [], "summary": {"final_pnl": 0}}

        with patch("lib.curation_prefetch.get_wallet_curation_data", side_effect=fake_fetch):
            manager.prime_session("session-a", ["0xa0", "0xa1", "0xa2"], "detail", "Esports", 30, warm_count=2)
            manager.prime_session("session-b", ["0xb0"], "detail", "Esports", 30, warm_count=1)
            time.sleep(0.35)

        self.assertEqual(started, ["0xa0", "0xb0", "0xa1", "0xa2"])
        self.assertEqual(manager.get_session_progress("session-a")["ready"], 3)
        self.assertEqual(manager.get_session_progress("session-b")["ready"], 1)

    def test_stale_sessions_are_dropped_during_eviction(self):
        manager = CurationPrefetchManager(max_workers=1, ttl_seconds=1, max_ready_entries=20)

        def fake_fetch(wallet, filter_value, lookback_days, filter_level):
            return {"series": [], "summary": {"final_pnl": 0}}

        with patch("lib.curation_prefetch.get_wallet_curation_data", side_effect=fake_fetch):
            manager.prime_session("session-a", ["0xa0"], "detail", "Esports", 30, warm_count=1)
            time.sleep(0.05)
            manager._session_last_access["session-a"] = time.time() - 5
            manager._evict_locked()

        self.assertEqual(manager.get_session_progress("session-a")["total"], 0)


if __name__ == "__main__":
    unittest.main()
