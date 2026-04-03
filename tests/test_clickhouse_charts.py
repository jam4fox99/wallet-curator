import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch

from lib.clickhouse_charts import (
    CURATION_ALL_RANGE,
    _build_curation_warning_chips,
    _build_final_price_lookup,
    build_chart_payload,
    build_wallet_curation_payload_from_base,
    build_wallet_trade_audit_payload_from_base,
    build_curation_signals,
    compute_market_pnl_breakdown,
    normalize_curation_range_key,
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

    def test_normalize_curation_range_key_maps_all_time_aliases(self):
        self.assertEqual(normalize_curation_range_key(None), CURATION_ALL_RANGE)
        self.assertEqual(normalize_curation_range_key("365"), CURATION_ALL_RANGE)
        self.assertEqual(normalize_curation_range_key("2W"), "14D")
        self.assertEqual(normalize_curation_range_key(30), "30D")

    def test_all_time_payload_uses_full_trade_history(self):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        base_data = {
            "wallet": "wallet",
            "filter_value": "Esports",
            "filter_level": "detail",
            "token_scope": [
                {
                    "token_id": self.token_yes,
                    "condition_id": "cid-1",
                    "question": self.question,
                    "opening_shares": 0.0,
                    "visible_trade_count": 2,
                }
            ],
            "trades": [
                {
                    "token_id": self.token_yes,
                    "condition_id": "cid-1",
                    "trade_date": date(2024, 1, 1),
                    "side": "BUY",
                    "shares": 10.0,
                    "usdc": 4.0,
                    "fee_usdc": 0.0,
                    "price": 0.4,
                    "role": "maker",
                },
                {
                    "token_id": self.token_yes,
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 4, 1),
                    "side": "SELL",
                    "shares": 4.0,
                    "usdc": 3.0,
                    "fee_usdc": 0.0,
                    "price": 0.75,
                    "role": "maker",
                },
            ],
            "closes": [
                {"token_id": self.token_yes, "trade_date": date(2023, 12, 31), "close_price": 0.4},
                {"token_id": self.token_yes, "trade_date": date(2026, 4, 3), "close_price": 0.7},
            ],
            "resolutions": {},
        }

        with patch("lib.clickhouse_charts.date", FixedDate):
            payload = build_wallet_curation_payload_from_base(base_data, "ALL")

        self.assertIsNotNone(payload)
        self.assertEqual(payload["meta"]["range_key"], "ALL")
        self.assertEqual(payload["summary"]["first_trade_date"], "2024-01-01")
        self.assertEqual(payload["summary"]["total_trades"], 2)

    def test_interval_payload_carries_opening_positions_from_pre_window_trades(self):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        base_data = {
            "wallet": "wallet",
            "filter_value": "Esports",
            "filter_level": "detail",
            "token_scope": [
                {
                    "token_id": self.token_yes,
                    "condition_id": "cid-1",
                    "question": self.question,
                    "opening_shares": 0.0,
                    "visible_trade_count": 2,
                }
            ],
            "trades": [
                {
                    "token_id": self.token_yes,
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 3, 20),
                    "side": "BUY",
                    "shares": 10.0,
                    "usdc": 4.0,
                    "fee_usdc": 0.0,
                    "price": 0.4,
                    "role": "maker",
                },
                {
                    "token_id": self.token_yes,
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 4, 1),
                    "side": "SELL",
                    "shares": 5.0,
                    "usdc": 3.0,
                    "fee_usdc": 0.0,
                    "price": 0.6,
                    "role": "maker",
                },
            ],
            "closes": [
                {"token_id": self.token_yes, "trade_date": date(2026, 3, 26), "close_price": 0.4},
                {"token_id": self.token_yes, "trade_date": date(2026, 4, 1), "close_price": 0.6},
                {"token_id": self.token_yes, "trade_date": date(2026, 4, 3), "close_price": 0.7},
            ],
            "resolutions": {},
        }

        with patch("lib.clickhouse_charts.date", FixedDate):
            payload = build_wallet_curation_payload_from_base(base_data, "7D")

        self.assertIsNotNone(payload)
        self.assertEqual(payload["meta"]["range_key"], "7D")
        self.assertEqual(payload["summary"]["first_trade_date"], "2026-03-27")
        self.assertEqual(payload["summary"]["total_trades"], 1)
        self.assertEqual(payload["series"][-1]["pnl"], 2.5)
        self.assertEqual(payload["summary"]["active_days"], 1)

    def test_interval_payload_builds_trade_and_both_sides_rows(self):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        base_data = {
            "wallet": "wallet",
            "filter_value": "League of Legends",
            "filter_level": "detail",
            "token_scope": [
                {
                    "token_id": "cid-1-t1",
                    "condition_id": "cid-1",
                    "question": "LoL: T1 vs KT Rolster - Map 1 Winner",
                    "outcome": "T1",
                    "opening_shares": 0.0,
                    "visible_trade_count": 2,
                },
                {
                    "token_id": "cid-1-kt",
                    "condition_id": "cid-1",
                    "question": "LoL: T1 vs KT Rolster - Map 1 Winner",
                    "outcome": "KT Rolster",
                    "opening_shares": 0.0,
                    "visible_trade_count": 1,
                },
                {
                    "token_id": "cid-1-draw",
                    "condition_id": "cid-1",
                    "question": "LoL: T1 vs KT Rolster - Map 1 Winner",
                    "outcome": "Draw",
                    "opening_shares": 0.0,
                    "visible_trade_count": 1,
                },
                {
                    "token_id": "cid-2-gen",
                    "condition_id": "cid-2",
                    "question": "LoL: Gen.G vs DRX - Match Winner",
                    "outcome": "Gen.G",
                    "opening_shares": 0.0,
                    "visible_trade_count": 1,
                },
            ],
            "trades": [
                {
                    "token_id": "cid-1-t1",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 4, 1),
                    "side": "BUY",
                    "shares": 10.0,
                    "usdc": 4.0,
                    "fee_usdc": 0.0,
                    "price": 0.4,
                    "role": "maker",
                    "outcome": "T1",
                    "ts": datetime(2026, 4, 1, 12, 0, 0),
                },
                {
                    "token_id": "cid-1-kt",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 4, 2),
                    "side": "BUY",
                    "shares": 8.0,
                    "usdc": 3.6,
                    "fee_usdc": 0.0,
                    "price": 0.45,
                    "role": "taker",
                    "outcome": "KT Rolster",
                    "ts": datetime(2026, 4, 2, 12, 0, 0),
                },
                {
                    "token_id": "cid-1-t1",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 4, 3),
                    "side": "SELL",
                    "shares": 4.0,
                    "usdc": 2.4,
                    "fee_usdc": 0.0,
                    "price": 0.6,
                    "role": "maker",
                    "outcome": "T1",
                    "ts": datetime(2026, 4, 3, 8, 0, 0),
                },
                {
                    "token_id": "cid-1-draw",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 4, 3),
                    "side": "BUY",
                    "shares": 1.0,
                    "usdc": 0.1,
                    "fee_usdc": 0.0,
                    "price": 0.1,
                    "role": "maker",
                    "outcome": "Draw",
                    "ts": datetime(2026, 4, 3, 9, 0, 0),
                },
                {
                    "token_id": "cid-2-gen",
                    "condition_id": "cid-2",
                    "trade_date": date(2026, 4, 2),
                    "side": "BUY",
                    "shares": 5.0,
                    "usdc": 1.0,
                    "fee_usdc": 0.0,
                    "price": 0.2,
                    "role": "maker",
                    "outcome": "Gen.G",
                    "ts": datetime(2026, 4, 2, 15, 0, 0),
                },
            ],
            "closes": [
                {"token_id": "cid-1-t1", "trade_date": date(2026, 3, 31), "close_price": 0.4},
                {"token_id": "cid-1-kt", "trade_date": date(2026, 3, 31), "close_price": 0.45},
                {"token_id": "cid-1-draw", "trade_date": date(2026, 3, 31), "close_price": 0.1},
                {"token_id": "cid-2-gen", "trade_date": date(2026, 3, 31), "close_price": 0.2},
                {"token_id": "cid-1-t1", "trade_date": date(2026, 4, 3), "close_price": 0.7},
                {"token_id": "cid-1-kt", "trade_date": date(2026, 4, 3), "close_price": 0.25},
                {"token_id": "cid-1-draw", "trade_date": date(2026, 4, 3), "close_price": 0.05},
                {"token_id": "cid-2-gen", "trade_date": date(2026, 4, 3), "close_price": 0.9},
            ],
            "resolutions": {},
        }

        with patch("lib.clickhouse_charts.date", FixedDate):
            payload = build_wallet_curation_payload_from_base(base_data, "7D")

        self.assertNotIn("trade_rows", payload)

        both_sides = payload["both_sides_rows"]
        self.assertEqual(len(both_sides), 1)
        self.assertEqual(both_sides[0]["market"], "LoL: T1 vs KT Rolster - Map 1 Winner")
        self.assertEqual(both_sides[0]["outcome_a_label"], "T1")
        self.assertEqual(both_sides[0]["outcome_b_label"], "KT Rolster")
        self.assertEqual(both_sides[0]["extra_outcome_count"], 1)
        self.assertAlmostEqual(both_sides[0]["sold_shares_a"], 4.0, places=2)
        self.assertAlmostEqual(both_sides[0]["sold_shares_b"], 0.0, places=2)
        self.assertAlmostEqual(both_sides[0]["combined_avg_buy"], 0.85, places=3)
        self.assertAlmostEqual(both_sides[0]["total_pnl"], 0.95, places=2)

    def test_trade_audit_payload_from_base_limits_display_rows_to_winners_and_losers(self):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        trades = []
        for idx in range(120):
            price = idx / 100
            trades.append(
                {
                    "token_id": "sen-token",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 3, 29),
                    "side": "BUY",
                    "shares": 1.0,
                    "usdc": price,
                    "fee_usdc": 0.0,
                    "price": price,
                    "role": "maker",
                    "outcome": "Sentinels",
                    "ts": datetime(2026, 3, 29, 10, idx % 60, idx % 60),
                }
            )

        base_data = {
            "wallet": "wallet",
            "filter_value": "League of Legends",
            "filter_level": "detail",
            "token_scope": [
                {
                    "token_id": "sen-token",
                    "condition_id": "cid-1",
                    "question": "LoL: Sentinels vs LYON - Game 4 Winner",
                    "outcome": "Sentinels",
                    "opening_shares": 0.0,
                    "visible_trade_count": len(trades),
                }
            ],
            "trades": trades,
            "closes": [
                {"token_id": "sen-token", "trade_date": date(2026, 3, 29), "close_price": 0.5},
            ],
            "resolutions": {},
        }

        with patch("lib.clickhouse_charts.date", FixedDate):
            payload = build_wallet_trade_audit_payload_from_base(base_data, "30D")

        self.assertEqual(payload["total_rows"], 120)
        self.assertEqual(len(payload["display_rows"]), 100)
        prices = [row["price"] for row in payload["display_rows"]]
        self.assertIn(0.0, prices)
        self.assertIn(1.19, prices)
        self.assertNotIn(0.6, prices)
        self.assertAlmostEqual(payload["display_rows"][0]["mtm_pnl"], 0.5, places=2)
        self.assertAlmostEqual(payload["display_rows"][-1]["mtm_pnl"], -0.69, places=2)

    def test_both_sides_rows_ignore_synthetic_complements_and_cleanup_buys(self):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        base_data = {
            "wallet": "wallet",
            "filter_value": "League of Legends",
            "filter_level": "detail",
            "token_scope": [
                {
                    "token_id": "team-we-token",
                    "condition_id": "cid-1",
                    "question": "LoL: Team WE vs ThunderTalk Gaming (BO3) - Esports World Cup China Qualifier Phase 1",
                    "outcome": "",
                    "opening_shares": 0.0,
                    "visible_trade_count": 2,
                },
                {
                    "token_id": "tt-token",
                    "condition_id": "cid-1",
                    "question": "LoL: Team WE vs ThunderTalk Gaming (BO3) - Esports World Cup China Qualifier Phase 1",
                    "outcome": "",
                    "opening_shares": 0.0,
                    "visible_trade_count": 2,
                },
            ],
            "trades": [
                {
                    "token_id": "team-we-token",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 3, 29),
                    "side": "BUY",
                    "shares": 100.0,
                    "usdc": 68.0,
                    "fee_usdc": 0.0,
                    "price": 0.68,
                    "role": "maker",
                    "outcome": "Team WE",
                    "ts": datetime(2026, 3, 29, 10, 34, 4),
                },
                {
                    "token_id": "tt-token",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 3, 29),
                    "side": "SELL",
                    "shares": 100.0,
                    "usdc": 32.0,
                    "fee_usdc": 0.0,
                    "price": 0.32,
                    "role": "taker",
                    "outcome": "ThunderTalk Gaming",
                    "ts": datetime(2026, 3, 29, 10, 34, 4),
                },
                {
                    "token_id": "team-we-token",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 3, 29),
                    "side": "SELL",
                    "shares": 100.0,
                    "usdc": 99.9,
                    "fee_usdc": 0.0,
                    "price": 0.999,
                    "role": "taker",
                    "outcome": "Team WE",
                    "ts": datetime(2026, 3, 29, 18, 25, 42),
                },
                {
                    "token_id": "tt-token",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 3, 29),
                    "side": "BUY",
                    "shares": 100.0,
                    "usdc": 0.1,
                    "fee_usdc": 0.0,
                    "price": 0.001,
                    "role": "taker",
                    "outcome": "ThunderTalk Gaming",
                    "ts": datetime(2026, 3, 29, 18, 25, 42),
                },
            ],
            "closes": [
                {"token_id": "team-we-token", "trade_date": date(2026, 3, 29), "close_price": 0.999},
                {"token_id": "tt-token", "trade_date": date(2026, 3, 29), "close_price": 0.001},
            ],
            "resolutions": {},
        }

        with patch("lib.clickhouse_charts.date", FixedDate):
            payload = build_wallet_curation_payload_from_base(base_data, "30D")
            trade_audit = build_wallet_trade_audit_payload_from_base(base_data, "30D")

        self.assertEqual(trade_audit["rows"][0]["outcome_label"], "Team WE")
        self.assertEqual(trade_audit["rows"][-1]["outcome_label"], "ThunderTalk Gaming")
        self.assertEqual(payload["both_sides_rows"], [])

    def test_gamma_outcome_fallback_labels_rows_when_clickhouse_metadata_is_blank(self):
        class FixedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 4, 3)

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {
                "clobTokenIds": '["123456789","987654321"]',
                "outcomes": '["Sentinels","LYON"]',
            }
        ]

        base_data = {
            "wallet": "wallet",
            "filter_value": "League of Legends",
            "filter_level": "detail",
            "token_scope": [
                {
                    "token_id": "123456789",
                    "condition_id": "cid-1",
                    "question": "LoL: Sentinels vs LYON - Game 4 Winner",
                    "outcome": "",
                    "opening_shares": 0.0,
                    "visible_trade_count": 1,
                },
                {
                    "token_id": "987654321",
                    "condition_id": "cid-1",
                    "question": "LoL: Sentinels vs LYON - Game 4 Winner",
                    "outcome": "",
                    "opening_shares": 0.0,
                    "visible_trade_count": 1,
                },
            ],
            "trades": [
                {
                    "token_id": "123456789",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 3, 29),
                    "side": "BUY",
                    "shares": 10.0,
                    "usdc": 6.8,
                    "fee_usdc": 0.0,
                    "price": 0.68,
                    "role": "maker",
                    "outcome": "",
                    "ts": datetime(2026, 3, 29, 10, 34, 4),
                },
                {
                    "token_id": "987654321",
                    "condition_id": "cid-1",
                    "trade_date": date(2026, 3, 29),
                    "side": "BUY",
                    "shares": 10.0,
                    "usdc": 3.2,
                    "fee_usdc": 0.0,
                    "price": 0.32,
                    "role": "maker",
                    "outcome": "",
                    "ts": datetime(2026, 3, 29, 10, 35, 4),
                },
            ],
            "closes": [
                {"token_id": "123456789", "trade_date": date(2026, 3, 29), "close_price": 0.7},
                {"token_id": "987654321", "trade_date": date(2026, 3, 29), "close_price": 0.3},
            ],
            "resolutions": {},
        }

        with patch.dict("lib.clickhouse_charts._GAMMA_OUTCOME_CACHE", {}, clear=True), patch(
            "lib.clickhouse_charts.requests.get", return_value=response
        ), patch("lib.clickhouse_charts.date", FixedDate):
            payload = build_wallet_curation_payload_from_base(base_data, "30D")
            trade_audit = build_wallet_trade_audit_payload_from_base(base_data, "30D")

        self.assertEqual(trade_audit["rows"][0]["outcome_label"], "Sentinels")
        self.assertEqual(payload["both_sides_rows"][0]["outcome_b_label"], "LYON")


if __name__ == "__main__":
    unittest.main()
