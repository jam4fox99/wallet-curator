import unittest
from datetime import date, datetime
from unittest.mock import patch

from lib.clickhouse_charts import (
    _build_curation_warning_chips,
    _build_final_price_lookup,
    build_chart_payload,
    build_curation_signals,
    compute_market_pnl_breakdown,
)


class ClickHouseChartsRegressionTests(unittest.TestCase):
    def setUp(self):
        self.question = "Miami Open: Sebastian Korda vs Martin Landaluce"
        self.token_yes = "yes-token"
        self.token_no = "no-token"
        self.token_scope = [
            {"token_id": self.token_yes, "question": self.question},
            {"token_id": self.token_no, "question": self.question},
        ]
        self.trades = [
            {
                "token_id": self.token_yes,
                "trade_date": date(2026, 3, 24),
                "side": "SELL",
                "shares": 100.0,
                "usdc": 99.9,
                "fee_usdc": 0.0,
            },
            {
                "token_id": self.token_no,
                "trade_date": date(2026, 3, 24),
                "side": "BUY",
                "shares": 100.0,
                "usdc": 0.1,
                "fee_usdc": 0.0,
            },
        ]
        self.closes = [
            {"token_id": self.token_yes, "trade_date": date(2026, 3, 31), "close_price": 0.999},
            {"token_id": self.token_no, "trade_date": date(2026, 3, 31), "close_price": 0.001},
        ]
        self.resolutions = {
            self.token_yes: {"resolved_ts": datetime(2026, 4, 1, 0, 0, 0), "price": 1.0},
            self.token_no: {"resolved_ts": datetime(2026, 4, 1, 0, 0, 0), "price": 0.0},
        }

    def test_final_prices_use_closes_until_resolution_day(self):
        prices = _build_final_price_lookup(
            [self.token_yes, self.token_no],
            self.closes,
            self.resolutions,
            date(2026, 3, 31),
        )
        self.assertEqual(prices[self.token_yes], 0.999)
        self.assertEqual(prices[self.token_no], 0.001)

        resolved_prices = _build_final_price_lookup(
            [self.token_yes, self.token_no],
            self.closes,
            self.resolutions,
            date(2026, 4, 1),
        )
        self.assertEqual(resolved_prices[self.token_yes], 1.0)
        self.assertEqual(resolved_prices[self.token_no], 0.0)

    def test_breakdown_marks_negative_positions_with_signed_value(self):
        final_prices = _build_final_price_lookup(
            [self.token_yes, self.token_no],
            self.closes,
            self.resolutions,
            date(2026, 3, 31),
        )

        breakdown = compute_market_pnl_breakdown(self.token_scope, self.trades, final_prices)
        market = breakdown["markets"][0]

        self.assertEqual(market["market_name"], self.question)
        self.assertEqual(market["total_trades"], 2)
        self.assertEqual(market["volume"], 100.0)
        self.assertAlmostEqual(market["net_cash"], 0.0, places=2)

    def test_breakdown_uses_opening_positions_as_window_baseline(self):
        opening_positions = {self.token_yes: 10.0}
        opening_prices = {self.token_yes: 0.4, self.token_no: 0.6}
        final_prices = {self.token_yes: 0.6, self.token_no: 0.4}
        trades = [
            {
                "token_id": self.token_yes,
                "trade_date": date(2026, 3, 31),
                "side": "SELL",
                "shares": 5.0,
                "usdc": 3.0,
                "fee_usdc": 0.0,
            }
        ]

        breakdown = compute_market_pnl_breakdown(
            self.token_scope,
            trades,
            final_prices,
            opening_positions=opening_positions,
            opening_prices=opening_prices,
        )
        market = breakdown["markets"][0]

        self.assertEqual(market["total_trades"], 1)
        self.assertEqual(market["volume"], 3.0)
        self.assertAlmostEqual(market["net_cash"], 2.0, places=2)

    def test_chart_payload_marks_from_opening_inventory_not_zero(self):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 3, 31)

        opening_positions = {self.token_yes: 10.0}
        token_scope = [
            {
                "token_id": self.token_yes,
                "condition_id": "cid-1",
                "question": self.question,
                "opening_shares": 10.0,
                "visible_trade_count": 0,
                "first_trade_ts": None,
                "last_trade_ts": None,
            }
        ]
        closes = [
            {"token_id": self.token_yes, "trade_date": date(2026, 2, 28), "close_price": 0.4},
            {"token_id": self.token_yes, "trade_date": date(2026, 3, 31), "close_price": 0.6},
        ]

        with patch("lib.clickhouse_charts.date", FixedDate):
            payload = build_chart_payload(
                "wallet",
                "ATP",
                30,
                token_scope,
                [],
                closes,
                {},
                opening_positions=opening_positions,
            )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["summary"]["first_trade_date"], "2026-03-01")
        self.assertEqual(payload["summary"]["final_pnl"], 2.0)
        self.assertEqual(payload["series"][-1]["marked_value"], 2.0)

    def test_curation_signals_compute_breadth_and_copyability_metrics(self):
        token_scope = [
            {"token_id": "m1-yes", "condition_id": "c1", "question": "Market 1"},
            {"token_id": "m1-no", "condition_id": "c1", "question": "Market 1"},
            {"token_id": "m2-yes", "condition_id": "c2", "question": "Market 2"},
        ]
        trades = [
            {
                "token_id": "m1-yes",
                "condition_id": "c1",
                "trade_date": date(2026, 3, 24),
                "side": "BUY",
                "shares": 10.0,
                "usdc": 4.0,
                "fee_usdc": 0.0,
                "price": 0.4,
                "role": "maker",
            },
            {
                "token_id": "m1-no",
                "condition_id": "c1",
                "trade_date": date(2026, 3, 24),
                "side": "BUY",
                "shares": 10.0,
                "usdc": 4.7,
                "fee_usdc": 0.0,
                "price": 0.47,
                "role": "taker",
            },
            {
                "token_id": "m2-yes",
                "condition_id": "c2",
                "trade_date": date(2026, 3, 25),
                "side": "BUY",
                "shares": 10.0,
                "usdc": 9.6,
                "fee_usdc": 0.0,
                "price": 0.96,
                "role": "",
            },
        ]
        breakdown = {
            "concentration": {"top1_pct": 30.0, "top3_pct": 65.0, "top5_pct": 80.0},
            "markets": [],
            "win_rate": 50.0,
        }

        signals = build_curation_signals(token_scope, trades, breakdown)

        self.assertEqual(signals["active_days"], 2)
        self.assertEqual(signals["unique_markets"], 2)
        self.assertEqual(signals["both_sides_market_pct"], 50.0)
        self.assertAlmostEqual(signals["copy_price_gap"], 0.07, places=4)
        self.assertAlmostEqual(signals["near_certain_buy_volume_pct"], 52.5, places=1)
        self.assertEqual(signals["metric_severities"]["concentration"], "amber")

    def test_curation_warning_chips_red_thresholds(self):
        chips, severities = _build_curation_warning_chips(
            active_days=3,
            unique_markets=4,
            top1_pct=50.0,
            top3_pct=80.0,
            both_sides_market_pct=90.0,
            copy_price_gap=0.06,
            near_certain_buy_volume_pct=30.0,
        )

        self.assertEqual(severities["sample"], "red")
        self.assertEqual(severities["concentration"], "red")
        self.assertEqual(severities["both_sides_market_pct"], "red")
        self.assertEqual(severities["copy_price_gap"], "red")
        self.assertEqual(severities["near_certain_buy_volume_pct"], "red")
        self.assertEqual({chip["key"] for chip in chips}, {
            "low_sample",
            "concentrated_edge",
            "heavy_both_sides",
            "taker_price_disadvantage",
            "near_certain_buying",
        })
        self.assertTrue(all(chip["severity"] == "red" for chip in chips))

    def test_curation_warning_chips_amber_thresholds(self):
        chips, severities = _build_curation_warning_chips(
            active_days=6,
            unique_markets=15,
            top1_pct=30.0,
            top3_pct=65.0,
            both_sides_market_pct=75.0,
            copy_price_gap=0.03,
            near_certain_buy_volume_pct=15.0,
        )

        self.assertEqual(severities["sample"], "amber")
        self.assertEqual(severities["concentration"], "amber")
        self.assertEqual(severities["both_sides_market_pct"], "amber")
        self.assertEqual(severities["copy_price_gap"], "amber")
        self.assertEqual(severities["near_certain_buy_volume_pct"], "amber")
        self.assertEqual({chip["severity"] for chip in chips}, {"amber"})


if __name__ == "__main__":
    unittest.main()
