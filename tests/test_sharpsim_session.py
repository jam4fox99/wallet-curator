import time
import unittest

from lib.sharpsim_session import SharpsimSessionManager


class SharpsimSessionManagerTests(unittest.TestCase):
    def test_create_get_and_clear_session(self):
        manager = SharpsimSessionManager(ttl_seconds=60, max_sessions=5)
        payload = {"wallet_order": ["0xabc"], "wallets": {"0xabc": {"address": "0xabc"}}}

        session_id = manager.create_session(payload)

        self.assertEqual(manager.get_session(session_id)["wallet_order"], ["0xabc"])
        self.assertEqual(manager.get_wallet_meta(session_id, "0xabc")["address"], "0xabc")

        manager.clear_session(session_id)
        self.assertIsNone(manager.get_session(session_id))

    def test_stale_sessions_are_evicted_on_access(self):
        manager = SharpsimSessionManager(ttl_seconds=1, max_sessions=5)
        session_id = manager.create_session({"wallet_order": []})
        manager._sessions[session_id].last_accessed = time.time() - 5

        self.assertIsNone(manager.get_session(session_id))


if __name__ == "__main__":
    unittest.main()
