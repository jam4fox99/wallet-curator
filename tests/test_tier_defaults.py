import unittest

from lib.cloud_db import POSTGRES_MIGRATIONS
from lib.tier_defaults import DEFAULT_TIER_CONFIG_ROWS, TIER_SORT_ORDER_UPDATES


class TierDefaultsTests(unittest.TestCase):
    def test_default_tier_config_rows_match_sqlite_display_order(self):
        self.assertEqual(
            [row[0] for row in DEFAULT_TIER_CONFIG_ROWS],
            ["high_conviction", "promoted", "test"],
        )
        self.assertEqual(
            [row[3] for row in DEFAULT_TIER_CONFIG_ROWS],
            [1, 2, 3],
        )

    def test_postgres_migrations_include_tier_sort_order_repair(self):
        for statement in TIER_SORT_ORDER_UPDATES:
            self.assertIn(statement, POSTGRES_MIGRATIONS)


if __name__ == "__main__":
    unittest.main()
