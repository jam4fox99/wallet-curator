from datetime import date, datetime
from unittest.mock import patch
from io import BytesIO
from pathlib import Path
import unittest

from openpyxl import load_workbook

from lib.sharpsim_parser import build_sim_payload, parse_sharpsim


def _drl_row(ts, status, side, token_id, condition_id, question, price, shares, notional):
    return {
        "ts": ts,
        "status": status,
        "side": side,
        "token_id": token_id,
        "condition_id": condition_id,
        "question": question,
        "copied_price": price,
        "copied_shares": shares,
        "copied_notional": notional,
    }


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


class SharpsimReplayTests(unittest.TestCase):
    def test_build_sim_payload_filters_to_copied_rows_and_rebases_selected_window(self):
        wallet_meta = {
            "address": "0xabc",
            "filter_value": "League of Legends",
            "copied": 2,
            "skipped": 1,
            "sim_7d": 4.0,
            "sim_30d": 6.0,
        }
        drl_rows = [
            _drl_row(datetime(2026, 3, 28, 10, 0), "COPIED", "BUY", "yes-token", "cid-1", "Match 1", 0.40, 10.0, 4.0),
            _drl_row(datetime(2026, 3, 29, 10, 0), "SKIPPED", "BUY", "yes-token", "cid-1", "Match 1", 0.60, 10.0, 6.0),
            _drl_row(datetime(2026, 4, 2, 10, 0), "COPIED", "SELL", "yes-token", "cid-1", "Match 1", 0.80, 5.0, 4.0),
        ]
        closes = [
            {"token_id": "yes-token", "trade_date": date(2026, 3, 27), "close_price": 0.40},
            {"token_id": "yes-token", "trade_date": date(2026, 4, 3), "close_price": 0.70},
        ]
        resolutions = {}

        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        with patch("lib.sharpsim_parser.date", FixedDate):
            payload = build_sim_payload(wallet_meta, drl_rows, closes, resolutions, "7D")

        self.assertEqual(payload["summary"]["total_trades"], 2)
        self.assertEqual(payload["summary"]["copied_trades"], 2)
        self.assertEqual(payload["summary"]["sim_status"], "ready")
        self.assertEqual(payload["validation"]["workbook_value"], 4.0)
        self.assertEqual(payload["series"][0]["pnl"], 0.0)

    def test_build_sim_payload_carries_opening_positions_from_pre_window_copied_rows(self):
        wallet_meta = {"address": "0xabc", "filter_value": "League of Legends", "copied": 1, "skipped": 0}
        drl_rows = [
            _drl_row(datetime(2026, 3, 1, 9, 0), "COPIED", "BUY", "yes-token", "cid-1", "Match 1", 0.40, 10.0, 4.0),
        ]
        closes = [
            {"token_id": "yes-token", "trade_date": date(2026, 3, 31), "close_price": 0.40},
            {"token_id": "yes-token", "trade_date": date(2026, 4, 3), "close_price": 0.60},
        ]

        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        with patch("lib.sharpsim_parser.date", FixedDate):
            payload = build_sim_payload(wallet_meta, drl_rows, closes, {}, "1D")

        self.assertEqual(payload["summary"]["first_trade_date"], "2026-04-02")
        self.assertEqual(payload["series"][-1]["marked_value"], 2.0)


if __name__ == "__main__":
    unittest.main()
