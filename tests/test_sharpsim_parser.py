from io import BytesIO
from pathlib import Path
import unittest

from openpyxl import load_workbook

from lib.sharpsim_parser import parse_sharpsim


class SharpsimParserTests(unittest.TestCase):
    def test_parse_sharpsim_extracts_wallet_order_and_filter_summary(self):
        payload = parse_sharpsim(Path("tests/Sharpsim.xlsx").read_bytes(), "Sharpsim.xlsx")

        self.assertEqual(payload["wallet_order"][0], "0xdd92232bcdfbbac04132b3cbacbf32c2e5b16b2a")
        self.assertEqual(payload["wallet_order"][1], "0x31864feb9d25dee93728c6225ba891530967e9ca")
        self.assertEqual(payload["wallets"][payload["wallet_order"][0]]["filter_level"], "detail")
        self.assertEqual(payload["wallets"][payload["wallet_order"][0]]["filter_value"], "League of Legends")
        self.assertEqual(payload["filter_summary"]["League of Legends"], 43)
        self.assertEqual(payload["wallets"][payload["wallet_order"][0]]["copied"], 1571)
        self.assertEqual(payload["wallets"][payload["wallet_order"][0]]["skipped"], 3824)

    def test_parse_sharpsim_marks_missing_drl_wallet_without_failing_upload(self):
        workbook = load_workbook("tests/Sharpsim.xlsx")
        del workbook["01_0xdd9223_LOL_DRL"]
        buffer = BytesIO()
        workbook.save(buffer)

        payload = parse_sharpsim(buffer.getvalue(), "Sharpsim.xlsx")

        wallet = payload["wallet_order"][0]
        self.assertEqual(payload["wallets"][wallet]["sim_status"], "missing_drl")
        self.assertEqual(payload["drl"].get(wallet, []), [])


if __name__ == "__main__":
    unittest.main()
