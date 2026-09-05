from __future__ import annotations

import sys
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter import twse  # noqa: E402


class TwseHistoryAdapterTest(unittest.TestCase):
    def test_fetch_twse_stock_month_rows_parses_every_daily_row(self) -> None:
        payload = {
            "stat": "OK",
            "data": [
                ["115/08/03", "1,000", "200,000", "200", "205", "198", "203", "+3", "500"],
                ["115/08/04", "2,000", "410,000", "203", "208", "201", "205", "+2", "600"],
            ],
        }
        with patch.object(twse, "request_json", return_value=payload):
            result = twse.fetch_twse_stock_month_rows("2330", "2026-08-17")
        self.assertTrue(result["ok"])
        self.assertEqual(result["row_count"], 2)
        self.assertEqual([row["date"] for row in result["rows"]], ["2026-08-03", "2026-08-04"])
        self.assertEqual(result["rows"][1]["close"], 205.0)
        self.assertEqual(result["rows"][1]["source"], "TWSE STOCK_DAY")

    def test_stock_day_refresh_writes_every_month_row(self) -> None:
        payload = {
            "stat": "OK",
            "data": [
                ["115/08/18", "1,000", "200,000", "200", "205", "198", "203", "+3", "500"],
                ["115/08/19", "2,000", "410,000", "203", "208", "201", "205", "+2", "600"],
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE eod_price(
                    date TEXT, code TEXT, name TEXT, open REAL, high REAL, low REAL,
                    close REAL, volume REAL, amount REAL, change_value REAL,
                    transactions REAL, source TEXT, updated_at REAL,
                    PRIMARY KEY(date, code)
                );
                CREATE TABLE history_price(
                    date TEXT, code TEXT, open REAL, high REAL, low REAL, close REAL,
                    volume REAL, amount REAL, volume_unit TEXT, source TEXT,
                    updated_at REAL, source_quality TEXT, fetched_at REAL, market TEXT,
                    PRIMARY KEY(date, code)
                );
                """
            )
            conn.close()

            def open_db() -> sqlite3.Connection:
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            with (
                patch.object(twse, "request_json", return_value=payload),
                patch.object(twse, "db", side_effect=open_db),
            ):
                result = twse.fetch_twse_stock_day_for_code("2330", target_date="2026-08-19")

            self.assertTrue(result["ok"])
            self.assertEqual(result["history_rows"], 2)
            with closing(sqlite3.connect(db_path)) as check:
                rows = check.execute(
                    "SELECT date,close,source,source_quality FROM history_price ORDER BY date"
                ).fetchall()
            self.assertEqual(
                rows,
                [
                    ("2026-08-18", 203.0, "TWSE STOCK_DAY", "official"),
                    ("2026-08-19", 205.0, "TWSE STOCK_DAY", "official"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
