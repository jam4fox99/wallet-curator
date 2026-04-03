import os
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["DISABLE_SCHEDULER"] = "1"
os.environ["DATABASE_URL"] = ""

import app


class AppCurationTests(unittest.TestCase):
    def test_handle_sim_upload_activates_sim_mode(self):
        fake_payload = {
            "wallet_order": ["0xabc"],
            "wallets": {"0xabc": {"address": "0xabc", "filter_level": "detail", "filter_value": "League of Legends"}},
            "filter_summary": {"League of Legends": 1},
        }
        session_manager = Mock()
        session_manager.create_session.return_value = "sim-session-1"

        with patch("lib.sharpsim_parser.parse_sharpsim", return_value=fake_payload), patch(
            "lib.sharpsim_session.get_sharpsim_session_manager", return_value=session_manager
        ):
            result = app.handle_sim_upload(
                "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,ZmFrZQ==",
                "Sharpsim.xlsx",
            )

        self.assertEqual(result[0], "sim-session-1")
        self.assertTrue(result[1])
        self.assertTrue(result[2])
        self.assertEqual(result[4], {"display": "none"})
        self.assertEqual(result[5], {"display": "inline-flex"})

    def test_cur_range_store_is_owned_by_single_callback(self):
        source = Path(app.__file__).read_text()
        self.assertEqual(source.count('Output("cur-range", "data")'), 1)

    def test_wallet_curation_uses_inline_loading_shell_not_dcc_loading(self):
        source = Path(app.__file__).read_text()
        self.assertNotIn("dcc.Loading(", source)

    def test_normalize_curation_range_defaults_invalid_values(self):
        self.assertEqual(app._normalize_curation_range(None), "ALL")
        self.assertEqual(app._normalize_curation_range("bad"), "ALL")
        self.assertEqual(app._normalize_curation_range(14), "14D")
        self.assertEqual(app._normalize_curation_range("2W"), "14D")
        self.assertEqual(app._normalize_curation_range(365), "ALL")

    def test_coerce_curation_render_state_recovers_stale_argument_orders(self):
        wallets, filter_raw, selected_range = app._coerce_curation_render_state(
            0,
            ["0xabc"],
            "subcategory::League of Legends",
            365,
        )
        self.assertEqual(wallets, ["0xabc"])
        self.assertEqual(filter_raw, "subcategory::League of Legends")
        self.assertEqual(selected_range, "ALL")

        wallets, filter_raw, selected_range = app._coerce_curation_render_state(
            ["0xdef"],
            "subcategory::Counter-Strike",
            30,
            2,
        )
        self.assertEqual(wallets, ["0xdef"])
        self.assertEqual(filter_raw, "subcategory::Counter-Strike")
        self.assertEqual(selected_range, "30D")

    def test_start_curation_uses_selected_range_store(self):
        manager = Mock()

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=manager):
            result = app.start_curation(
                1,
                "0xabc\n0xdef",
                "detail::Esports",
                30,
                False,
                "",
            )

        self.assertEqual(result[0], ["0xabc", "0xdef"])
        self.assertEqual(result[1], "detail::Esports")
        self.assertEqual(result[2], 0)
        manager.prime_session.assert_called_once()
        self.assertEqual(manager.prime_session.call_args.kwargs["session_id"], result[5])
        self.assertEqual(
            manager.prime_session.call_args.kwargs["wallet_configs"],
            [
                {"address": "0xabc", "filter_level": "detail", "filter_value": "Esports"},
                {"address": "0xdef", "filter_level": "detail", "filter_value": "Esports"},
            ],
        )
        self.assertEqual(manager.prime_session.call_args.kwargs["warm_count"], 6)

    def test_start_curation_uses_sim_wallet_configs_when_session_is_active(self):
        payload = {
            "wallet_order": ["0xabc", "0xdef"],
            "wallets": {
                "0xabc": {"address": "0xabc", "filter_level": "detail", "filter_value": "League of Legends"},
                "0xdef": {"address": "0xdef", "filter_level": "detail", "filter_value": "Valorant"},
            },
        }
        session_manager = Mock()
        session_manager.get_session.return_value = payload
        prefetch_manager = Mock()

        with patch("lib.sharpsim_session.get_sharpsim_session_manager", return_value=session_manager), patch(
            "lib.curation_prefetch.get_curation_prefetch_manager", return_value=prefetch_manager
        ):
            result = app.start_curation(1, "", None, "30D", True, "sim-session-1")

        self.assertEqual(result[0], ["0xabc", "0xdef"])
        self.assertEqual(result[1], "detail::League of Legends")
        self.assertEqual(
            prefetch_manager.prime_session.call_args.kwargs["wallet_configs"],
            [
                {"address": "0xabc", "filter_level": "detail", "filter_value": "League of Legends"},
                {"address": "0xdef", "filter_level": "detail", "filter_value": "Valorant"},
            ],
        )

    def test_update_curation_status_keeps_poll_running_while_current_wallet_loads(self):
        manager = Mock()
        wallet = "0xabc"
        base_key = (wallet, "subcategory", "League of Legends")
        manager.make_base_key.return_value = base_key
        manager.get_session_progress.return_value = {"total": 1, "ready": 0, "running": 1, "queued": 0, "error": 0}
        manager.get_payload.return_value = None
        manager.get_status.return_value = "running"
        manager.get_error.return_value = None

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=manager):
            result = app.update_curation_status(
                0,
                "session-1",
                "7D",
                0,
                [wallet],
                "subcategory::League of Legends",
                None,
            )

        self.assertEqual(result[0], "Wallet 1 of 1 • 7D • ready 0/1 • running 1")
        self.assertEqual(result[2]["status"], "loading")
        self.assertFalse(result[3])
        manager.warm_session_index.assert_called_once_with("session-1", 0, warm_count=6)

    def test_update_curation_status_disables_poll_after_terminal_state(self):
        manager = Mock()
        wallet = "0xabc"
        base_key = (wallet, "subcategory", "League of Legends")
        payload = {
            "series": [
                {
                    "date": "2026-04-01",
                    "pnl": 12.5,
                    "cumulative_cash": 5.0,
                    "marked_value": 7.5,
                    "daily_trade_count": 2,
                }
            ],
            "summary": {
                "final_pnl": 12.5,
                "roi_pct": 10.0,
                "total_trades": 2,
                "total_volume_usd": 125.0,
                "unique_markets": 1,
                "active_days": 1,
            },
            "signals": {},
            "breakdown": {"markets": []},
        }
        manager.make_base_key.return_value = base_key
        manager.get_session_progress.return_value = {"total": 1, "ready": 1, "running": 0, "queued": 0, "error": 0}
        manager.get_payload.return_value = payload
        manager.get_status.return_value = "ready"
        manager.get_error.return_value = None

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=manager):
            result = app.update_curation_status(
                0,
                "session-1",
                "7D",
                0,
                [wallet],
                "subcategory::League of Legends",
                None,
            )

        self.assertEqual(result[0], "Wallet 1 of 1 • 7D • ready 1/1 • running 0")
        self.assertEqual(result[2]["status"], "ready")
        self.assertTrue(result[3])
        manager.warm_session_index.assert_called_once_with("session-1", 0, warm_count=6)
        manager.get_payload.assert_called_once_with(base_key, "7D")

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=manager):
            repeat = app.update_curation_status(
                0,
                "session-1",
                "7D",
                1,
                [wallet],
                "subcategory::League of Legends",
                result[2],
            )

        self.assertIs(repeat[2], app.no_update)

    def test_render_curation_wallet_returns_plotly_figure_when_payload_ready(self):
        manager = Mock()
        wallet = "0xabc"
        base_key = (wallet, "subcategory", "League of Legends")
        payload = {
            "series": [{"date": "2026-04-01", "pnl": 1.0, "cumulative_cash": 0.0, "marked_value": 1.0, "daily_trade_count": 1}],
            "summary": {
                "final_pnl": 1.0,
                "roi_pct": 1.0,
                "total_trades": 1,
                "total_volume_usd": 10.0,
                "unique_markets": 1,
                "active_days": 1,
            },
            "signals": {},
            "breakdown": {"markets": []},
        }
        manager.make_base_key.return_value = base_key
        manager.get_payload.return_value = payload

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=manager):
            result = app.render_curation_wallet(
                {
                    "status": "ready",
                    "wallet": wallet,
                    "filter_level": "subcategory",
                    "filter_value": "League of Legends",
                    "range": "ALL",
                },
                False,
                "",
            )

        self.assertTrue(hasattr(result[3], "to_plotly_json"))
        manager.get_payload.assert_called_once_with(base_key, "ALL")

    def test_render_curation_wallet_adds_sim_trace_when_overlay_visible(self):
        actual_manager = Mock()
        session_manager = Mock()
        wallet = "0xabc"
        base_key = (wallet, "detail", "League of Legends")
        base_payload = {
            "wallet": wallet,
            "filter_value": "League of Legends",
            "closes": [{"token_id": "yes-token", "trade_date": date(2026, 4, 3), "close_price": 0.7}],
            "resolutions": {},
        }
        actual_payload = {
            "series": [{"date": "2026-04-03", "pnl": 1.0, "cumulative_cash": 0.0, "marked_value": 1.0, "daily_trade_count": 1}],
            "summary": {"final_pnl": 1.0, "roi_pct": 1.0, "total_trades": 1, "total_volume_usd": 10.0, "unique_markets": 1, "active_days": 1},
            "signals": {},
            "breakdown": {"markets": []},
        }
        sim_session = {
            "wallets": {wallet: {"address": wallet, "filter_value": "League of Legends", "sim_status": "ready", "copied": 2, "skipped": 1}},
            "drl": {wallet: []},
        }
        sim_payload = {
            "series": [{"date": "2026-04-03", "pnl": 2.0, "cumulative_cash": 0.0, "marked_value": 2.0, "daily_trade_count": 1}],
            "summary": {"final_pnl": 2.0, "roi_pct": 20.0, "copied_trades": 2, "sim_status": "ready"},
            "validation": {"workbook_value": 2.0, "recomputed_value": 2.0, "delta": 0.0},
        }
        actual_manager.make_base_key.return_value = base_key
        actual_manager.get_payload.return_value = actual_payload
        actual_manager.get_base_payload.return_value = base_payload
        session_manager.get_session.return_value = sim_session

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=actual_manager), patch(
            "lib.sharpsim_session.get_sharpsim_session_manager", return_value=session_manager
        ), patch("lib.sharpsim_parser.build_sim_payload", return_value=sim_payload):
            result = app.render_curation_wallet(
                {"status": "ready", "wallet": wallet, "filter_level": "detail", "filter_value": "League of Legends", "range": "ALL"},
                True,
                "sim-session-1",
            )

        figure = result[3]
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(figure.data[1].name, "Sim P&L")

    def test_render_curation_wallet_shows_warning_when_sim_payload_is_unavailable(self):
        actual_manager = Mock()
        wallet = "0xabc"
        base_key = (wallet, "detail", "League of Legends")
        actual_manager.make_base_key.return_value = base_key
        actual_manager.get_payload.return_value = {
            "series": [{"date": "2026-04-03", "pnl": 1.0, "cumulative_cash": 0.0, "marked_value": 1.0, "daily_trade_count": 1}],
            "summary": {"final_pnl": 1.0, "roi_pct": 1.0, "total_trades": 1, "total_volume_usd": 10.0, "unique_markets": 1, "active_days": 1},
            "signals": {},
            "breakdown": {"markets": []},
        }
        actual_manager.get_base_payload.return_value = {"wallet": wallet, "filter_value": "League of Legends", "closes": [], "resolutions": {}}

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=actual_manager), patch(
            "lib.sharpsim_session.get_sharpsim_session_manager"
        ) as session_manager, patch("lib.sharpsim_parser.build_sim_payload", return_value=None):
            session_manager.return_value.get_session.return_value = {
                "wallets": {wallet: {"address": wallet, "filter_value": "League of Legends", "sim_status": "missing_drl", "sim_error": ""}},
                "drl": {wallet: []},
            }
            result = app.render_curation_wallet(
                {"status": "ready", "wallet": wallet, "filter_level": "detail", "filter_value": "League of Legends", "range": "ALL"},
                True,
                "sim-session-1",
            )

        self.assertTrue(hasattr(result[3], "to_plotly_json"))
        self.assertIn("Sim data unavailable", str(result[2]))

    def test_download_curation_results_returns_csv_for_reviewed_wallets(self):
        decisions = {
            "0xabc": {
                "wallet": "0xabc",
                "decision": "approved",
                "filter_level": "detail",
                "filter_value": "League of Legends",
                "actual_final_pnl": 12.5,
                "actual_roi_pct": 10.0,
                "sim_final_pnl": 4.0,
                "sim_roi_pct": 40.0,
                "sim_copied": 2,
                "sim_skipped": 1,
                "sim_status": "ready",
            }
        }

        download = app.download_curation_results(1, decisions)

        self.assertTrue(download["filename"].startswith("curation_results_"))
        self.assertIn("wallet,filter_level,filter_value,decision", download["content"])
        self.assertIn("0xabc,detail,League of Legends,approved,12.5,10.0,4.0,40.0,2,1,ready", download["content"])

    def test_handle_curation_action_records_structured_decision_rows(self):
        with patch("app._build_curation_decision_row", return_value={"wallet": "0xabc", "decision": "approved", "sim_status": "ready"}):
            result = app.handle_curation_action(
                1,
                0,
                0,
                0,
                ["0xabc"],
                [],
                {},
                {"status": "ready", "wallet": "0xabc", "filter_level": "detail", "filter_value": "League of Legends", "range": "ALL"},
                "sim-session-1",
            )

        self.assertEqual(result[2]["0xabc"]["sim_status"], "ready")

    def test_render_curation_wallet_accepts_stale_browser_argument_order(self):
        wallets, filter_raw, selected_range = app._coerce_curation_render_state(
            0,
            ["0xabc"],
            "subcategory::League of Legends",
            365,
        )

        self.assertEqual(wallets, ["0xabc"])
        self.assertEqual(filter_raw, "subcategory::League of Legends")
        self.assertEqual(selected_range, "ALL")


if __name__ == "__main__":
    unittest.main()
