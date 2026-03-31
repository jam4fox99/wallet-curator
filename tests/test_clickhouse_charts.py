import unittest
from datetime import date, datetime

from lib.clickhouse_charts import _build_final_price_lookup, compute_market_pnl_breakdown


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


if __name__ == "__main__":
    unittest.main()
