from __future__ import annotations

import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.twse_valuation import (  # noqa: E402
    TwseValuationSourceDelayed,
    _fetch_rwd,
    fetch_twse_bwibbu_day,
    normalize_twse_valuation_number,
    parse_twse_bwibbu_rows,
)


class TwseValuationAdapterTest(unittest.TestCase):
    def test_normalize_twse_valuation_number(self) -> None:
        cases = [
            ("-", None),
            ("", None),
            (None, None),
            ("N/A", None),
            ("18.7", 18.7),
            ("6.91", 6.91),
            ("4.19", 4.19),
            ("1,234.56", 1234.56),
        ]
        for raw, expected in cases:
            self.assertEqual(normalize_twse_valuation_number(raw), expected)

    def test_parse_openapi_shape(self) -> None:
        rows = parse_twse_bwibbu_rows(
            [
                {
                    "Date": "20260612",
                    "Code": "2382",
                    "Name": "廣達",
                    "ClosePrice": "372",
                    "DividendYield": "4.19",
                    "DividendYear": "113",
                    "PEratio": "18.7",
                    "PBratio": "6.91",
                    "FiscalYearQuarter": "114/1",
                }
            ]
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["symbol"], "2382")
        self.assertEqual(row["name"], "廣達")
        self.assertEqual(row["data_date"], "2026-06-12")
        self.assertEqual(row["close_price"], 372.0)
        self.assertEqual(row["dividend_yield"], 4.19)
        self.assertEqual(row["dividend_year"], "113")
        self.assertEqual(row["pe_ratio"], 18.7)
        self.assertEqual(row["pb_ratio"], 6.91)
        self.assertEqual(row["financial_year_quarter"], "114/1")
        self.assertEqual(row["source"], "TWSE_BWIBBU")

    def test_parse_rwd_shape(self) -> None:
        rows = parse_twse_bwibbu_rows(
            {
                "fields": ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"],
                "data": [["2330", "台積電", "1,180", "1.10", "113", "28.5", "7.22", "114/1"]],
            },
            "2026-06-12",
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["symbol"], "2330")
        self.assertEqual(row["name"], "台積電")
        self.assertEqual(row["data_date"], "2026-06-12")
        self.assertEqual(row["close_price"], 1180.0)
        self.assertEqual(row["dividend_yield"], 1.10)
        self.assertEqual(row["pe_ratio"], 28.5)
        self.assertEqual(row["pb_ratio"], 7.22)

    @patch("adapter.twse_valuation._fetch_openapi")
    @patch("adapter.twse_valuation._fetch_rwd")
    def test_explicit_date_prefers_historical_rwd_endpoint(
        self,
        fetch_rwd,
        fetch_openapi,
    ) -> None:
        fetch_rwd.return_value = [{"symbol": "2357", "data_date": "2026-08-26"}]

        rows = fetch_twse_bwibbu_day("2026-08-26")

        self.assertEqual(rows[0]["data_date"], "2026-08-26")
        fetch_rwd.assert_called_once_with("2026-08-26")
        fetch_openapi.assert_not_called()

    @patch("adapter.twse_valuation._fetch_openapi")
    @patch("adapter.twse_valuation._fetch_rwd")
    def test_explicit_date_rejects_stale_openapi_fallback(
        self,
        fetch_rwd,
        fetch_openapi,
    ) -> None:
        fetch_rwd.side_effect = RuntimeError("source delayed")
        fetch_openapi.return_value = [{"symbol": "2357", "data_date": "2026-08-25"}]

        with self.assertRaises(TwseValuationSourceDelayed) as raised:
            fetch_twse_bwibbu_day("2026-08-26")

        self.assertEqual(raised.exception.requested_date, "2026-08-26")
        self.assertEqual(raised.exception.available_date, "2026-08-25")
        self.assertIn("returned 2026-08-25", raised.exception.source_errors[-1])

    @patch("adapter.twse_valuation.request_json")
    def test_rwd_rejects_response_date_mismatch(self, request_json) -> None:
        request_json.return_value = {
            "stat": "OK",
            "date": "20260825",
            "fields": ["證券代號"],
            "data": [["2357"]],
        }

        with self.assertRaisesRegex(RuntimeError, "returned 2026-08-25"):
            _fetch_rwd("2026-08-26")


if __name__ == "__main__":
    unittest.main()
