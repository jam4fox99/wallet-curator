import os
import unittest
from unittest.mock import Mock, patch

os.environ["DISABLE_SCHEDULER"] = "1"
os.environ["DATABASE_URL"] = ""

import app


class AppCurationTests(unittest.TestCase):
    def test_normalize_curation_lookback_defaults_invalid_values(self):
        self.assertEqual(app._normalize_curation_lookback(None), 365)
        self.assertEqual(app._normalize_curation_lookback("bad"), 365)
        self.assertEqual(app._normalize_curation_lookback(14), 14)
        self.assertEqual(app._normalize_curation_lookback(999), 365)

    def test_start_curation_uses_selected_range_store(self):
        manager = Mock()

        with patch("lib.curation_prefetch.get_curation_prefetch_manager", return_value=manager):
            result = app.start_curation(
                1,
                "0xabc\n0xdef",
                "detail::Esports",
                30,
            )

        self.assertEqual(result[0], ["0xabc", "0xdef"])
        self.assertEqual(result[2], 30)
        manager.prime_session.assert_called_once()
        self.assertEqual(manager.prime_session.call_args.kwargs["lookback_days"], 30)


if __name__ == "__main__":
    unittest.main()
