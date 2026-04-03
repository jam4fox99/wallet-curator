import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sync_script import build_wallet_sync_payload, sync_active_wallets


class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.commit_count = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commit_count += 1


class SyncScriptWalletStateTests(unittest.TestCase):
    def _write_wallet_csv(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        wallet_csv = Path(temp_dir.name) / "active_wallets.csv"
        wallet_csv.write_text(content, encoding="utf-8")
        return wallet_csv

    def test_build_wallet_sync_payload_preserves_header_and_global_row_without_wallets(self):
        wallet_csv = self._write_wallet_csv(
            "address,market_whitelist,copy_percentage_enabled,copy_percentage\n"
            "__global__,Esports,true,10\n"
        )

        payload = build_wallet_sync_payload(wallet_csv)

        self.assertEqual(
            payload["header_row"],
            "address,market_whitelist,copy_percentage_enabled,copy_percentage",
        )
        self.assertEqual(payload["global_row"], "__global__,Esports,true,10")
        self.assertEqual(payload["wallets"], [])
        self.assertIn("__global__", payload["csv_text"])

    def test_build_wallet_sync_payload_extracts_wallet_rows_and_order(self):
        wallet_csv = self._write_wallet_csv(
            "address,market_whitelist,copy_percentage_enabled,copy_percentage\n"
            "__global__,Esports,true,10\n"
            "0xABC,Counter-Strike,true,4\n"
            "0xdef,League of Legends,false,\n"
        )

        payload = build_wallet_sync_payload(wallet_csv)

        self.assertEqual(len(payload["wallets"]), 2)
        self.assertEqual(payload["wallets"][0][0], "0xabc")
        self.assertEqual(payload["wallets"][0][1], "Counter-Strike")
        self.assertEqual(payload["wallets"][0][2], "CS2")
        self.assertEqual(payload["wallets"][0][4], 0)
        self.assertEqual(payload["wallets"][1][0], "0xdef")
        self.assertEqual(payload["wallets"][1][2], "LOL")
        self.assertEqual(payload["wallets"][1][4], 1)

    def test_sync_active_wallets_truncates_stale_rows_on_zero_wallet_csv(self):
        wallet_csv = self._write_wallet_csv(
            "address,market_whitelist,copy_percentage_enabled,copy_percentage\n"
            "__global__,Esports,true,10\n"
        )
        conn = _FakeConnection()
        captured = {}

        def fake_execute_values(cursor, sql, values, page_size=250):
            captured["values"] = list(values)

        with patch("sync_script.execute_values", side_effect=fake_execute_values):
            wallet_count = sync_active_wallets(conn, wallet_csv)

        self.assertEqual(wallet_count, 0)
        self.assertEqual(conn.commit_count, 1)
        self.assertNotIn("values", captured)
        self.assertEqual(conn.cursor_obj.executed[0][0], "TRUNCATE TABLE synced_active_wallets")
        self.assertIn("INSERT INTO synced_csv_state", conn.cursor_obj.executed[1][0])
        self.assertEqual(conn.cursor_obj.executed[1][1][0], "address,market_whitelist,copy_percentage_enabled,copy_percentage")
        self.assertEqual(conn.cursor_obj.executed[1][1][1], "__global__,Esports,true,10")


if __name__ == "__main__":
    unittest.main()
