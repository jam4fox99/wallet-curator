DEFAULT_TIER_CONFIG_ROWS = [
    ("high_conviction", "High Conviction", 20.0, 1),
    ("promoted", "Promoted", 10.0, 2),
    ("test", "Test", 4.0, 3),
]

TIER_SORT_ORDER_UPDATES = [
    "UPDATE tier_config SET sort_order = 1 WHERE tier_name = 'high_conviction'",
    "UPDATE tier_config SET sort_order = 2 WHERE tier_name = 'promoted'",
    "UPDATE tier_config SET sort_order = 3 WHERE tier_name = 'test'",
]
