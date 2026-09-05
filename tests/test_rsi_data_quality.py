from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.data_quality import assess_recent_trading_date_coverage  # noqa: E402


class RsiDataQualityTest(unittest.TestCase):
    def test_recent_trading_date_coverage_requires_every_reference_date(self) -> None:
        reference = [f"2026-08-{day:02d}" for day in range(1, 6)]
        result = assess_recent_trading_date_coverage(reference, reference, required_days=5)
        self.assertTrue(result["ready"])
        self.assertEqual(result["coverage_ratio"], 1.0)
        self.assertEqual(result["missing_dates"], [])

    def test_recent_trading_date_coverage_fails_closed_on_hidden_gap(self) -> None:
        reference = ["2026-08-08", "2026-08-07", "2026-08-06", "2026-08-05", "2026-08-04"]
        actual = ["2026-08-08", "2026-08-07", "2026-08-05", "2026-08-04"]
        result = assess_recent_trading_date_coverage(actual, reference, required_days=5)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "recent_trading_dates_missing")
        self.assertEqual(result["missing_dates"], ["2026-08-06"])

    def test_recent_trading_date_coverage_rejects_short_reference_inventory(self) -> None:
        result = assess_recent_trading_date_coverage(
            ["2026-08-08", "2026-08-07"],
            ["2026-08-08", "2026-08-07"],
            required_days=3,
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "market_reference_dates_insufficient")


if __name__ == "__main__":
    unittest.main()
