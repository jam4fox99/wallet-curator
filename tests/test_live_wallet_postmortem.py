import unittest
from datetime import date, datetime, timezone

from lib.live_wallet_postmortem import (
    _candidate_assessment,
    _derive_failure_modes,
    _expand_game_filter,
    LiveOutcome,
    MasterFeatures,
)


class LiveWalletPostmortemTests(unittest.TestCase):
    def _build_live(self, **overrides):
        base = dict(
            wallet_address="0xabc",
            cohort="candidate",
            game_filter="CS2",
            window_start_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            window_end_at=datetime(2026, 3, 8, tzinfo=timezone.utc),
            live_pnl=50.0,
            live_roi_pct=10.0,
            live_markets=15,
            live_trades=120,
            live_days=8,
        )
        base.update(overrides)
        return LiveOutcome(**base)

    def _build_master(self, **overrides):
        base = dict(
            wallet_address="0xabc",
            game_filter="CS2",
            detail_filters=("Counter-Strike",),
            window_start_date=date(2026, 3, 1),
            window_end_date=date(2026, 3, 8),
            total_trades=200,
            active_days=8,
            unique_markets=25,
            unique_conditions=25,
            total_volume_usd=5000.0,
            final_pnl=500.0,
            roi_pct=10.0,
            max_drawdown_pct=40.0,
            market_win_rate_pct=55.0,
            top1_pct=12.0,
            top3_pct=28.0,
            top5_pct=40.0,
            maker_buy_volume_pct=85.0,
            maker_buy_trade_pct=85.0,
            maker_buy_avg_price=0.45,
            taker_buy_avg_price=0.46,
            copy_price_gap=0.01,
            both_sides_market_pct=20.0,
            both_sides_volume_pct=25.0,
            near_certain_buy_volume_pct=0.0,
            avg_buy_price=0.45,
            top_markets=[],
        )
        base.update(overrides)
        return MasterFeatures(**base)

    def test_expand_game_filter_handles_esports_union(self):
        self.assertEqual(_expand_game_filter("LOL"), ("League of Legends",))
        self.assertEqual(
            _expand_game_filter("ESPORTS"),
            ("League of Legends", "Valorant", "Counter-Strike", "Dota 2"),
        )

    def test_failure_modes_prioritize_copyability_and_concentration(self):
        live = LiveOutcome(
            wallet_address="0xabc",
            cohort="negative_removed",
            game_filter="CS2",
            window_start_at=None,
            window_end_at=None,
            live_pnl=-50.0,
            live_roi_pct=-20.0,
            live_markets=5,
            live_trades=30,
            live_days=3,
        )
        master = MasterFeatures(
            wallet_address="0xabc",
            game_filter="CS2",
            detail_filters=("Counter-Strike",),
            window_start_date=None,
            window_end_date=None,
            total_trades=30,
            active_days=3,
            unique_markets=5,
            unique_conditions=5,
            total_volume_usd=1000.0,
            final_pnl=-200.0,
            roi_pct=-20.0,
            max_drawdown_pct=120.0,
            market_win_rate_pct=40.0,
            top1_pct=60.0,
            top3_pct=85.0,
            top5_pct=95.0,
            maker_buy_volume_pct=80.0,
            maker_buy_trade_pct=80.0,
            maker_buy_avg_price=0.45,
            taker_buy_avg_price=0.55,
            copy_price_gap=0.10,
            both_sides_market_pct=55.0,
            both_sides_volume_pct=70.0,
            near_certain_buy_volume_pct=30.0,
            avg_buy_price=0.5,
            top_markets=[],
        )
        reasons = _derive_failure_modes(live, master)
        self.assertIn("low live sample", reasons)
        self.assertIn("concentrated edge", reasons)
        self.assertIn("heavy both-sides trading", reasons)

    def test_candidate_assessment_marks_clean_positive_profile_as_check_first(self):
        verdict, notes = _candidate_assessment(self._build_live(), self._build_master())
        self.assertEqual(verdict, "check_first")
        self.assertIn("meaningful copy test", notes)

    def test_candidate_assessment_marks_tiny_concentrated_profile_as_avoid(self):
        live = self._build_live(live_markets=2, live_days=2, live_trades=4)
        master = self._build_master(
            unique_markets=2,
            top1_pct=90.0,
            top3_pct=100.0,
            both_sides_market_pct=100.0,
            copy_price_gap=0.15,
        )
        verdict, notes = _candidate_assessment(live, master)
        self.assertEqual(verdict, "avoid_for_now")
        self.assertIn("tiny positive sample", notes)


if __name__ == "__main__":
    unittest.main()
