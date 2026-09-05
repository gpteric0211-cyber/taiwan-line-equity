from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.market_history_service import build_official_kline_payload  # noqa: E402


def row(
    trade_date: str,
    *,
    open_price: float = 100,
    high: float = 105,
    low: float = 98,
    close: float = 103,
    volume: float = 10_000,
    volume_unit: str = "shares",
    source: str = "TWSE MI_INDEX",
    source_quality: str = "official",
) -> dict[str, object]:
    return {
        "date": trade_date,
        "code": "2454",
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "volume_unit": volume_unit,
        "source": source,
        "source_quality": source_quality,
        "market": "listed",
    }


class MarketHistoryServiceTests(unittest.TestCase):
    def test_returns_only_valid_official_bars_in_ascending_order(self) -> None:
        rows = [
            row("2026-08-21", high=110, low=99, close=108, volume=20_000),
            row("2026-08-20", high=106, low=97, close=102, volume=15, volume_unit="lots"),
            row("2026-08-19", source="Yahoo Finance", source_quality="fallback"),
            row("2026-08-18", high=95, low=100),
            row("2026-08-17", source_quality="fallback"),
            row("2026-08-24"),
        ]

        result = build_official_kline_payload(
            "2454",
            rows,
            as_of_date="2026-08-23",
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            [item["trade_date"] for item in result["items"]],
            ["2026-08-20", "2026-08-21"],
        )
        self.assertEqual(result["items"][0]["volume_shares"], 15_000)
        self.assertEqual(result["summary"]["latest_close"], 108)
        self.assertEqual(result["summary"]["period_high"], 110)
        self.assertEqual(result["summary"]["period_low"], 97)
        self.assertEqual(result["summary"]["total_volume_shares"], 35_000)
        self.assertEqual(result["summary"]["average_volume_shares"], 17_500)
        self.assertTrue(result["data_quality"]["official_only"])
        self.assertFalse(result["data_quality"]["estimated"])
        self.assertEqual(result["data_quality"]["rejected_row_count"], 4)
        self.assertEqual(
            result["data_quality"]["rejection_counts"]["untrusted_source_quality"],
            1,
        )

    def test_fails_closed_when_no_official_valid_bar_exists(self) -> None:
        result = build_official_kline_payload(
            "2454",
            [row("2026-08-21", source="Yahoo Finance", source_quality="fallback")],
            as_of_date="2026-08-23",
        )

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["items"], [])
        self.assertEqual(
            result["data_quality"]["rejection_counts"],
            {"non_official_source": 1},
        )

    def test_limits_chart_window_without_reclassifying_old_valid_rows(self) -> None:
        rows = [row(f"2026-08-{day:02d}") for day in range(1, 6)]

        result = build_official_kline_payload(
            "2454",
            rows,
            limit=3,
            as_of_date="2026-08-23",
        )

        self.assertEqual(
            [item["trade_date"] for item in result["items"]],
            ["2026-08-03", "2026-08-04", "2026-08-05"],
        )
        self.assertEqual(result["data_quality"]["qualified_row_count"], 5)
        self.assertEqual(result["data_quality"]["truncated_row_count"], 2)
        self.assertEqual(result["data_quality"]["rejected_row_count"], 0)


if __name__ == "__main__":
    unittest.main()
