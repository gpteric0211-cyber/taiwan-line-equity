from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from repository.rsi_adjustment_repository import (
    apply_rsi_split_adjustments,
    select_yahoo_applied_split_events,
)


class RsiSplitAdjustmentTests(unittest.TestCase):
    def test_ignores_event_when_yahoo_close_was_not_normalized(self) -> None:
        rows = [
            {"date": "2026-06-18", "close": 5130.0},
            {"date": "2026-06-22", "close": 1710.0},
        ]
        yahoo = {"2026-06-18": 5130.0, "2026-06-22": 1710.0}
        events = [{"event_date": "2026-06-22", "pre_event_factor": 1 / 3}]

        selected, decisions = select_yahoo_applied_split_events(rows, yahoo, events)

        self.assertEqual(selected, [])
        self.assertEqual(decisions[0]["status"], "event_not_applied_by_yahoo")

    def test_selects_event_when_yahoo_pre_event_close_has_expected_ratio(self) -> None:
        rows = [
            {"date": "2026-07-20", "close": 101.0},
            {"date": "2026-07-21", "close": 102.0},
        ]
        factor = 1000 / 1010
        yahoo = {"2026-07-20": 101.0 * factor, "2026-07-21": 102.0}
        events = [{"event_date": "2026-07-21", "pre_event_factor": factor}]

        selected, decisions = select_yahoo_applied_split_events(rows, yahoo, events)

        self.assertEqual(len(selected), 1)
        self.assertEqual(decisions[0]["status"], "applied_by_yahoo")

    def test_adjusts_pre_event_technical_ohlc_and_preserves_raw_ohlc(self) -> None:
        rows = [
            {"date": "2026-07-19", "open": 98.0, "high": 103.0, "low": 97.0, "close": 100.0},
            {"date": "2026-07-20", "open": 100.0, "high": 104.0, "low": 99.0, "close": 101.0},
            {"date": "2026-07-21", "open": 101.0, "high": 105.0, "low": 100.0, "close": 102.0},
        ]
        events = [{"event_date": "2026-07-21", "pre_event_factor": 1000 / 1010}]

        adjusted = apply_rsi_split_adjustments(rows, events)

        self.assertEqual([row["open"] for row in adjusted], [98.0, 100.0, 101.0])
        self.assertEqual([row["high"] for row in adjusted], [103.0, 104.0, 105.0])
        self.assertEqual([row["low"] for row in adjusted], [97.0, 99.0, 100.0])
        self.assertEqual([row["close"] for row in adjusted], [100.0, 101.0, 102.0])
        self.assertAlmostEqual(adjusted[0]["technical_open"], 98.0 * 1000 / 1010)
        self.assertAlmostEqual(adjusted[0]["technical_high"], 103.0 * 1000 / 1010)
        self.assertAlmostEqual(adjusted[0]["technical_low"], 97.0 * 1000 / 1010)
        self.assertAlmostEqual(adjusted[0]["technical_close"], 100.0 * 1000 / 1010)
        self.assertAlmostEqual(adjusted[0]["rsi_close"], 100.0 * 1000 / 1010)
        self.assertAlmostEqual(adjusted[1]["rsi_close"], 101.0 * 1000 / 1010)
        self.assertEqual(adjusted[2]["technical_open"], 101.0)
        self.assertEqual(adjusted[2]["technical_high"], 105.0)
        self.assertEqual(adjusted[2]["technical_low"], 100.0)
        self.assertEqual(adjusted[2]["technical_close"], 102.0)
        self.assertEqual(adjusted[2]["rsi_close"], 102.0)

    def test_multiple_events_are_cumulative_for_older_rows(self) -> None:
        rows = [
            {"date": "2025-08-20", "close": 100.0},
            {"date": "2025-08-21", "close": 100.0},
            {"date": "2026-07-22", "close": 100.0},
            {"date": "2026-07-23", "close": 100.0},
        ]
        events = [
            {"event_date": "2025-08-21", "pre_event_factor": 1000 / 1034},
            {"event_date": "2026-07-23", "pre_event_factor": 1000 / 1020},
        ]

        adjusted = apply_rsi_split_adjustments(rows, events)

        self.assertAlmostEqual(adjusted[0]["rsi_close"], 100.0 * 1000 / 1034 * 1000 / 1020)
        self.assertAlmostEqual(adjusted[1]["rsi_close"], 100.0 * 1000 / 1020)
        self.assertAlmostEqual(adjusted[2]["rsi_close"], 100.0 * 1000 / 1020)
        self.assertEqual(adjusted[3]["rsi_close"], 100.0)


if __name__ == "__main__":
    unittest.main()
