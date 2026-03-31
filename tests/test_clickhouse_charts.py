import unittest
from datetime import date, datetime
from unittest.mock import patch

from lib.clickhouse_charts import _build_final_price_lookup, build_chart_payload, compute_market_pnl_breakdown


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


if __name__ == "__main__":
    unittest.main()
